import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("stable_baselines3")

from flightsim.batch import run_batch  # noqa: E402
from flightsim.config import load_raw  # noqa: E402
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config  # noqa: E402
from flightsim.envs.evaluate import run_episode  # noqa: E402
from flightsim.rl.policy import RLPolicy, model_identity  # noqa: E402
from flightsim.rl.train import load_training_config, train  # noqa: E402

ROOT = Path(__file__).parent.parent
RL_CONFIG = ROOT / "configs" / "rl" / "ppo_comfort.yaml"
COMFORT = ROOT / "configs" / "envs" / "altitude_heading_hold_comfort.yaml"
TINY = {
    "n_envs": 2,
    "total_timesteps": 1024,
    "ppo.n_steps": 256,
    "ppo.batch_size": 256,
    "ppo.n_epochs": 1,
    "evaluation.every_timesteps": 512,
    "evaluation.seeds": [1000, 1001],
    "checkpoint_every_timesteps": 512,
    "env.episode_s": 5.0,
}


class FixedModel:
    def __init__(self):
        self.seen = None

    def predict(self, obs, deterministic):
        self.seen = obs
        return np.zeros(4, dtype=np.float32), None


def test_policy_applies_training_normalization():
    model = FixedModel()
    policy = RLPolicy(model, mean=np.full(16, 1.0), var=np.full(16, 4.0), clip=2.0, epsilon=0.0)
    obs = np.linspace(-10, 10, 16).astype(np.float32)
    policy(obs, {})
    np.testing.assert_allclose(model.seen, np.clip((obs - 1.0) / 2.0, -2, 2), rtol=1e-6)


def test_training_config_inlines_the_env_config():
    cfg = load_training_config(RL_CONFIG)
    assert "env_config" not in cfg
    assert cfg["env"] == load_raw(COMFORT)


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory):
    return train(RL_CONFIG, tmp_path_factory.mktemp("rl"), TINY)


def test_tiny_training_run_produces_a_complete_run_directory(tiny_run):
    for name in ("config.json", "progress.csv", "evaluation.csv", "summary.json"):
        assert (tiny_run / name).is_file(), name
    for d in ("best", "final"):
        assert (tiny_run / d / "model.zip").is_file() and (tiny_run / d / "obs_rms.npz").is_file()
    summary = json.loads((tiny_run / "summary.json").read_text())
    assert summary["timesteps"] >= 1024 and summary["stop_reason"] == "total_timesteps"
    config = json.loads((tiny_run / "config.json").read_text())
    assert config["run_id"] == tiny_run.name and config["config"]["env"]["episode_s"] == 5.0


def test_existing_run_is_not_overwritten(tiny_run):
    with pytest.raises(FileExistsError):
        train(RL_CONFIG, tiny_run.parent, TINY)


def test_trained_agent_flies_through_the_standard_interface(tiny_run):
    policy = RLPolicy.load(tiny_run / "best")
    env = AltitudeHeadingHoldEnv(load_env_config(COMFORT, {"episode_s": 5.0}))
    a, b = run_episode(env, policy, 3), run_episode(env, policy, 3)
    assert a == b  # deterministic actions, seeded episode


def test_batch_runner_evaluates_the_agent_and_identifies_the_model(tiny_run, tmp_path):
    ident = model_identity(tiny_run / "best")
    _, manifest, table = run_batch(load_raw(COMFORT, {"episode_s": 5.0}), "rl", ident, [0, 1], tmp_path, workers=1)
    assert manifest["policy"] == "rl" and manifest["policy_config"]["model_sha256"] == ident["model_sha256"]
    assert table.num_rows == 2


def test_reward_scale_scales_training_rewards_but_not_logged_returns():
    from flightsim.rl.train import _make_env

    raw = load_raw(COMFORT, {"episode_s": 2.0})
    scaled, plain = _make_env(raw, 0.1)(), _make_env(raw, 1.0)()
    scaled.reset(seed=5)
    plain.reset(seed=5)
    action = np.zeros(4, dtype=np.float32)
    total_scaled = total_plain = 0.0
    while True:
        _, rs, term, trunc, info = scaled.step(action)
        _, rp, *_ = plain.step(action)
        total_scaled += rs
        total_plain += rp
        if term or trunc:
            break
    assert total_scaled == pytest.approx(0.1 * total_plain)
    assert info["episode"]["r"] == pytest.approx(total_plain)  # Monitor logs the true return


def test_v2_config_differs_from_v1_only_in_reward_handling():
    v1 = load_training_config(RL_CONFIG)
    v2 = load_training_config(RL_CONFIG.parent / "ppo_comfort_v2.yaml")
    assert v2["normalize"]["rewards"] is False and v2["normalize"]["reward_scale"] == 0.1
    assert {k: v for k, v in v1.items() if k != "normalize"} == {k: v for k, v in v2.items() if k != "normalize"}


# --- Residual RL on the LQR -----------------------------------------------------------

RESIDUAL_CONFIG = RL_CONFIG.parent / "residual_lqr.yaml"


def _lqr_and_env(seconds: float):
    from flightsim.rl.residual import make_lqr_policy

    cfg = load_env_config(COMFORT, {"episode_s": seconds})
    return cfg, make_lqr_policy(cfg, load_raw(ROOT / "configs" / "lqr.yaml"))


def test_residual_config_inlines_the_lqr_config_and_does_not_clip_rewards():
    cfg = load_training_config(RESIDUAL_CONFIG)
    assert cfg["residual"]["lqr"] == load_raw(ROOT / "configs" / "lqr.yaml")
    assert cfg["normalize"]["rewards"] is True and cfg["normalize"]["clip_rewards"] == 1.0e6
    assert cfg["evaluation"]["seeds"] == [1000, 1050]


def test_combine_scales_and_clips():
    from flightsim.rl.residual import combine

    out = combine(np.array([0.9, -0.5, 0.0, 0.1], dtype=np.float32), np.array([1.0, -1.0, 0.5, 0.0]), 0.2)
    np.testing.assert_allclose(out, [1.0, -0.7, 0.1, 0.1], atol=1e-6)


def test_zero_residual_flies_exactly_like_the_lqr():
    from flightsim.rl.residual import ResidualEnv, ResidualPolicy

    cfg, lqr = _lqr_and_env(10.0)
    alone = run_episode(AltitudeHeadingHoldEnv(cfg), lqr, 4)

    class Zero:
        def reset(self, info):
            pass

        def __call__(self, obs, info):
            assert obs.shape == (20,)  # task observation + LQR action
            return np.zeros(4, dtype=np.float32)

    as_policy = run_episode(AltitudeHeadingHoldEnv(cfg), ResidualPolicy(Zero(), lqr, 0.2), 4)
    assert replace(as_policy, policy="lqr") == alone  # identical flight; only the name differs

    env = ResidualEnv(AltitudeHeadingHoldEnv(cfg), lqr, 0.2)
    obs, _ = env.reset(seed=4)
    total, done = 0.0, False
    while not done:
        obs, reward, terminated, truncated, _ = env.step(np.zeros(4, dtype=np.float32))
        total += reward
        done = terminated or truncated
    assert total == alone.episode_return


@pytest.fixture(scope="module")
def tiny_residual_run(tmp_path_factory):
    return train(RESIDUAL_CONFIG, tmp_path_factory.mktemp("rl_residual"), TINY)


def test_residual_run_saves_its_spec_and_the_batch_runner_rebuilds_the_controller(tiny_residual_run, tmp_path):
    from flightsim.rl.policy import load_policy
    from flightsim.rl.residual import ResidualPolicy

    best = tiny_residual_run / "best"
    spec = json.loads((best / "residual.json").read_text())
    assert spec["scale"] == 0.2 and spec["lqr"] == load_raw(ROOT / "configs" / "lqr.yaml")
    ident = model_identity(best)
    assert "residual_sha256" in ident
    cfg = load_env_config(COMFORT, {"episode_s": 5.0})
    policy = load_policy(best, cfg)
    assert isinstance(policy, ResidualPolicy)
    a, b = run_episode(AltitudeHeadingHoldEnv(cfg), policy, 3), run_episode(AltitudeHeadingHoldEnv(cfg), policy, 3)
    assert a == b
    _, manifest, table = run_batch(load_raw(COMFORT, {"episode_s": 5.0}), "rl", ident, [0, 1], tmp_path, workers=1)
    assert manifest["policy_config"]["residual_sha256"] == ident["residual_sha256"] and table.num_rows == 2
