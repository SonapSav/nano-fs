"""Minimal heading hold for step 1: heading error -> bank command -> aileron.

Elevator, throttle and rudder stay at their trim values. The full PID baseline
autopilot comes in step 3.
"""

import math
from dataclasses import replace

from flightsim.config import HeadingHoldGains
from flightsim.core import Controls, State


def wrap_angle_rad(a: float) -> float:
    """Wrap to [-pi, pi)."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class HeadingHold:
    def __init__(self, gains: HeadingHoldGains, trim: Controls, target_heading_rad: float):
        self.gains = gains
        self.trim = trim
        self.target_heading_rad = target_heading_rad

    def __call__(self, s: State) -> Controls:
        g = self.gains
        heading_error = wrap_angle_rad(self.target_heading_rad - s.psi_rad)
        bank_cmd = max(-g.max_bank_rad, min(g.max_bank_rad, g.k_heading * heading_error))
        aileron = self.trim.aileron + g.k_bank * (bank_cmd - s.phi_rad) - g.k_roll_rate * s.p_radps
        return replace(self.trim, aileron=max(-1.0, min(1.0, aileron)))
