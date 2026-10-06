"""Takeoff task (flightsim/envs/takeoff.py) and takeoff autopilot (flightsim/control/takeoff.py)."""

import math
from dataclasses import replace
from pathlib import Path

import pytest

from flightsim.control.takeoff import load_takeoff_gains
from flightsim.envs import load_env_config, make_env
from flightsim.envs.altitude_heading import controls_to_action
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import TakeoffPolicy
from flightsim.envs.takeoff import TakeoffEnv

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "configs" / "envs" / "takeoff.yaml"
CROSSWIND = ROOT / "configs" / "envs" / "takeoff_crosswind.yaml"
GAINS = ROOT / "configs" / "takeoff_autopilot.yaml"
KT = 1852 / 3600


def _autopilot_episode(config, seed):
    cfg = load_env_config(config)
    env = make_env(cfg)
    run_episode(env, TakeoffPolicy(load_takeoff_gains(GAINS), cfg.control_rate_hz), seed)
    return env


def _fly(env, seed, pilot):
    _, info = env.reset(seed=seed)
    while True:
        _, _, terminated, truncated, info = env.step(controls_to_action(pilot(info), env.action_names))
        if terminated or truncated:
            return terminated, info


def test_starts_at_rest_on_the_runway_engine_idling():
    env = make_env(load_env_config(CONFIG))
    assert isinstance(env, TakeoffEnv)
    _, info = env.reset(seed=3)
    s = info["state"]
    along, cross = env.runway_coords()
    assert along == pytest.approx(10.0, abs=0.1) and abs(cross) <= 2.0 + 1e-6
    assert math.hypot(s.v_north_mps, s.v_east_mps) < 0.05 and s.engine_rpm < 900
    assert env.height_m() == pytest.approx(0.0, abs=1e-9) and info["on_ground"]
    other = make_env(load_env_config(CONFIG))
    assert other.reset(seed=3)[1]["state"] == s  # same seed, same start


def test_autopilot_takes_off_and_climbs_out():
    env = _autopilot_episode(CONFIG, 0)
    t = env.takeoff_summary()
    assert t["climbed"] and t["failure"] is None and t["skips"] == 0
    assert 150 < t["liftoff"]["ground_roll_m"] < 400 and 50 < t["liftoff"]["cas_mps"] / KT < 65  # rotate at 55 KIAS
    assert t["fifty_ft"]["distance_m"] < 800 and t["ground_max_cross_m"] < 3.0


def test_crosswind_draws_and_a_strong_crosswind_takeoff():
    env = make_env(load_env_config(CROSSWIND))
    for seed in range(40):
        env.reset(seed=seed)
        w = env.takeoff_wind
        assert abs(w["crosswind_mps"]) <= 15 * KT + 1e-9 and w["headwind_mps"] >= -1e-9
    env = _autopilot_episode(CROSSWIND, 1050)  # 15 kt crosswind from the left at 20 ft
    assert abs(env.takeoff_wind["crosswind_mps"]) > 14 * KT
    assert env.takeoff_summary()["climbed"]


def test_idle_power_never_lifts_off():
    env = make_env(load_env_config(CONFIG, {"takeoff.limits.max_ground_s": 15}))
    terminated, info = _fly(env, 0, lambda info: info["trim"])
    assert terminated and info["termination_reason"] == "no_liftoff"


def test_full_back_stick_from_the_start_strikes_the_tail():
    env = make_env(load_env_config(CONFIG))
    terminated, info = _fly(env, 0, lambda info: replace(info["trim"], throttle=1.0, elevator=-1.0))
    assert terminated and info["termination_reason"] == "tail_strike"


def test_full_rudder_leaves_the_runway():
    env = make_env(load_env_config(CONFIG))
    terminated, info = _fly(env, 0, lambda info: replace(info["trim"], throttle=1.0, rudder=1.0))
    assert terminated and info["termination_reason"] == "off_runway"


def test_stream_reports_the_takeoff_live_and_in_replays(tmp_path):
    from flightsim.datalog import write_log
    from flightsim.stream.sources import LiveSource, ReplaySource

    cfg = load_env_config(CROSSWIND)
    live = LiveSource(cfg, None, 1050, policy=TakeoffPolicy(load_takeoff_gains(GAINS), cfg.control_rate_hz))
    assert live.takeoff["task"] == "takeoff" and live.takeoff["wind"]["u20_mps"] > 0 and live.approach is None
    list(live.frames())
    assert live.end_reason == "climbed" and live.takeoff_result["climbed"]
    path = write_log(tmp_path / "t.parquet", live._env.episode_result(), live._env.provenance())
    assert ReplaySource(path).takeoff == live.takeoff


def test_batch_runs_the_takeoff_autopilot(tmp_path):
    from flightsim.batch import run_batch
    from flightsim.config import load_raw

    _, _, table = run_batch(load_raw(CONFIG), "takeoff", load_raw(GAINS), [0, 1], tmp_path)
    rows = table.to_pylist()
    assert all(r["climbed"] for r in rows) and all(r["liftoff_ground_roll_m"] > 100 for r in rows)
    assert rows[0]["landed"] is None  # approach columns stay null
