"""Circuit task (flightsim/envs/circuit.py) and circuit autopilot (flightsim/control/circuit.py)."""

import math
from dataclasses import replace
from pathlib import Path

import pytest

from flightsim.control.circuit import circuit_gains_from_raw, load_circuit_raw
from flightsim.envs import load_env_config, make_env
from flightsim.envs.altitude_heading import controls_to_action
from flightsim.envs.circuit import CircuitEnv
from flightsim.envs.policies import CircuitPolicy

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "configs" / "envs" / "circuit.yaml"
CROSSWIND = ROOT / "configs" / "envs" / "circuit_crosswind.yaml"
AUTOPILOT = ROOT / "configs" / "circuit_autopilot.yaml"
FT = 0.3048


def _fly(config, seed, takeover=None):
    """Fly the circuit autopilot; `takeover(info, controls)` may replace its controls."""
    cfg = load_env_config(config)
    env = make_env(cfg)
    policy = CircuitPolicy(circuit_gains_from_raw(load_circuit_raw(AUTOPILOT)), cfg.control_rate_hz)
    _, info = env.reset(seed=seed)
    policy.reset(info)
    legs = []
    while True:
        u = policy._autopilot(info["state"], info["on_ground"], info["touched_down"], info["nose_wheel_down"])
        if not legs or legs[-1] != policy._autopilot.leg:
            legs.append(policy._autopilot.leg)
        if takeover:
            u = takeover(info, u)
        _, _, terminated, truncated, info = env.step(controls_to_action(u, env.action_names))
        if terminated or truncated:
            return env, info, legs


def test_starts_at_rest_with_a_landing_reference_trim():
    env = make_env(load_env_config(CONFIG))
    assert isinstance(env, CircuitEnv)
    _, info = env.reset(seed=0)
    s = info["state"]
    assert info["on_ground"] and not info["established"] and math.hypot(s.v_north_mps, s.v_east_mps) < 0.05
    assert env.runway_coords()[0] == pytest.approx(10.0, abs=0.1) and s.engine_rpm < 900
    assert info["approach_trim"].flaps == 1.0 and 0.2 < info["approach_trim"].throttle < 0.8
    assert info["approach_trim_state"].v_down_mps > 0  # descending on the glide path


def test_autopilot_flies_a_calm_circuit_and_stops_on_the_runway():
    env, info, legs = _fly(CONFIG, 0)
    assert legs == ["takeoff", "crosswind", "downwind", "base", "final", "approach"]
    landing = info["landing"]
    assert landing["landed"] and landing["rollout"] is not None and info["termination_reason"] is None
    assert env.max_height_m == pytest.approx(1000 * FT - env._parked_cg_m, abs=15)  # pattern altitude (wheels)
    assert 250 < landing["touchdown"]["along_m"] < 450 and abs(landing["touchdown"]["cross_m"]) < 3


def test_autopilot_flies_a_crosswind_circuit():
    env, info, _ = _fly(CROSSWIND, 1000)
    assert abs(env.approach_wind["crosswind_mps"]) > 10 * 1852 / 3600  # 13 kt from the left at 20 ft
    assert info["landing"]["landed"]


def test_descending_before_climbing_out_is_sank_back():
    diving = []

    def dive(info, u):  # from 15 m up: throttle closed, nose down, and it stays that way
        if info["state"].alt_agl_m > 15 or diving:
            diving.append(True)
            return replace(u, throttle=0.0, elevator=0.3)
        return u

    _, info, _ = _fly(CONFIG, 0, dive)
    assert info["termination_reason"] == "sank_back"


def test_stream_and_batch_run_the_circuit(tmp_path):
    from flightsim.batch import run_batch
    from flightsim.config import load_raw
    from flightsim.datalog import write_log
    from flightsim.stream.sources import LiveSource, ReplaySource

    cfg = load_env_config(CONFIG)
    live = LiveSource(cfg, None, 0, policy=CircuitPolicy(circuit_gains_from_raw(load_circuit_raw(AUTOPILOT)), cfg.control_rate_hz))
    assert live.approach["task"] == "circuit" and live.takeoff is None
    list(live.frames())
    assert live.end_reason == "landed" and live.landing["landed"]
    path = write_log(tmp_path / "c.parquet", live._env.episode_result(), live._env.provenance())
    assert ReplaySource(path).approach == live.approach
    _, _, table = run_batch(load_raw(CONFIG), "circuit", load_circuit_raw(AUTOPILOT), [0], tmp_path / "batch")
    row = table.to_pylist()[0]
    assert row["landed"] and row["climbed"] and row["liftoff_ground_roll_m"] > 100
