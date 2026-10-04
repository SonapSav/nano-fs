import asyncio
import json
from pathlib import Path

import numpy as np
import pytest
from websockets.asyncio.client import connect

from flightsim.control.autopilot import load_autopilot_gains
from flightsim.control.manual import STALE_AFTER_S, HumanPolicy
from flightsim.core import Controls
from flightsim.datalog import read_log
from flightsim.datalog import schema as S
from flightsim.envs import AltitudeHeadingHoldEnv, controls_to_action, load_env_config
from flightsim.stream.server import ServerConfig, run_server
from flightsim.stream.sources import ManualSource

ROOT = Path(__file__).parent.parent


ENV_CONFIG = ROOT / "configs" / "envs" / "altitude_heading_hold.yaml"


@pytest.fixture(scope="module")
def env_cfg():
    return load_env_config(ENV_CONFIG, {"episode_s": 8.0})


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _policy():
    clock = FakeClock()
    p = HumanPolicy(clock)
    trim = Controls(elevator=0.0, aileron=0.04, rudder=-0.01, throttle=0.7, pitch_trim=0.15)
    p.reset({"trim": trim})
    return p, clock, trim


def test_centred_input_flies_the_trim_point():
    p, clock, trim = _policy()
    p.set_input(0.0, 0.0, 0.0, trim.throttle)
    assert np.allclose(p(None, {}), controls_to_action(trim))


def test_input_is_relative_to_trim_and_clipped():
    p, clock, trim = _policy()
    p.set_input(0.3, 5.0, -0.2, 2.0)
    a = p(None, {})
    assert a[0] == pytest.approx(trim.elevator + 0.3)
    assert a[1] == pytest.approx(1.0)  # trim + clipped input, then clipped to the action range
    assert a[2] == pytest.approx(trim.rudder - 0.2)
    assert a[3] == pytest.approx(1.0)  # throttle 1 -> action 1


def test_stale_input_centres_stick_and_holds_throttle():
    p, clock, trim = _policy()
    p.set_input(0.5, 0.5, 0.5, 0.9)
    clock.t = STALE_AFTER_S + 0.01
    a = p(None, {})
    assert a[:3] == pytest.approx(controls_to_action(trim)[:3])
    assert a[3] == pytest.approx(0.8)


def test_rejects_non_finite_input():
    p, _, _ = _policy()
    with pytest.raises(ValueError):
        p.set_input(float("nan"), 0, 0, 0.5)


def _fly(source: ManualSource, inputs: dict[int, tuple]) -> None:
    """Drive the source as the server would, setting inputs at given frame indices."""
    clock = FakeClock()
    source.pilot._clock = clock
    for i, (t, _) in enumerate(source.frames()):
        clock.t = t
        if i in inputs:
            source.pilot.set_input(*inputs[i])


def test_demonstration_refly_reproduces_logged_states_exactly(env_cfg, tmp_path):
    source = ManualSource(env_cfg, seed=11)
    _fly(source, {0: (0.0, -0.2, 0.0, 0.7), 240: (-0.1, 0.0, 0.05, 0.8), 600: (0.0, 0.1, 0.0, 0.75)})
    path = source.save(tmp_path)
    table, meta = read_log(path)
    assert meta[S.META_PILOT] == "human"
    assert meta[S.META_RUN_ID] == path.stem and path.stem.startswith(table.column("run_id")[0].as_py())

    rows = table.to_pylist()
    controls = [Controls(**{f: r[c] for f, c in S.COMMAND_COLUMNS.items()}) for r in rows[:-1]]
    states = AltitudeHeadingHoldEnv(env_cfg).refly(rows[0]["seed"], controls)
    assert [s.alt_msl_m for s in states] == [r["alt_msl_m"] for r in rows]
    assert [s.phi_rad for s in states] == [r["phi_rad"] for r in rows]
    assert [s.tas_mps for s in states] == [r["tas_mps"] for r in rows]


def test_demo_run_id_depends_on_inputs(env_cfg):
    a, b, c = (ManualSource(env_cfg, seed=11) for _ in range(3))
    _fly(a, {0: (0.0, -0.2, 0.0, 0.7)})
    _fly(b, {0: (0.0, -0.2, 0.0, 0.7)})
    _fly(c, {0: (0.0, 0.2, 0.0, 0.7)})
    assert a.demo_run_id() == b.demo_run_id() != c.demo_run_id()


def test_short_flights_are_not_saved(env_cfg, tmp_path):
    source = ManualSource(load_env_config(ENV_CONFIG, {"episode_s": 2.0}), seed=1)
    _fly(source, {})
    assert source.save(tmp_path) is None


def test_server_manual_session_applies_input_and_saves(env_cfg, tmp_path):
    gains = load_autopilot_gains(ROOT / "configs" / "autopilot.yaml")

    async def main():
        ready = asyncio.get_running_loop().create_future()
        server = asyncio.create_task(run_server(ServerConfig(tmp_path, load_env_config(ENV_CONFIG, {"episode_s": 6.0}), gains), "127.0.0.1", 0, ready.set_result))
        port = await ready
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                await ws.send(json.dumps({"type": "play", "source": "manual", "seed": 2, "speed": 50}))
                hello = json.loads(await ws.recv())
                rows, saved, end = [], None, None
                loop = asyncio.get_running_loop()
                t0 = loop.time()
                while end is None or saved is None:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if msg["type"] == "frame":
                        rows.append(msg["row"])
                        await ws.send(json.dumps({"type": "input", "elevator": 0, "aileron": -0.05, "rudder": 0, "throttle": 0.8}))
                    elif msg["type"] == "end":
                        end = msg
                    elif msg["type"] == "saved":
                        saved = msg
                return hello, rows, saved, end, loop.time() - t0
        finally:
            server.cancel()
            with pytest.raises(asyncio.CancelledError):
                await server

    hello, rows, saved, end, elapsed = asyncio.run(main())
    assert hello["source"] == "manual"
    assert elapsed > 5.0  # speed 50 was capped to real time
    assert rows[-2]["cmd_aileron_norm"] == pytest.approx(rows[0]["cmd_aileron_norm"] - 0.05, abs=1e-6)  # input reached the physics
    assert rows[-2]["cmd_throttle_norm"] == pytest.approx(0.8)
    assert rows[-1]["phi_rad"] < -0.1  # rolled left
    assert end["reason"] == "finished"
    assert (tmp_path / saved["path"]).is_file()
