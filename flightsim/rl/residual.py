"""Residual RL: the agent adds a bounded correction to the LQR autopilot's commands.

    action = clip(lqr_action + scale * residual, -1, 1),  residual in [-1, 1]

The agent observes the task observation followed by the LQR's action, so it can learn
when the autopilot's command needs correcting. With a zero residual the controller is
exactly the LQR, so training starts from a controller that completes every episode.

The same composition runs in training (ResidualEnv) and evaluation (ResidualPolicy).
"""

import gymnasium as gym
import numpy as np

from flightsim.envs.policies import LQRPolicy


def make_lqr_policy(env_cfg, lqr_raw: dict) -> LQRPolicy:
    return LQRPolicy.designed(env_cfg, lqr_raw)


def augment(obs: np.ndarray, base_action: np.ndarray) -> np.ndarray:
    return np.concatenate([obs, base_action]).astype(np.float32)


def combine(base_action: np.ndarray, residual: np.ndarray, scale: float) -> np.ndarray:
    return np.clip(base_action + scale * np.asarray(residual, dtype=np.float32), -1.0, 1.0).astype(np.float32)


class ResidualEnv(gym.Wrapper):
    """Training view of the task: actions are residuals, observations include the LQR action."""

    def __init__(self, env, base: LQRPolicy, scale: float):
        super().__init__(env)
        self.base, self.scale = base, scale
        n_obs, n_act = env.observation_space.shape[0], env.action_space.shape[0]
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(n_obs + n_act,), dtype=np.float32)

    def reset(self, **kwargs):
        obs, info = self.env.reset(**kwargs)
        self.base.reset(info)
        self._base_action = np.asarray(self.base(obs, info), dtype=np.float32)
        return augment(obs, self._base_action), info

    def step(self, residual):
        obs, reward, terminated, truncated, info = self.env.step(combine(self._base_action, residual, self.scale))
        # The LQR is called once per decision, in the same order as when it flies alone
        # (one extra call after the last step, whose action is never used).
        self._base_action = np.asarray(self.base(obs, info), dtype=np.float32)
        return augment(obs, self._base_action), reward, terminated, truncated, info


class ResidualPolicy:
    """Evaluation view: LQR plus a trained agent's residual, through the standard policy interface."""

    name = "rl_residual"

    def __init__(self, agent, base: LQRPolicy, scale: float):
        self.agent, self.base, self.scale = agent, base, scale

    def reset(self, info: dict) -> None:
        self.base.reset(info)
        self.agent.reset(info)

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray:
        base_action = np.asarray(self.base(obs, info), dtype=np.float32)
        return combine(base_action, self.agent(augment(obs, base_action), info), self.scale)
