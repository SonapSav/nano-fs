"""Human pilot input as a policy.

Inputs arrive asynchronously (from the browser) and are sampled by the environment at
its fixed decision rate: the latest input is held until the next decision. If inputs
stop arriving, the stick and pedals return to centre (trim) and the brakes release;
throttle, flaps and pitch trim hold their positions. Flaps, pitch trim and brake only
matter if the task's action set includes them; otherwise they stay at trim.
"""

import math
import time
from collections.abc import Callable

import numpy as np

from dataclasses import replace

from flightsim.core import Controls
from flightsim.envs.altitude_heading import ACTION_NAMES, controls_to_action

STALE_AFTER_S = 0.5


class HumanPolicy:
    name = "human"

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._trim = Controls()
        self._names: tuple[str, ...] = ()
        self._input = (0.0, 0.0, 0.0)  # (elevator, aileron, rudder) offsets from trim, in [-1, 1]
        self._throttle = 0.0
        self._flaps = 0.0
        self._pitch_trim = 0.0
        self._brake = 0.0
        self._received_at = -math.inf

    def reset(self, info: dict) -> None:
        self._trim = info["trim"]
        self._names = info.get("action_names", ACTION_NAMES)  # envs always provide it
        self._input = (0.0, 0.0, 0.0)
        self._throttle = float(self._trim.throttle)
        self._flaps = float(self._trim.flaps)
        self._pitch_trim = float(self._trim.pitch_trim)
        self._brake = 0.0
        self._received_at = -math.inf

    def set_input(
        self, elevator: float, aileron: float, rudder: float, throttle: float,
        flaps: float | None = None, pitch_trim: float | None = None, brake: float | None = None,
    ) -> None:  # fmt: skip
        """Stick and pedals relative to trim (0 = centred); throttle, flaps and brake
        absolute in [0, 1]; pitch trim absolute in [-1, 1] (positive = nose down). Omitted
        flaps or pitch trim keep their current value; an omitted brake is released."""
        values = [v for v in (elevator, aileron, rudder, throttle, flaps, pitch_trim, brake) if v is not None]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
            raise ValueError("inputs must be finite numbers")
        clip = lambda v, lo, hi: max(lo, min(hi, float(v)))  # noqa: E731
        self._input = (clip(elevator, -1, 1), clip(aileron, -1, 1), clip(rudder, -1, 1))
        self._throttle = clip(throttle, 0, 1)
        if flaps is not None:
            self._flaps = clip(flaps, 0, 1)
        if pitch_trim is not None:
            self._pitch_trim = clip(pitch_trim, -1, 1)
        self._brake = 0.0 if brake is None else clip(brake, 0, 1)
        self._received_at = self._clock()

    def __call__(self, obs: np.ndarray, info: dict) -> np.ndarray:
        stale = self._clock() - self._received_at > STALE_AFTER_S
        e, a, r = (0.0, 0.0, 0.0) if stale else self._input
        t = self._trim
        u = replace(
            t, elevator=t.elevator + e, aileron=t.aileron + a, rudder=t.rudder + r,
            throttle=self._throttle, flaps=self._flaps, pitch_trim=self._pitch_trim,
            brake=0.0 if stale else self._brake,
        )  # fmt: skip
        return np.clip(controls_to_action(u, self._names), -1.0, 1.0)
