"""Takeoff autopilot: ground roll, rotation and climb-out on the runway heading.

Phases (latched in order):
  roll     full throttle (ramped in), brakes off, elevator neutral; the pedals (rudder and
           nosewheel) steer the track onto the centreline, the ailerons go into the wind
           (sideslip) and hold the wings level.
  rotate   from `rotate_kias` (POH: lift the nose wheel at 55 KIAS): the pitch command rises
           at `rotate_rate` to `rotate_pitch`, below the tail-skid contact attitude, until
           the aircraft lifts off.
  climb    airborne: the rotation attitude is held until the wheels are `clear_height_m`
           up (so the aircraft does not settle back), then pitch holds the climb speed
           (speed on pitch, full throttle). From lift-off the ground track follows the
           extended centreline (bank, limited close to the ground, plus an integral of the
           offset) and the rudder centres the slip ball, starting from the ground roll's
           last rudder (the propeller's yaw), so a crosswind gives a crab.

All from the full `State` plus the runway geometry the takeoff task provides
(`envs.takeoff.takeoff_geometry`). `dt_s` is the controller's update period.
"""

import math
from dataclasses import dataclass, replace
from pathlib import Path

from flightsim.config import load_raw
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.core import Controls, State
from flightsim.envs.runway import Runway

KT_TO_MPS = 1852.0 / 3600.0


@dataclass(frozen=True)
class TakeoffGains:
    rotate_cas_mps: float
    climb_cas_mps: float
    throttle_ramp_s: float
    # Ground roll
    ground_intercept_m: float  # the track aims at the centreline this far ahead
    k_steer: float  # rudder per rad track error (negative: rudder + = nose left)
    k_yaw_damp: float  # rudder per rad/s yaw rate
    k_aileron_beta: float  # aileron per rad sideslip (+: into the wind)
    k_wings_level: float  # aileron per rad bank
    k_roll_rate: float  # aileron per rad/s roll rate
    # Rotation and pitch
    rotate_rate_rad_s: float
    rotate_pitch_rad: float
    k_pitch: float  # elevator per rad pitch error (elevator + = nose down)
    k_pitch_rate: float  # elevator per rad/s pitch rate
    # Climb
    k_speed: float  # rad pitch per m/s above the climb speed
    ki_speed: float  # rad pitch per (m/s * s)
    max_pitch_rad: float
    clear_height_m: float  # wheels this high: the pitch may exceed the rotation attitude
    max_alpha_rad: float  # stall protection
    air_intercept_m: float
    ki_cross: float  # 1/s: integral of the centreline offset once airborne
    k_track: float  # rad bank per rad track error
    max_bank_rad: float
    low_bank_rad: float  # bank limit below `low_bank_height_m`
    low_bank_height_m: float
    k_bank: float  # aileron per rad bank error
    k_beta: float  # rudder per rad sideslip (centres the ball)
    ki_beta: float  # rudder per (rad sideslip * s): the bias against the propeller's yaw


def takeoff_gains_from_raw(raw: dict) -> TakeoffGains:
    g, r, c = raw["ground"], raw["rotation"], raw["climb"]
    rad = math.radians
    return TakeoffGains(
        rotate_cas_mps=float(raw["rotate_kias"]) * KT_TO_MPS,
        climb_cas_mps=float(raw["climb_kias"]) * KT_TO_MPS,
        throttle_ramp_s=float(raw["throttle_ramp_s"]),
        ground_intercept_m=float(g["intercept_m"]),
        k_steer=float(g["k_steer"]),
        k_yaw_damp=float(g["k_yaw_damp"]),
        k_aileron_beta=float(g["k_aileron_beta"]),
        k_wings_level=float(g["k_wings_level"]),
        k_roll_rate=float(g["k_roll_rate"]),
        rotate_rate_rad_s=rad(r["rate_deg_s"]),
        rotate_pitch_rad=rad(r["pitch_deg"]),
        k_pitch=float(r["k_pitch"]),
        k_pitch_rate=float(r["k_pitch_rate"]),
        k_speed=float(c["k_speed"]),
        ki_speed=float(c["ki_speed"]),
        max_pitch_rad=rad(c["max_pitch_deg"]),
        clear_height_m=float(c["clear_height_m"]),
        max_alpha_rad=rad(c["max_alpha_deg"]),
        air_intercept_m=float(c["intercept_m"]),
        ki_cross=float(c["ki_cross"]),
        k_track=float(c["k_track"]),
        max_bank_rad=rad(c["max_bank_deg"]),
        low_bank_rad=rad(c["low_bank_deg"]),
        low_bank_height_m=float(c["low_bank_height_m"]),
        k_bank=float(c["k_bank"]),
        k_beta=float(c["k_beta"]),
        ki_beta=float(c["ki_beta"]),
    )


def load_takeoff_gains(path: str | Path) -> TakeoffGains:
    return takeoff_gains_from_raw(load_raw(path))


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class TakeoffAutopilot:
    def __init__(self, gains: TakeoffGains, geometry: dict, trim: Controls, parked: State, dt_s: float):
        self.g, self.geo, self.trim, self.dt_s = gains, geometry, trim, dt_s
        h = math.radians(geometry["heading_deg"])
        self._rwy = h
        self._along = (math.cos(h), math.sin(h))
        self._runway = Runway.from_geometry(geometry)
        self._right = (-math.sin(h), math.cos(h))
        self._parked_cg_msl = parked.alt_msl_m  # wheels on the runway
        self.phase = "roll"
        self._t = 0.0
        self._theta_cmd: float | None = None
        self._i_speed = 0.0
        self._i_cross = 0.0
        self._rudder_bias: float | None = None  # airborne: starts at the last ground-law rudder
        self._last_rudder = trim.rudder

    def runway_coords(self, s: State) -> tuple[float, float]:
        return self._runway.coords(s)

    def __call__(self, s: State, on_ground: bool) -> Controls:
        g, trim, dt = self.g, self.trim, self.dt_s
        self._t += dt
        _, cross = self.runway_coords(s)
        height = s.alt_msl_m - self._parked_cg_msl  # wheels above the runway
        if self.phase == "roll" and s.cas_mps >= g.rotate_cas_mps:
            self.phase = "rotate"
            self._theta_cmd = s.theta_rad
        if self.phase == "rotate" and not on_ground:
            self.phase = "climb"
        throttle = clamp(trim.throttle + (1.0 - trim.throttle) * self._t / g.throttle_ramp_s, 0.0, 1.0)
        track = math.atan2(s.v_east_mps, s.v_north_mps) if math.hypot(s.v_north_mps, s.v_east_mps) > 1.0 else s.psi_rad
        airborne = self.phase == "climb"
        clear = airborne and height > g.clear_height_m

        # Lateral
        beta = s.beta_rad if s.tas_mps > 5.0 else 0.0
        if not airborne:
            track_cmd = self._rwy + math.atan2(-cross, g.ground_intercept_m)
            rudder = trim.rudder + g.k_steer * wrap_angle_rad(track_cmd - track) + g.k_yaw_damp * s.r_radps
            aileron = trim.aileron + g.k_aileron_beta * beta - g.k_wings_level * s.phi_rad - g.k_roll_rate * s.p_radps
        else:
            self._i_cross = clamp(self._i_cross + cross * dt, -100.0 / max(g.ki_cross, 1e-9), 100.0 / max(g.ki_cross, 1e-9))
            track_cmd = self._rwy + math.atan2(-(cross + g.ki_cross * self._i_cross), g.air_intercept_m)
            limit = g.low_bank_rad if height < g.low_bank_height_m else g.max_bank_rad
            bank_cmd = clamp(g.k_track * wrap_angle_rad(track_cmd - track), -limit, limit)
            aileron = trim.aileron + g.k_bank * (bank_cmd - s.phi_rad) - g.k_roll_rate * s.p_radps
            if self._rudder_bias is None:
                self._rudder_bias = self._last_rudder
            self._rudder_bias = clamp(self._rudder_bias - g.ki_beta * beta * dt, -0.5, 0.5)
            rudder = self._rudder_bias - g.k_beta * beta

        # Vertical
        if self.phase == "roll":
            elevator = trim.elevator
        else:
            if not clear:  # rotate, and hold the attitude until the wheels are clear
                self._theta_cmd = min(g.rotate_pitch_rad, self._theta_cmd + g.rotate_rate_rad_s * dt)
            else:
                # Speed on pitch: too fast -> nose up.
                err = s.cas_mps - g.climb_cas_mps
                self._i_speed = clamp(self._i_speed + err * dt, -0.2 / max(g.ki_speed, 1e-9), 0.2 / max(g.ki_speed, 1e-9))
                target = clamp(g.rotate_pitch_rad + g.k_speed * err + g.ki_speed * self._i_speed, 0.0, g.max_pitch_rad)
                step = g.rotate_rate_rad_s * dt  # smooth changes of the attitude
                self._theta_cmd = clamp(target, self._theta_cmd - step, self._theta_cmd + step)
            theta_cmd = min(self._theta_cmd, s.theta_rad + g.max_alpha_rad - s.alpha_rad)
            elevator = trim.elevator + g.k_pitch * (s.theta_rad - theta_cmd) + g.k_pitch_rate * s.q_radps
        rudder = clamp(rudder, -1, 1)
        self._last_rudder = rudder
        return replace(
            trim, elevator=clamp(elevator, -1, 1), aileron=clamp(aileron, -1, 1), rudder=rudder,
            throttle=throttle, brake=0.0,
        )  # fmt: skip
