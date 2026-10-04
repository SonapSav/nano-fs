"""Baseline PID autopilot: altitude, heading and airspeed hold.

Cascaded loops, all from the full `State`:
- lateral: heading error -> bank command -> aileron (the step 1 heading hold)
- longitudinal: altitude error -> climb-rate command -> pitch command -> elevator
- speed: airspeed error -> throttle (autothrottle)

Rudder, pitch trim, mixture and flaps stay at their trim values. Integrators are
clamped (anti-windup). `dt_s` is the controller's own update period.
"""

import math
from dataclasses import dataclass, replace
from pathlib import Path

from flightsim.config import HeadingHoldGains, load_raw, parse_heading_hold
from flightsim.control.heading_hold import HeadingHold
from flightsim.core import Controls, State


@dataclass(frozen=True)
class AutopilotGains:
    heading: HeadingHoldGains
    k_altitude: float  # (m/s climb) per m altitude error
    max_climb_mps: float
    k_climb: float  # rad pitch per m/s climb-rate error
    ki_climb: float  # rad pitch per (m/s * s)
    max_pitch_offset_rad: float  # pitch command limit about trim pitch
    k_pitch: float  # elevator per rad pitch error
    k_pitch_rate: float  # elevator per rad/s pitch rate
    k_speed: float  # throttle per m/s airspeed error
    ki_speed: float  # throttle per (m/s * s)


@dataclass(frozen=True)
class Targets:
    alt_msl_m: float
    heading_rad: float
    tas_mps: float


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class Autopilot:
    def __init__(self, gains: AutopilotGains, trim: Controls, trim_pitch_rad: float, targets: Targets, dt_s: float):
        self.gains = gains
        self.trim = trim
        self.trim_pitch_rad = trim_pitch_rad
        self.targets = targets
        self.dt_s = dt_s
        self._heading = HeadingHold(gains.heading, trim, targets.heading_rad)
        self._climb_integral = 0.0
        self._speed_integral = 0.0

    def __call__(self, s: State) -> Controls:
        g, t = self.gains, self.targets

        climb_cmd = _clip(g.k_altitude * (t.alt_msl_m - s.alt_msl_m), -g.max_climb_mps, g.max_climb_mps)
        climb_err = climb_cmd - (-s.v_down_mps)
        i_limit = g.max_pitch_offset_rad / g.ki_climb if g.ki_climb else 0.0
        self._climb_integral = _clip(self._climb_integral + climb_err * self.dt_s, -i_limit, i_limit)
        pitch_cmd = self.trim_pitch_rad + _clip(
            g.k_climb * climb_err + g.ki_climb * self._climb_integral, -g.max_pitch_offset_rad, g.max_pitch_offset_rad
        )
        # Positive elevator is nose down, so pitch error drives it negative.
        elevator = self.trim.elevator - g.k_pitch * (pitch_cmd - s.theta_rad) + g.k_pitch_rate * s.q_radps

        speed_err = t.tas_mps - s.tas_mps
        s_limit = 1.0 / g.ki_speed if g.ki_speed else 0.0
        self._speed_integral = _clip(self._speed_integral + speed_err * self.dt_s, -s_limit, s_limit)
        throttle = self.trim.throttle + g.k_speed * speed_err + g.ki_speed * self._speed_integral

        aileron = self._heading(s).aileron
        return replace(
            self.trim,
            elevator=_clip(elevator, -1.0, 1.0),
            aileron=aileron,
            throttle=_clip(throttle, 0.0, 1.0),
        )


def load_autopilot_gains(path: str | Path, overrides: dict | None = None) -> AutopilotGains:
    return autopilot_gains_from_raw(load_raw(path, overrides))


def autopilot_gains_from_raw(raw: dict) -> AutopilotGains:
    return AutopilotGains(
        heading=parse_heading_hold(raw["heading_hold"]),
        k_altitude=float(raw["k_altitude"]),
        max_climb_mps=float(raw["max_climb_mps"]),
        k_climb=float(raw["k_climb"]),
        ki_climb=float(raw["ki_climb"]),
        max_pitch_offset_rad=math.radians(raw["max_pitch_offset_deg"]),
        k_pitch=float(raw["k_pitch"]),
        k_pitch_rate=float(raw["k_pitch_rate"]),
        k_speed=float(raw["k_speed"]),
        ki_speed=float(raw["ki_speed"]),
    )
