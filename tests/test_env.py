import math
from pathlib import Path

import gymnasium as gym
import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env

from flightsim.control.autopilot import load_autopilot_gains
from flightsim.datalog import read_log, write_log
from flightsim.envs import ENV_ID, AltitudeHeadingHoldEnv, controls_to_action, load_env_config
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import PIDPolicy, TrimHoldPolicy

ROOT = Path(__file__).parent.parent
ENV_CONFIG = ROOT / "configs" / "envs" / "altitude_heading_hold.yaml"


@pytest.fixture
def cfg():
    return load_env_config(ENV_CONFIG)


@pytest.fixture
def env(cfg):
    return AltitudeHeadingHoldEnv(cfg)


@pytest.fixture
def pid(cfg):
    return PIDPolicy(load_autopilot_gains(ROOT / "configs" / "autopilot.yaml"), cfg.control_rate_hz)


@pytest.mark.filterwarnings("ignore:.*Box observation space.*infinity")
def test_passes_gymnasium_env_checker(env):
    check_env(env, skip_render_check=True)


def test_registered_with_gymnasium():
    env = gym.make(ENV_ID, config_path=str(ENV_CONFIG))
    obs, _ = env.reset(seed=0)
    assert obs.shape == env.observation_space.shape


def _rollout(env, seed, n=200):
    obs, info = env.reset(seed=seed)
    rng = np.random.default_rng(42)
    states = [info["state"]]
    for _ in range(n):
        obs, *_, info = env.step(rng.uniform(-0.3, 0.3, 4).astype(np.float32))
        states.append(info["state"])
    return states


def test_same_seed_is_bit_identical_even_when_env_is_reused(cfg):
    """Regression: a reused JSBSim instance drifted in the last bits between episodes."""
    env = AltitudeHeadingHoldEnv(cfg)
    first = _rollout(env, seed=7)
    _rollout(env, seed=99)
    assert _rollout(env, seed=7) == first
    assert _rollout(AltitudeHeadingHoldEnv(cfg), seed=7) == first


def test_different_seeds_draw_different_tasks(env):
    _, a = env.reset(seed=1)
    _, b = env.reset(seed=2)
    assert a["targets"] != b["targets"]


def test_trim_action_flies_straight_and_level(env):
    _, info = env.reset(seed=3)
    action = controls_to_action(info["trim"])
    for _ in range(20 * 30):
        _, _, terminated, _, info = env.step(action)
    s, s0 = info["state"], info["trim_state"]
    assert not terminated
    assert abs(s.alt_msl_m - s0.alt_msl_m) < 5.0
    assert abs(math.degrees(s.psi_rad - s0.psi_rad)) < 1.0


def test_full_nose_up_terminates_on_alpha(env):
    env.reset(seed=4)
    for _ in range(20 * 60):
        _, reward, terminated, truncated, info = env.step(np.array([-1.0, 0.0, 0.0, -1.0], dtype=np.float32))
        if terminated:
            break
    assert terminated and not truncated
    assert info["termination_reason"] == "alpha"
    assert reward <= -env.cfg.reward.termination_penalty


def test_pid_captures_targets_and_beats_trim_hold(env, pid):
    for seed in range(3):
        m_pid = run_episode(env, pid, seed)
        m_trim = run_episode(env, TrimHoldPolicy(), seed)
        assert m_pid.termination_reason is None
        assert m_pid.alt_final_abs_m < 1.0 and m_pid.heading_final_abs_deg < 0.5
        assert m_pid.alt_settle_s < 60.0 and m_pid.heading_settle_s < 40.0
        assert m_pid.episode_return > m_trim.episode_return


def test_episode_log_round_trip(cfg, pid, tmp_path):
    env = AltitudeHeadingHoldEnv(cfg, record=True)
    run_episode(env, pid, seed=5)
    result = env.episode_result()
    table, meta = read_log(write_log(tmp_path / "ep.parquet", result, env.provenance()))
    assert table.num_rows == cfg.max_decisions * cfg.sim_steps_per_action + 1
    assert table.column("seed")[0].as_py() == 5
    assert meta["flightsim.config_json"] == cfg.config_json


def test_provenance_requires_explicit_seed(cfg):
    env = AltitudeHeadingHoldEnv(cfg, record=True)
    env.reset()
    with pytest.raises(RuntimeError, match="explicit seed"):
        env.provenance()
