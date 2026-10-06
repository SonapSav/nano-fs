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


def _with_server(data_dir, env_cfg, gains, body, **extra):
    async def main():
        cfg = ServerConfig(data_dir, env_cfg, gains, **extra)
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


def test_every_play_in_a_session_is_paced(logged_episode, env_cfg, gains):
    """Regression: a second play on the same connection used to stream unpaced."""
    data_dir, _ = logged_episode

    async def body(port):
        loop = asyncio.get_running_loop()
        walls = []
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            for _ in range(2):
                await ws.send(json.dumps({"type": "play", "source": "live", "seed": 3, "speed": 4}))
                t0 = loop.time()
                await _collect(ws)
                walls.append(loop.time() - t0)
        return walls

    for wall in _with_server(data_dir, env_cfg, gains, body):
        assert wall == pytest.approx(6.0 / 4, abs=0.4)


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

        paths = ("/", "/vendor/three.module.js", "/vendor/three.core.js", "/vendor/addons/objects/Sky.js")
        return await asyncio.to_thread(lambda: [get(p) for p in paths])

    (html_type, html), (js_type, js), (_, core), (sky_type, sky) = _with_server(data_dir, env_cfg, gains, body)
    assert html_type.startswith("text/html") and b'"three": "./vendor/three.module.js"' in html
    assert b'"three/addons/": "./vendor/addons/"' in html
    assert sky_type == "text/javascript" and b"class Sky extends Mesh" in sky
    assert js_type == "text/javascript" and b"from './three.core.js'" in js
    assert b"REVISION = '186'" in core


def test_viewer_works_offline_with_the_vendored_font(logged_episode, env_cfg, gains):
    """No external resources: the font is served locally, with its licence."""
    import re

    data_dir, _ = logged_episode

    async def body(port):
        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                return r.headers["Content-Type"], r.read()

        def fetch_all():
            html = get("/")[1].decode()
            css = get("/vendor/fonts/barlow-condensed.css")[1].decode()
            fonts = [get("/vendor/fonts/" + name) for name in re.findall(r"url\(\./(.*?)\)", css)]
            return html, css, fonts

        return await asyncio.to_thread(fetch_all)

    html, css, fonts = _with_server(data_dir, env_cfg, gains, body)
    assert not re.search(r"(src|href)=\"https?://", html)
    assert len(fonts) == 3 and all(ctype == "font/woff2" and body[:4] == b"wOF2" for ctype, body in fonts)
    assert (ROOT / "flightsim" / "viewer" / "vendor" / "fonts" / "OFL.txt").is_file()


def test_server_streams_the_lqr_autopilot(env_cfg, gains, tmp_path):
    """The LQR flies the same episode live as in a batch run; the hello says who flies."""
    from flightsim.config import load_raw
    from flightsim.envs.policies import LQRPolicy

    lqr_raw = load_raw(ROOT / "configs" / "lqr.yaml")
    env = AltitudeHeadingHoldEnv(env_cfg, record=True)
    run_episode(env, LQRPolicy.designed(env_cfg, lqr_raw), seed=3)
    states, _ = env.recorded

    async def body(port):
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await ws.send(json.dumps({"type": "play", "source": "live", "autopilot": "lqr", "seed": 3, "speed": 64}))
            lqr = await _collect(ws)
            await ws.send(json.dumps({"type": "play", "source": "live", "seed": 3, "speed": 64}))
            pid = await _collect(ws)
            await ws.send(json.dumps({"type": "play", "source": "live", "autopilot": "rl", "seed": 3}))
            error = json.loads(await ws.recv())
            return lqr, pid, error

    (hello, frames, end), (pid_hello, pid_frames, _), error = _with_server(tmp_path, env_cfg, gains, body, lqr_raw=lqr_raw)
    assert hello["source"] == "live" and hello["pilot"] == "lqr" and pid_hello["pilot"] == "pid"
    assert end["reason"] == "finished"
    assert frames[-1]["alt_msl_m"] == states[-1].alt_msl_m  # same flight as the batch path
    assert frames[-1]["alt_msl_m"] != pid_frames[-1]["alt_msl_m"]
    assert error["type"] == "error" and "unknown autopilot 'rl'" in error["message"]


def test_replay_seeking(logged_episode, env_cfg, gains):
    """Seek while playing, seek while paused (one frame, stays paused), start part-way,
    and no seeking in live flights."""
    data_dir, path = logged_episode
    rel = path.relative_to(data_dir).as_posix()

    async def next_frame(ws, timeout=2.0):
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if msg["type"] in ("frame", "end"):
                return msg

    async def body(port):
        out = {}
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            # 1. While playing: jump back from ~3 s to 0.5 s.
            await ws.send(json.dumps({"type": "play", "source": "replay", "path": rel, "speed": 4}))
            await ws.recv()  # hello
            while (await next_frame(ws))["row"]["t_s"] < 3.0:
                pass
            await ws.send(json.dumps({"type": "seek", "t_s": 0.5}))
            times = []
            while True:
                msg = await next_frame(ws)
                if msg["type"] == "end":
                    break
                times.append(msg["row"]["t_s"])
            out["after_seek"] = times

            # 2. While paused: one frame at the new position, then nothing until resume.
            await ws.send(json.dumps({"type": "play", "source": "replay", "path": rel, "speed": 1}))
            await ws.recv()  # hello
            await next_frame(ws)
            await ws.send(json.dumps({"type": "pause"}))
            await asyncio.sleep(0.2)
            while True:  # drain frames sent before the pause
                try:
                    await asyncio.wait_for(ws.recv(), 0.05)
                except TimeoutError:
                    break
            await ws.send(json.dumps({"type": "seek", "t_s": 4.0}))
            out["paused_frame"] = (await next_frame(ws))["row"]["t_s"]
            try:
                await asyncio.wait_for(ws.recv(), 0.5)
                out["stayed_paused"] = False
            except TimeoutError:
                out["stayed_paused"] = True
            await ws.send(json.dumps({"type": "speed", "value": 64}))
            await ws.send(json.dumps({"type": "resume"}))
            out["after_resume"] = (await next_frame(ws))["row"]["t_s"]
            while (await next_frame(ws))["type"] != "end":
                pass

            # 3. Start part-way.
            await ws.send(json.dumps({"type": "play", "source": "replay", "path": rel, "speed": 64, "start_s": 5.0}))
            await ws.recv()  # hello
            out["start"] = (await next_frame(ws))["row"]["t_s"]
            while (await next_frame(ws))["type"] != "end":
                pass

            # 4. Live flights cannot seek.
            await ws.send(json.dumps({"type": "play", "source": "live", "seed": 3, "speed": 1}))
            await ws.recv()  # hello
            await ws.send(json.dumps({"type": "seek", "t_s": 1.0}))
            while (msg := json.loads(await ws.recv()))["type"] != "error":
                pass
            out["live_error"] = msg["message"]
        return out

    out = _with_server(data_dir, env_cfg, gains, body)
    t = out["after_seek"]
    jumps = [i for i in range(1, len(t)) if t[i] < t[i - 1]]  # frames sent before the seek arrive first
    assert len(jumps) == 1 and t[jumps[0]] == pytest.approx(0.5, abs=1 / 120 + 1e-9)
    after = t[jumps[0]:]
    assert all(b > a for a, b in zip(after, after[1:])) and after[-1] == pytest.approx(6.0)
    assert out["paused_frame"] == pytest.approx(4.0, abs=1 / 120 + 1e-9) and out["stayed_paused"]
    assert 4.0 < out["after_resume"] < 4.2
    assert out["start"] == pytest.approx(5.0, abs=1 / 120 + 1e-9)
    assert "only possible during a replay" in out["live_error"]


def test_replays_carry_the_logged_episode_targets(tmp_path):
    """Logs do not store the altitude/heading targets; replays rebuild them from the logged
    config and seed, so the viewer shows the same bugs as during the live flight."""
    from flightsim.control.autopilot import load_autopilot_gains
    from flightsim.datalog import write_log
    from flightsim.envs import load_env_config
    from flightsim.stream.sources import LiveSource, ReplaySource

    root = Path(__file__).parent.parent
    cfg = load_env_config(root / "configs" / "envs" / "altitude_heading_hold.yaml", {"episode_s": 2.0})
    live = LiveSource(cfg, load_autopilot_gains(root / "configs" / "autopilot.yaml"), 7)
    list(live.frames())
    path = write_log(tmp_path / "r.parquet", live._env.episode_result(), live._env.provenance())
    replay = ReplaySource(path)
    assert replay.targets == live.targets and replay.approach is None and replay.takeoff is None
