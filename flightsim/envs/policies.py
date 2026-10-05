"""Policies that act through the environment's action interface, so baselines and
learned agents are compared under the same constraints (decision rate, action mapping)."""

from typing import Protocol

import numpy as np

from flightsim.control.approach import ApproachAutopilot, ApproachGains
from flightsim.control.autopilot import Autopilot, AutopilotGains
from flightsim.control.lqr import GainSchedule, LQRAutopilot
from flightsim.envs.altitude_heading import controls_to_action


class Policy(Protocol):
    name: str

    def reset(self, info: dict) -> None: ...

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray: ...


class TrimHoldPolicy:
    """Holds the trim controls: the do-nothing reference."""

    name = "trim_hold"

    def reset(self, info: dict) -> None:
        self._action = controls_to_action(info["trim"], info["action_names"])

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray:
        return self._action


class PIDPolicy:
    """The baseline PID autopilot, running at the environment's decision rate."""

    name = "pid"

    def __init__(self, gains: AutopilotGains, control_rate_hz: float):
        self.gains = gains
        self.dt_s = 1.0 / control_rate_hz

    def reset(self, info: dict) -> None:
        self._autopilot = Autopilot(self.gains, info["trim"], info["trim_state"].theta_rad, info["targets"], self.dt_s)
        self._names = info["action_names"]

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray:
        return controls_to_action(self._autopilot(info["state"]), self._names)


class LQRPolicy:
    """The gain-scheduled LQR autopilot, at the environment's decision rate. The schedule
    is designed once at construction (trim + linearize at each grid point)."""

    name = "lqr"

    def __init__(self, schedule: GainSchedule, control_rate_hz: float):
        self.schedule = schedule
        self.dt_s = 1.0 / control_rate_hz

    @classmethod
    def designed(cls, env_cfg, lqr_raw: dict) -> "LQRPolicy":
        """For a task: the schedule designed for its aircraft, loading and decision rate (cached on disk)."""
        schedule = GainSchedule.cached(env_cfg.aircraft, env_cfg.loading, lqr_raw, 1.0 / env_cfg.control_rate_hz)
        return cls(schedule, env_cfg.control_rate_hz)

    def reset(self, info: dict) -> None:
        self._autopilot = LQRAutopilot(self.schedule, info["trim"], info["trim_state"], info["targets"], self.dt_s)
        self._names = info["action_names"]

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray:
        return controls_to_action(self._autopilot(info["state"]), self._names)


class ApproachPolicy:
    """The approach autopilot (glide path, centreline, flare) for the approach task."""

    name = "approach"

    def __init__(self, gains: ApproachGains, control_rate_hz: float):
        self.gains = gains
        self.dt_s = 1.0 / control_rate_hz

    def reset(self, info: dict) -> None:
        if "approach" not in info:
            raise ValueError("the approach autopilot needs the approach task")
        self._autopilot = ApproachAutopilot(self.gains, info["approach"], info["trim"], info["trim_state"], self.dt_s)
        self._names = info["action_names"]

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray:
        return controls_to_action(self._autopilot(info["state"], info["touched_down"]), self._names)
