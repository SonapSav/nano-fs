import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from flightsim.control.autopilot import load_autopilot_gains
from flightsim.datalog import make_run_id, write_log
from flightsim.datalog import schema as S
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import PIDPolicy
from flightsim.stream.protocol import encode
from flightsim.stream.server import ServerConfig, run_server
from flightsim.stream.sources import LiveSource, ReplaySource

ROOT = Path(__file__).parent.parent
ENV_CONFIG = ROOT / "configs" / "envs" / "altitude_heading_hold.yaml"


@pytest.fixture(scope="module")
def env_cfg():
    return load_env_config(ENV_CONFIG, {"episode_s": 6.0})  # short episodes keep tests fast


@pytest.fixture(scope="module")
def gains():
    return load_autopilot_gains(ROOT / "configs" / "autopilot.yaml")


@pytest.fixture(scope="module")
def logged_episode(tmp_path_factory, env_cfg, gains):
    """The same PID episode the live source flies, written as a log."""
    data_dir = tmp_path_factory.mktemp("data")
    env = AltitudeHeadingHoldEnv(env_cfg, record=True)
    run_episode(env, PIDPolicy(gains, env_cfg.control_rate_hz), seed=3)
    path = data_dir / "eps" / f"{make_run_id(env_cfg.config_hash, 3)}.parquet"
    write_log(path, env.episode_result(), env.provenance())
    return data_dir, path


def test_live_frames_equal_logged_rows(env_cfg, gains, logged_episode):
    """One format for live and replay: a live frame is exactly the log row."""
    _, path = logged_episode
    live = [row for _, row in LiveSource(env_cfg, gains, seed=3).frames()]
    replay = [row for _, row in ReplaySource(path).frames()]
    assert len(live) == len(replay)
    assert live == replay
    assert list(live[0]) == S.SCHEMA.names


def test_encode_nulls_non_finite_values():
    msg = json.loads(encode({"type": "frame", "row": {"a": float("nan"), "b": 1.5, "c": None}}))
    assert msg["row"] == {"a": None, "b": 1.5, "c": None}


def _with_server(data_dir, env_cfg, gains, body):
    async def main():
        cfg = ServerConfig(data_dir, env_cfg, gains)
        ready = asyncio.get_running_loop().create_future()
        server = asyncio.create_task(run_server(cfg, "127.0.0.1", 0, ready.set_result))
        port = await asyncio.wait_for(ready, 5)
        try:
            return await asyncio.wait_for(body(port), 30)
        finally:
            server.cancel()
            with pytest.raises(asyncio.CancelledError):
                await server

    return asyncio.run(main())


async def _collect(ws):
    hello = json.loads(await ws.recv())
    frames = []
    while True:
        msg = json.loads(await ws.recv())
        if msg["type"] == "end":
            return hello, frames, msg
        frames.append(msg["row"])


def test_server_streams_replay_and_live(logged_episode, env_cfg, gains):
    data_dir, path = logged_episode

    async def body(port):
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await ws.send(json.dumps({"type": "list"}))
            logs = json.loads(await ws.recv())["logs"]
            await ws.send(json.dumps({"type": "play", "source": "replay", "path": logs[0]["path"], "speed": 64}))
            replay = await _collect(ws)
            await ws.send(json.dumps({"type": "play", "source": "live", "seed": 3, "speed": 64}))
            live = await _collect(ws)
            return logs, replay, live

    logs, (r_hello, r_frames, r_end), (l_hello, l_frames, l_end) = _with_server(data_dir, env_cfg, gains, body)
    assert [log["path"] for log in logs] == [path.relative_to(data_dir).as_posix()]
    assert r_hello["source"] == "replay" and l_hello["source"] == "live"
    assert r_hello["run_id"] == l_hello["run_id"]
    assert l_hello["targets"] is not None
    # 30 Hz frames out of 120 Hz rows; the final state is always included.
    assert len(r_frames) == len(l_frames) == 6 * 30 + 1
    assert r_frames == l_frames
    assert r_frames[-1]["t_s"] == pytest.approx(6.0)
    assert r_frames[-1]["cmd_elevator_norm"] is None
    assert r_end["reason"] == l_end["reason"] == "finished"


def test_server_paces_to_wall_clock(logged_episode, env_cfg, gains):
    data_dir, _ = logged_episode

    async def body(port):
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await ws.send(json.dumps({"type": "play", "source": "live", "seed": 3, "speed": 4}))
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            await _collect(ws)
            return loop.time() - t0

    elapsed = _with_server(data_dir, env_cfg, gains, body)
    assert elapsed == pytest.approx(6.0 / 4, abs=0.4)


def test_pause_holds_the_stream(logged_episode, env_cfg, gains):
    data_dir, _ = logged_episode

    async def body(port):
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await ws.send(json.dumps({"type": "play", "source": "live", "seed": 3, "speed": 1}))
            await ws.recv()  # hello
            await ws.send(json.dumps({"type": "pause"}))
            await asyncio.sleep(0.2)  # let frames already in flight arrive
            while True:
                try:
                    await asyncio.wait_for(ws.recv(), 0.05)
                except TimeoutError:
                    break
            try:
                await asyncio.wait_for(ws.recv(), 0.5)
                return False
            except TimeoutError:
                return True

    assert _with_server(data_dir, env_cfg, gains, body)


def test_server_rejects_paths_outside_data_and_viewer_dirs(logged_episode, env_cfg, gains):
    data_dir, _ = logged_episode

    async def body(port):
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await ws.send(json.dumps({"type": "play", "source": "replay", "path": "../../etc/passwd"}))
            ws_reply = json.loads(await ws.recv())

        def status(path):
            try:
                return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code

        paths = ("/", "/app.js", "/../pyproject.toml", "/%2e%2e/pyproject.toml")
        return ws_reply, await asyncio.to_thread(lambda: tuple(status(p) for p in paths))

    ws_reply, codes = _with_server(data_dir, env_cfg, gains, body)
    assert ws_reply["type"] == "error"
    assert codes == (200, 200, 404, 404)


def test_viewer_is_served_with_vendored_three(logged_episode, env_cfg, gains):
    data_dir, _ = logged_episode

    async def body(port):
        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                return r.headers["Content-Type"], r.read()

        return await asyncio.to_thread(lambda: [get(p) for p in ("/", "/vendor/three.module.js", "/vendor/three.core.js")])

    (html_type, html), (js_type, js), (_, core) = _with_server(data_dir, env_cfg, gains, body)
    assert html_type.startswith("text/html") and b'"three": "./vendor/three.module.js"' in html
    assert js_type == "text/javascript" and b"from './three.core.js'" in js
    assert b"REVISION = '186'" in core
