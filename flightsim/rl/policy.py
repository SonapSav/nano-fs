"""A trained Stable-Baselines3 agent as a policy, so it is evaluated exactly like the
autopilots (same environment interface, decision rate, batch runner and metrics).

A model directory holds `model.zip` (the SB3 model) and `obs_rms.npz` (the observation
normalization statistics from training, applied here the way VecNormalize applied them).
"""

import hashlib
from pathlib import Path

import numpy as np

MODEL_FILE = "model.zip"
OBS_RMS_FILE = "obs_rms.npz"


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_identity(model_dir: str | Path) -> dict:
    """Config for the batch runner: the directory plus content hashes, so a batch id
    identifies the exact network and normalization."""
    d = Path(model_dir)
    return {
        "model_dir": str(d),
        "model_sha256": file_sha256(d / MODEL_FILE),
        "obs_rms_sha256": file_sha256(d / OBS_RMS_FILE),
    }


def save_obs_rms(path: str | Path, mean: np.ndarray, var: np.ndarray, clip: float, epsilon: float) -> None:
    np.savez(path, mean=mean, var=var, clip=np.array(clip), epsilon=np.array(epsilon))


class RLPolicy:
    name = "rl"

    def __init__(self, model, mean: np.ndarray, var: np.ndarray, clip: float, epsilon: float):
        self.model = model
        self._mean, self._std = mean, np.sqrt(var + epsilon)
        self._clip = clip

    @classmethod
    def load(cls, model_dir: str | Path) -> "RLPolicy":
        import torch
        from stable_baselines3 import PPO

        torch.set_num_threads(1)  # batch workers run one episode each; avoid oversubscription
        d = Path(model_dir)
        rms = np.load(d / OBS_RMS_FILE)
        model = PPO.load(d / MODEL_FILE, device="cpu")
        return cls(model, rms["mean"], rms["var"], float(rms["clip"]), float(rms["epsilon"]))

    def reset(self, info: dict) -> None:
        pass

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray:
        normalized = np.clip((obs - self._mean) / self._std, -self._clip, self._clip).astype(np.float32)
        action, _ = self.model.predict(normalized, deterministic=True)
        return action
