import json
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
