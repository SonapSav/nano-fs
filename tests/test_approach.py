"""Approach and landing task (flightsim/envs/approach.py)."""

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from flightsim.envs import load_env_config, make_env
from flightsim.envs.altitude_heading import controls_to_action
from flightsim.envs.approach import ApproachLandingEnv

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "configs" / "envs" / "approach_landing.yaml"
KT = 1852 / 3600


def _env(**overrides):
    return make_env(load_env_config(CONFIG, overrides))


def _pilot(env, info, flare_gain=8.0, idle_from_start=False):
    """A simple test pilot: centreline and glide path tracking, then a flare at 6 m."""
    trim, s = info["trim"], info["state"]
    _, cross = env.runway_coords(s)
    hdg_err = math.atan2(math.sin(env.cfg.approach.runway_heading_rad - s.psi_rad), math.cos(env.cfg.approach.runway_heading_rad - s.psi_rad))
    if s.alt_agl_m > 6.0 and env.touchdown is None:
        bank = max(-0.35, min(0.35, 2.0 * (hdg_err + max(-0.35, min(0.35, -cross / 150.0)))))
        elevator = -2.0 * (math.radians(1.0) - 0.02 * env.glide_path_deviation_m(s) - s.theta_rad) + 0.5 * s.q_radps
        throttle = 0.0 if idle_from_start else max(0.0, min(1.0, trim.throttle + 0.08 * (env.cfg.approach.target_cas_mps - s.cas_mps)))
    else:
        bank, throttle = 0.0, 0.0
        elevator = -flare_gain * (math.radians(6.0) - s.theta_rad) + 1.0 * s.q_radps
    aileron = trim.aileron + 1.5 * (bank - s.phi_rad) - 0.3 * s.p_radps
    u = replace(trim, elevator=max(-1.0, min(1.0, elevator)), aileron=max(-1.0, min(1.0, aileron)), throttle=throttle)
    return controls_to_action(u, env.action_names)


def _fly(env, seed, **pilot):
    _, info = env.reset(seed=seed)
    total = 0.0
    while True:
        _, r, terminated, truncated, info = env.step(_pilot(env, info, **pilot))
        total += r
        if terminated or truncated:
            return total, terminated, truncated, info


def test_config_selects_the_approach_task():
    env = _env()
    assert isinstance(env, ApproachLandingEnv) and env.observation_space.shape == (20,)
    assert env.cfg.terrain == "procedural"


def test_starts_on_the_glide_path_and_extended_centreline():
    env = _env()
    _, info = env.reset(seed=3)
    along, cross = env.runway_coords()
    a = env.cfg.approach
    assert along == pytest.approx(a.aim_point_m - a.start_distance_m, abs=0.5)
    assert cross == pytest.approx(env.start_offsets["lateral_m"], abs=0.5)
    assert env.glide_path_deviation_m() == pytest.approx(env.start_offsets["vertical_m"], abs=0.5)
    s = info["state"]
    assert s.cas_mps / KT == pytest.approx(env.start_offsets["kias"], abs=0.5)
    assert -s.v_down_mps / s.tas_mps == pytest.approx(-math.sin(a.glide_path_rad), abs=0.005)  # trimmed 3 deg descent
    assert info["trim"].flaps == pytest.approx(1.0)


def test_same_seed_same_approach():
    a, b = _env(), _env()
    _, ia = a.reset(seed=5)
    _, ib = b.reset(seed=5)
    assert ia["state"] == ib["state"] and a.start_offsets == b.start_offsets


def test_a_flared_approach_lands():
    total, terminated, truncated, info = _fly(_env(), 0)
    landing = info["landing"]
    assert truncated and not terminated and landing["landed"]
    td = landing["touchdown"]
    assert not td["nose_first"] and td["sink_mps"] < 600 * 0.3048 / 60 and abs(td["cross_m"]) < 5
    assert total > -1000  # the landing bonus outweighs the tracking and touchdown costs


def test_no_flare_lands_nose_first_and_fails():
    _, terminated, _, info = _fly(_env(), 0, flare_gain=0.5)
    assert terminated and info["termination_reason"] == "nose_first"


def test_idle_all_the_way_ends_in_a_stall_or_short():
    """Holding the glide path with pitch at idle power bleeds off speed until the wing stalls."""
    _, terminated, _, info = _fly(_env(), 0, idle_from_start=True)
    assert terminated and info["termination_reason"] in ("alpha", "undershoot")


def test_hands_off_drifts_and_loses_the_approach():
    env = _env()
    _, info = env.reset(seed=0)
    a = controls_to_action(info["trim"], env.action_names)
    while True:
        _, _, terminated, truncated, info = env.step(a)
        if terminated or truncated:
            break
    assert terminated and info["termination_reason"] == "lost_approach"


def test_recorded_approach_refly_reproduces_the_log():
    env = make_env(load_env_config(CONFIG, {"episode_s": 20.0}), record=True)
    _, info = env.reset(seed=2)
    while True:
        _, _, terminated, truncated, info = env.step(_pilot(env, info))
        if terminated or truncated:
            break
    states, controls = env.recorded
    again = make_env(load_env_config(CONFIG, {"episode_s": 20.0}))
    assert again.refly(2, controls) == states


def test_live_stream_reports_the_landing():
    """The viewer's live source ends with "landed" and the touchdown details."""
    from flightsim.stream.sources import LiveSource

    class TestPilot:
        name = "test"

        def __init__(self):
            self.env = None

        def reset(self, info):
            pass

        def __call__(self, obs, info):
            return _pilot(self.env, info)

    pilot = TestPilot()
    source = LiveSource(load_env_config(CONFIG), None, 0, policy=pilot)
    pilot.env = source._env
    rows = [row for _, row in source.frames()]
    assert source.end_reason == "landed" and source.landing["landed"]
    assert source.landing["touchdown"]["along_m"] > 0 and rows[-1]["alt_agl_m"] < 2.0


def test_approach_geometry_reaches_the_viewer_live_and_in_replays(tmp_path):
    """The stream's hello carries the runway and glide path, for live flights and replays."""
    from flightsim.datalog import write_log
    from flightsim.stream.sources import LiveSource, ReplaySource

    cfg = load_env_config(CONFIG, {"episode_s": 2.0})
    live = LiveSource(cfg, None, 0, policy=type("P", (), {"name": "x", "reset": lambda s, i: None, "__call__": lambda s, o, i: np.zeros(4, np.float32)})())
    assert live.approach["threshold_east_m"] == -500.0 and live.approach["glide_path_deg"] == pytest.approx(3.0)
    assert live.approach["aim_point_m"] == 250.0 and live.approach["elevation_m"] == pytest.approx(0.0, abs=1e-9)
    list(live.frames())
    path = write_log(tmp_path / "a.parquet", live._env.episode_result(), live._env.provenance())
    assert ReplaySource(path).approach == live.approach
    cruise = load_env_config(ROOT / "configs" / "envs" / "altitude_heading_hold.yaml", {"episode_s": 1.0})
    assert LiveSource(cruise, None, 0, policy=live._policy).approach is None


def test_approach_autopilot_lands_and_the_viewer_can_watch_it(tmp_path):
    """The approach autopilot lands main wheels first in the touchdown zone, and the
    stream server plays it (autopilot "approach") to a "landed" end."""
    import asyncio
    import json

    from websockets.asyncio.client import connect

    from flightsim.control.approach import load_approach_gains
    from flightsim.control.autopilot import load_autopilot_gains
    from flightsim.envs.evaluate import run_episode
    from flightsim.envs.policies import ApproachPolicy
    from flightsim.stream.server import ServerConfig, run_server

    gains = load_approach_gains(ROOT / "configs" / "approach_autopilot.yaml")
    cfg = load_env_config(CONFIG)
    for seed in (0, 7):
        env = make_env(cfg)
        m = run_episode(env, ApproachPolicy(gains, cfg.control_rate_hz), seed)
        summary = env.landing_summary()
        td = summary["touchdown"]
        assert summary["landed"] and m.termination_reason is None
        assert 100 <= td["along_m"] <= 400 and td["sink_mps"] < 2.0 and td["pitch_deg"] > 3.0 and abs(td["cross_m"]) < 3

    cruise = load_env_config(ROOT / "configs" / "envs" / "altitude_heading_hold.yaml", {"episode_s": 2.0})

    async def main():
        ready = asyncio.get_running_loop().create_future()
        server_cfg = ServerConfig(tmp_path, cruise, load_autopilot_gains(ROOT / "configs" / "autopilot.yaml"), approach_env_cfg=cfg, approach_gains=gains)
        server = asyncio.create_task(run_server(server_cfg, "127.0.0.1", 0, ready.set_result))
        port = await ready
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws", max_size=None) as ws:
                await ws.send(json.dumps({"type": "play", "source": "live", "autopilot": "approach", "seed": 0, "speed": 64}))
                hello = json.loads(await ws.recv())
                while (msg := json.loads(await asyncio.wait_for(ws.recv(), 30)))["type"] != "end":
                    pass
                return hello, msg
        finally:
            server.cancel()
            with pytest.raises(asyncio.CancelledError):
                await server

    hello, end = asyncio.run(main())
    assert hello["pilot"] == "approach" and hello["approach"]["threshold_east_m"] == -500.0
    assert end["reason"] == "landed" and end["landing"]["landed"]
