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


def _pilot(env, info, flare_gain=8.0, idle_from_start=False, brake=0.5):
    """A simple test pilot: centreline and glide path tracking, a flare at 6 m, brakes on the ground."""
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
    brake = brake if env.touchdown is not None else 0.0
    u = replace(trim, elevator=max(-1.0, min(1.0, elevator)), aileron=max(-1.0, min(1.0, aileron)), throttle=throttle, brake=brake)
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
    assert isinstance(env, ApproachLandingEnv) and env.observation_space.shape == (21,)  # 16 + the previous 5 actions
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
    live = LiveSource(cfg, None, 0, policy=type("P", (), {"name": "x", "reset": lambda s, i: None, "__call__": lambda s, o, i: np.zeros(len(i["action_names"]), np.float32)})())
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


# --- Crosswind ------------------------------------------------------------------------------

CROSSWIND = ROOT / "configs" / "envs" / "approach_landing_crosswind.yaml"


def test_wind_draws_respect_the_crosswind_and_tailwind_limits():
    env = make_env(load_env_config(CROSSWIND))
    for seed in range(60):
        env.reset(seed=seed)
        w = env.approach_wind
        assert 0 <= w["u20_mps"] <= 20 * KT + 1e-9
        assert abs(w["crosswind_mps"]) <= 15 * KT + 1e-9 and w["headwind_mps"] >= -1e-9  # POH 15 kt; no tailwind
    assert make_env(load_env_config(CONFIG)).reset(seed=0)[1]["state"] is not None  # calm config: no wind
    calm = make_env(load_env_config(CONFIG))
    calm.reset(seed=0)
    assert calm.approach_wind is None


def test_strong_headwind_starts_trimmed_and_crabbed_on_track():
    """Seed 1119 has a ~19 kt headwind at 20 ft (~35 kt at the start height), where JSBSim's
    trim in wind fails: the start is trimmed in calm air and the wind added."""
    env = make_env(load_env_config(CROSSWIND, {"approach.wind.turbulence": False}))  # steadiness, so no gusts
    _, info = env.reset(seed=1119)
    s = info["state"]
    assert env.approach_wind["headwind_mps"] > 15 * KT
    states = [env.step(controls_to_action(info["trim"], env.action_names))[4]["state"] for _ in range(40)]  # 2 s
    assert max(abs(x.tas_mps - s.tas_mps) for x in states) < 0.5  # steady relative to the air
    ground_speed = math.hypot(s.v_north_mps, s.v_east_mps)
    assert -s.v_down_mps == pytest.approx(-ground_speed * math.tan(math.radians(3)), rel=0.05)  # 3 deg over the ground


def _fly_autopilot(seed, **flare):
    from flightsim.config import load_raw
    from flightsim.control.approach import approach_gains_from_raw
    from flightsim.envs.evaluate import run_episode
    from flightsim.envs.policies import ApproachPolicy

    raw = load_raw(ROOT / "configs" / "approach_autopilot.yaml")
    raw["flare"].update(flare)
    cfg = load_env_config(CROSSWIND)
    env = make_env(cfg)
    run_episode(env, ApproachPolicy(approach_gains_from_raw(raw), cfg.control_rate_hz), seed)
    return env


def test_approach_autopilot_lands_in_a_crosswind():
    env = _fly_autopilot(1009)  # ~10 kt crosswind from the right at 20 ft, with turbulence
    w, summary = env.approach_wind, env.landing_summary()
    assert abs(w["crosswind_mps"]) > 8 * KT
    assert summary["landed"] and abs(summary["touchdown"]["drift_deg"]) < 5


def test_landing_still_crabbed_is_a_side_load():
    """The same crosswind landing without the de-crab touches down crabbed: a side load."""
    env = _fly_autopilot(1009, decrab_height_m=0.0)
    assert env.landing_summary()["failure"] == "side_load"
    assert abs(env.landing_summary()["touchdown"]["drift_deg"]) > 5


# --- Rollout to a stop ----------------------------------------------------------------------


def test_landing_ends_stopped_on_the_runway():
    _, terminated, truncated, info = _fly(_env(), 0)
    ro = info["landing"]["rollout"]
    assert truncated and not terminated and ro is not None
    assert info["state"].v_north_mps ** 2 + info["state"].v_east_mps ** 2 < (2 * KT) ** 2
    assert 0 < ro["stop_along_m"] < 1000 and ro["ground_roll_m"] > 50 and abs(ro["stop_cross_m"]) < 15


def test_no_brakes_runs_off_the_end():
    _, terminated, _, info = _fly(_env(), 0, brake=0.0)
    assert terminated and info["termination_reason"] in ("overrun", "no_stop")


def test_without_a_rollout_block_landing_ends_once_settled():
    _, terminated, truncated, info = _fly(_env(**{"approach.rollout": None}), 0, brake=0.0)
    assert truncated and not terminated and info["landing"]["landed"] and info["landing"]["rollout"] is None


def test_approach_autopilot_brakes_to_a_stop_near_the_centreline():
    from flightsim.control.approach import load_approach_gains
    from flightsim.envs.evaluate import run_episode
    from flightsim.envs.policies import ApproachPolicy

    cfg = load_env_config(CONFIG)
    env = make_env(cfg)
    run_episode(env, ApproachPolicy(load_approach_gains(ROOT / "configs" / "approach_autopilot.yaml"), cfg.control_rate_hz), 0)
    s = env.landing_summary()
    assert s["landed"] and s["rollout"]["max_cross_m"] < 2.0
    assert 150 < s["rollout"]["ground_roll_m"] < 350  # half brakes from ~53 KCAS (calm seeds: 250-271 m)
