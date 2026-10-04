import math
from pathlib import Path

import numpy as np
import pytest

from flightsim.config import load_raw
from flightsim.envs import AltitudeHeadingHoldEnv, controls_to_action, load_env_config
from flightsim.envs.altitude_heading import load_factor

ROOT = Path(__file__).parent.parent
ENVS = ROOT / "configs" / "envs"
COMFORT = ENVS / "altitude_heading_hold_comfort.yaml"


def test_comfort_task_extends_windy_task_and_old_tasks_are_unchanged():
    windy, comfort = load_raw(ENVS / "altitude_heading_hold_wind.yaml"), load_raw(COMFORT)
    assert comfort["wind"] == windy["wind"]
    assert comfort["termination"]["max_bank_deg"] == 60  # C172P POH normal category
    assert (comfort["termination"]["max_load_factor"], comfort["termination"]["min_load_factor"]) == (3.8, -1.52)
    for name in ("altitude_heading_hold.yaml", "altitude_heading_hold_wind.yaml"):
        cfg = load_env_config(ENVS / name)
        assert cfg.reward.comfort is None and not cfg.reward.charge_remaining_steps
        assert cfg.termination.max_load_factor is None


def test_no_comfort_cost_in_level_flight():
    env = AltitudeHeadingHoldEnv(load_env_config(COMFORT, {"wind.steady_speed_mps": [0, 0], "wind.turbulence.probability": {"none": 1.0, "light": 0.0, "moderate": 0.0}}))
    _, info = env.reset(seed=1)
    action = controls_to_action(info["trim"])
    for _ in range(40):
        _, _, _, _, info = env.step(action)
        assert info["comfort_cost"] == 0.0


def test_steep_bank_costs_comfort_but_is_not_terminal():
    env = AltitudeHeadingHoldEnv(load_env_config(COMFORT, {"wind.turbulence.probability": {"none": 1.0, "light": 0.0, "moderate": 0.0}}))
    _, info = env.reset(seed=2)
    action = controls_to_action(info["trim"])
    action[1] += 0.15  # roll right
    costs = []
    for _ in range(20 * 10):
        _, _, terminated, _, info = env.step(action)
        costs.append(info["comfort_cost"])
        if abs(info["state"].phi_rad) > math.radians(40):
            break
    assert not terminated
    assert costs[0] == 0.0 and costs[-1] > 0.0


def test_load_factor_limit_terminates():
    env = AltitudeHeadingHoldEnv(load_env_config(COMFORT, {"termination.max_load_factor": 1.3}))
    _, info = env.reset(seed=3)
    action = controls_to_action(info["trim"])
    action[0] = -0.6  # pull
    for _ in range(20 * 10):
        _, _, terminated, _, info = env.step(action)
        if terminated:
            break
    assert info["termination_reason"] == "load_factor"
    assert load_factor(info["state"]) > 1.3


def test_max_step_cost_bounds_every_step_and_terminating_never_pays():
    env = AltitudeHeadingHoldEnv(load_env_config(COMFORT))
    assert env.max_step_cost() == pytest.approx(22.4)  # (1 + 1 + 0.2) * 4 + 0.1 * 16 + 3 * 4
    _, info = env.reset(seed=4)
    rng = np.random.default_rng(0)
    total = 0.0
    for _ in range(env.cfg.max_decisions):
        _, reward, terminated, truncated, info = env.step(rng.uniform(-1, 1, 4).astype(np.float32))
        if not terminated:
            assert -reward <= env.max_step_cost()
        total += reward
        if terminated or truncated:
            break
    assert terminated  # random full-range actions do not fly for long
    # Charged for the remaining steps: worse than flying the whole episode at the worst cost.
    assert total <= -env.max_step_cost() * (env.cfg.max_decisions - env._decisions) - env.cfg.reward.termination_penalty
