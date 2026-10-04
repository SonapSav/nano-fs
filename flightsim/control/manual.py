"""Human pilot input as a policy.

Inputs arrive asynchronously (from the browser) and are sampled by the environment at
its fixed decision rate: the latest input is held until the next decision. If inputs
stop arriving, the stick and pedals return to centre (trim) and the throttle holds.
"""

import math
import time
from collections.abc import Callable

import numpy as np

from flightsim.envs.altitude_heading import controls_to_action

STALE_AFTER_S = 0.5


class HumanPolicy:
    name = "human"

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._trim_action = np.zeros(4, dtype=np.float32)
        self._input = None  # (elevator, aileron, rudder) offsets from trim, in [-1, 1]
        self._throttle = None
        self._received_at = -math.inf

    def reset(self, info: dict) -> None:
        self._trim_action = controls_to_action(info["trim"])
        self._input = (0.0, 0.0, 0.0)
        self._throttle = float(info["trim"].throttle)
        self._received_at = -math.inf

    def set_input(self, elevator: float, aileron: float, rudder: float, throttle: float) -> None:
        """Stick and pedals relative to trim (0 = centred), throttle absolute in [0, 1]."""
        values = (elevator, aileron, rudder, throttle)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ValueError("inputs must be finite numbers")
        clip = lambda v, lo, hi: max(lo, min(hi, float(v)))  # noqa: E731
        self._input = (clip(elevator, -1, 1), clip(aileron, -1, 1), clip(rudder, -1, 1))
        self._throttle = clip(throttle, 0, 1)
        self._received_at = self._clock()

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray:
        stale = self._clock() - self._received_at > STALE_AFTER_S
        e, a, r = (0.0, 0.0, 0.0) if stale else self._input
        trim = self._trim_action
        action = np.array([trim[0] + e, trim[1] + a, trim[2] + r, 2.0 * self._throttle - 1.0], dtype=np.float32)
        return np.clip(action, -1.0, 1.0)
