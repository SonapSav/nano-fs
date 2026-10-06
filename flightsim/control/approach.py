"""Approach autopilot: glide path and centreline tracking, autothrottle, flare and rollout.

Phases (latched in order):
  approach  vertical: descent-rate command = glide path rate (3 deg x ground speed) plus
            a correction for being high or low -> pitch -> elevator; throttle holds the
            target airspeed. Lateral: the ground track is steered onto the centreline
            (track, not heading, so a crosswind gives a crab instead of a drift) -> bank
            -> aileron.
  flare     below `flare_height_m` (main wheels above the ground): throttle to idle, wings
            level, descent rate commanded proportional to wheel height (an exponential
            flare, sink = height / tau), so the aircraft rounds out and touches down main
            wheels first.
  rollout   after touchdown: throttle idle, the nose lowered gently, aileron into the
            wind, the pedals (rudder and nosewheel steering) keep the centreline; once the
            nosewheel is down, the brakes stop the aircraft.

All from the full `State` plus the runway geometry the approach task provides
(`envs.approach.approach_geometry`). `dt_s` is the controller's update period.
"""

import math
from dataclasses import dataclass, replace
from pathlib import Path

from flightsim.config import load_raw
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.core import Controls, State
from flightsim.world.terrain import R_EARTH_M

KT_TO_MPS = 1852.0 / 3600.0


@dataclass(frozen=True)
class ApproachGains:
    target_kias: float
    gust_additive: float  # fraction of the reported gust factor added to the target speed (FAA AFH: one half)
    max_gust_additive_kt: float
    # Vertical
    k_glide_path: float  # (m/s descent) per m above the glide path
    max_vs_correction_mps: float
    k_vs: float  # rad pitch per m/s descent-rate error
    ki_vs: float  # rad pitch per (m/s * s)
    max_pitch_offset_rad: float  # pitch command limit about the trimmed descent attitude
    k_pitch: float  # elevator per rad pitch error
    k_pitch_rate: float  # elevator per rad/s pitch rate
    k_speed: float  # throttle per m/s airspeed error
    ki_speed: float  # throttle per (m/s * s)
    # Lateral
    intercept_m: float  # the track aims at the centreline this far ahead
    ki_cross: float  # 1/s: integral of the centreline offset (removes the standing offset in a crosswind)
    k_track: float  # rad bank per rad track error
    max_bank_rad: float
    k_bank: float  # aileron per rad bank error
    k_roll_rate: float  # aileron per rad/s roll rate
    # Flare and rollout
    wheel_height_m: float  # main wheels below the CG (CG height when parked)
    flare_height_m: float  # main wheels above the ground when the flare starts
    flare_tau_s: float  # flare: sink rate = wheel height / tau ...
    flare_min_sink_mps: float  # ... but at least this (touch down instead of floating)
    flare_max_pitch_rad: float
    k_flare: float  # rad pitch per m/s sink-rate error in the flare (pitch only ever rises)
    flare_pitch_rate_rad_s: float  # the flare pitch command rises at most this fast
    k_pitch_flare: float  # elevator per rad pitch error in the flare (slow, high-alpha flight)
    ki_pitch_flare: float  # elevator per (rad * s)
    throttle_cut_s: float  # throttle ramps to idle over this time in the flare
    touchdown_pitch_rad: float | None  # the flare's pitch rises at least to this by the ground (main wheels first)
    touchdown_pitch_shape: float  # floor = start + (touchdown - start) * (height lost / flare height)^shape
    rollout_pitch_rad: float
    k_steer: float  # rudder per rad heading error on the ground (negative: rudder + = nose left)
    # Crosswind: de-crab (align the nose with the runway) and wing low (bank into the wind)
    decrab_height_m: float  # main wheels above the ground when the de-crab starts
    k_align: float  # rudder per rad heading error from the runway (negative: rudder + = nose left)
    k_yaw_damp: float  # rudder per rad/s yaw rate
    max_wing_low_rad: float  # bank limit while de-crabbed (to stop the drift), and on the ground
    max_alpha_rad: float  # stall protection: the pitch command keeps alpha below this
    k_bank_decrab: float  # aileron per rad bank error while de-crabbed (the sideslip rolls the wing away from the wind)
    ki_wing_low: float  # rad bank per (rad track error * s) while de-crabbed (holds the wing low without a standing drift)
    # Rollout
    nose_lower_rate_rad_s: float  # after touchdown the pitch command falls this fast (lower the nose gently)
    rollout_brake: float  # brake command once the nosewheel is down (POH normal landing: minimum required)
    k_rollout_aileron: float  # aileron per rad sideslip on the ground (+: into the wind)


def approach_gains_from_raw(raw: dict) -> ApproachGains:
    v, lat, fl = raw["vertical"], raw["lateral"], raw["flare"]
    rad = math.radians
    return ApproachGains(
        target_kias=float(raw["target_kias"]),
        gust_additive=float(raw.get("gust_additive", 0.0)),
        max_gust_additive_kt=float(raw.get("max_gust_additive_kt", 0.0)),
        k_glide_path=float(v["k_glide_path"]),
        max_vs_correction_mps=float(v["max_vs_correction_mps"]),
        k_vs=float(v["k_vs"]),
        ki_vs=float(v["ki_vs"]),
        max_pitch_offset_rad=rad(v["max_pitch_offset_deg"]),
        k_pitch=float(v["k_pitch"]),
        k_pitch_rate=float(v["k_pitch_rate"]),
        k_speed=float(v["k_speed"]),
        ki_speed=float(v["ki_speed"]),
        intercept_m=float(lat["intercept_m"]),
        ki_cross=float(lat.get("ki_cross", 0.0)),
        k_track=float(lat["k_track"]),
        max_bank_rad=rad(lat["max_bank_deg"]),
        k_bank=float(lat["k_bank"]),
        k_roll_rate=float(lat["k_roll_rate"]),
        wheel_height_m=float(fl["wheel_height_m"]),
        flare_height_m=float(fl["flare_height_m"]),
        flare_tau_s=float(fl["tau_s"]),
        flare_min_sink_mps=float(fl["min_sink_mps"]),
        flare_max_pitch_rad=rad(fl["max_pitch_deg"]),
        k_flare=float(fl["k_flare"]),
        flare_pitch_rate_rad_s=rad(fl["pitch_rate_deg_s"]),
        k_pitch_flare=float(fl["k_pitch"]),
        ki_pitch_flare=float(fl["ki_pitch"]),
        throttle_cut_s=float(fl["throttle_cut_s"]),
        touchdown_pitch_rad=rad(fl["touchdown_pitch_deg"]) if "touchdown_pitch_deg" in fl else None,
        touchdown_pitch_shape=float(fl.get("touchdown_pitch_shape", 1.0)),
        rollout_pitch_rad=rad(fl["rollout_pitch_deg"]),
        k_steer=float(fl["k_steer"]),
        decrab_height_m=float(fl["decrab_height_m"]),
        k_align=float(fl["k_align"]),
        k_yaw_damp=float(fl["k_yaw_damp"]),
        max_wing_low_rad=rad(fl["max_wing_low_deg"]),
        max_alpha_rad=rad(raw.get("max_alpha_deg", 90.0)),
        k_bank_decrab=float(fl.get("k_bank_decrab", lat["k_bank"])),
        ki_wing_low=float(fl.get("ki_wing_low", 0.0)),
        nose_lower_rate_rad_s=rad(raw.get("rollout", {}).get("nose_lower_rate_deg_s", 0.0)),
        rollout_brake=float(raw.get("rollout", {}).get("brake", 0.0)),
        k_rollout_aileron=float(raw.get("rollout", {}).get("k_aileron", 0.0)),
    )


def load_approach_gains(path: str | Path) -> ApproachGains:
    return approach_gains_from_raw(load_raw(path))


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class ApproachAutopilot:
    def __init__(
        self, gains: ApproachGains, geometry: dict, trim: Controls, trim_state: State, dt_s: float, wind_report: dict | None = None
    ):
        self.g, self.geo, self.trim, self.dt_s = gains, geometry, trim, dt_s
        # Approach speed plus a fraction of the reported gust factor (FAA-H-8083-3C ch. 9).
        gust_kt = (wind_report or {}).get("gust_factor_mps", 0.0) / KT_TO_MPS
        self.target_cas_mps = (gains.target_kias + min(gains.max_gust_additive_kt, gains.gust_additive * gust_kt)) * KT_TO_MPS
        self.theta_ref = trim_state.theta_rad  # trimmed descent attitude
        h = math.radians(geometry["heading_deg"])
        self._rwy = h
        self._along = (math.cos(h), math.sin(h))
        self._right = (-math.sin(h), math.cos(h))
        self._tan_gp = math.tan(math.radians(geometry["glide_path_deg"]))
        self.phase = "approach"
        self._i_vs = 0.0
        self._i_speed = 0.0
        self._flare_s = 0.0
        self._flare_theta = -math.inf  # the flare's pitch command never decreases
        self._i_pitch = 0.0
        self._i_cross = 0.0
        self._wing_low = 0.0  # integral part of the wing-low bank while de-crabbed
        self._rollout_theta: float | None = None  # rollout: the falling pitch command

    def runway_coords(self, s: State) -> tuple[float, float]:
        g = self.geo
        dn, de = s.lat_rad * R_EARTH_M - g["threshold_north_m"], s.lon_rad * R_EARTH_M - g["threshold_east_m"]
        return dn * self._along[0] + de * self._along[1], dn * self._right[0] + de * self._right[1]

    def __call__(self, s: State, touched_down: bool, nose_wheel_down: bool = False) -> Controls:
        g, trim, dt = self.g, self.trim, self.dt_s
        along, cross = self.runway_coords(s)
        wheels_m = s.alt_agl_m - g.wheel_height_m
        if touched_down:
            self.phase = "rollout"
        elif self.phase == "approach" and wheels_m < g.flare_height_m:
            self.phase = "flare"
        climb = -s.v_down_mps
        ground_speed = math.hypot(s.v_north_mps, s.v_east_mps)
        track = math.atan2(s.v_east_mps, s.v_north_mps)
        if self.phase == "approach":
            self._i_cross = clamp(self._i_cross + cross * dt, -100.0 / max(g.ki_cross, 1e-9), 100.0 / max(g.ki_cross, 1e-9))
        track_cmd = self._rwy + math.atan2(-(cross + g.ki_cross * self._i_cross), g.intercept_m)  # aim at the centreline ahead

        if self.phase == "rollout":
            # Lower the nose gently (the pitch command falls from the touchdown attitude),
            # aileron into the wind, pedals (rudder and nosewheel) onto the centreline, and
            # brakes once the nosewheel is down.
            if self._rollout_theta is None:
                self._rollout_theta = max(s.theta_rad, g.rollout_pitch_rad)
            if g.nose_lower_rate_rad_s > 0:
                self._rollout_theta = max(-0.1, self._rollout_theta - g.nose_lower_rate_rad_s * dt)
            else:
                self._rollout_theta = g.rollout_pitch_rad
            elevator = trim.elevator + g.k_pitch * (s.theta_rad - self._rollout_theta) + g.k_pitch_rate * s.q_radps
            aileron = trim.aileron + g.k_rollout_aileron * s.beta_rad
            if g.k_rollout_aileron == 0.0:  # wings level against the drift (before rollout tuning)
                bank_cmd = clamp(g.k_track * wrap_angle_rad(track_cmd - track), -g.max_wing_low_rad, g.max_wing_low_rad)
                aileron = trim.aileron + g.k_bank_decrab * (bank_cmd - s.phi_rad) - g.k_roll_rate * s.p_radps
            rudder = trim.rudder + g.k_steer * wrap_angle_rad(track_cmd - s.psi_rad) + g.k_yaw_damp * s.r_radps
            brake = g.rollout_brake if nose_wheel_down else 0.0
            return replace(trim, elevator=clamp(elevator, -1, 1), aileron=clamp(aileron, -1, 1), rudder=clamp(rudder, -1, 1), throttle=0.0, brake=brake)

        # Lateral: the track onto the centreline. Close to the ground the rudder lines the
        # nose up with the runway (de-crab) and the bank into the wind stops the drift.
        decrab = wheels_m < g.decrab_height_m
        if decrab:
            # Sideslip: the rudder lines the nose up with the runway, and the bank into the
            # wind (proportional plus integral on the track) stops the drift.
            max_bank = g.max_wing_low_rad
            rudder = trim.rudder + g.k_align * wrap_angle_rad(self._rwy - s.psi_rad) + g.k_yaw_damp * s.r_radps
        else:
            max_bank = g.max_bank_rad if self.phase == "approach" else math.radians(3.0)
            rudder = trim.rudder
        track_err = wrap_angle_rad(track_cmd - track)
        if decrab:
            self._wing_low = clamp(self._wing_low + g.ki_wing_low * track_err * dt, -max_bank, max_bank)
        bank_cmd = clamp(g.k_track * track_err + (self._wing_low if decrab else 0.0), -max_bank, max_bank)
        k_bank = g.k_bank_decrab if decrab else g.k_bank
        aileron = trim.aileron + k_bank * (bank_cmd - s.phi_rad) - g.k_roll_rate * s.p_radps

        if self.phase == "approach":
            gp_dev = s.alt_msl_m - (self.geo["elevation_m"] + max(0.0, self.geo["aim_point_m"] - along) * self._tan_gp)
            vs_cmd = -ground_speed * self._tan_gp - clamp(g.k_glide_path * gp_dev, -g.max_vs_correction_mps, g.max_vs_correction_mps)
            speed_err = self.target_cas_mps - s.cas_mps
            self._i_speed = clamp(self._i_speed + speed_err * dt, -0.3 / max(g.ki_speed, 1e-9), 0.3 / max(g.ki_speed, 1e-9))
            throttle = clamp(trim.throttle + g.k_speed * speed_err + g.ki_speed * self._i_speed, 0.0, 1.0)
            pitch_hi = self.theta_ref + g.max_pitch_offset_rad
        else:  # flare: raise the nose as the descent rate exceeds the shrinking command
            self._flare_s += dt
            vs_cmd = -max(g.flare_min_sink_mps, max(0.0, wheels_m) / g.flare_tau_s)
            throttle = clamp(trim.throttle * (1.0 - self._flare_s / g.throttle_cut_s), 0.0, 1.0)
            if self._flare_theta == -math.inf:
                self._flare_theta = self._flare_start_theta = s.theta_rad
            target = clamp(s.theta_rad + g.k_flare * (vs_cmd - climb), -1.0, g.flare_max_pitch_rad)
            step = g.flare_pitch_rate_rad_s * dt
            self._flare_theta = max(self._flare_theta, min(target, self._flare_theta + step))
            if g.touchdown_pitch_rad is not None:
                # Attitude floor: from the flare-start pitch to the touchdown attitude as the
                # wheels come down, so a fast or nose-low flare still lands main wheels first.
                frac = clamp(1.0 - wheels_m / g.flare_height_m, 0.0, 1.0) ** g.touchdown_pitch_shape
                start = min(self._flare_start_theta, g.touchdown_pitch_rad)
                self._flare_theta = max(self._flare_theta, start + (g.touchdown_pitch_rad - start) * frac)
        vs_err = vs_cmd - climb
        if self.phase == "approach":
            self._i_vs = clamp(self._i_vs + vs_err * dt, -0.1 / max(g.ki_vs, 1e-9), 0.1 / max(g.ki_vs, 1e-9))
            theta_cmd = clamp(self.theta_ref + g.k_vs * vs_err + g.ki_vs * self._i_vs, self.theta_ref - g.max_pitch_offset_rad, pitch_hi)
        else:
            theta_cmd = self._flare_theta
        # Stall protection (gusts and the wind shear near the ground cost airspeed): no more
        # nose-up than keeps the angle of attack below the limit.
        theta_cmd = min(theta_cmd, s.theta_rad + g.max_alpha_rad - s.alpha_rad)
        if self.phase == "flare":
            self._flare_theta = min(self._flare_theta, theta_cmd)
        # Elevator + is nose down: pitch above the command pushes.
        if self.phase == "approach":
            elevator = trim.elevator + g.k_pitch * (s.theta_rad - theta_cmd) + g.k_pitch_rate * s.q_radps
        else:
            err = s.theta_rad - theta_cmd
            self._i_pitch = clamp(self._i_pitch + err * dt, -0.5 / max(g.ki_pitch_flare, 1e-9), 0.5 / max(g.ki_pitch_flare, 1e-9))
            elevator = trim.elevator + g.k_pitch_flare * err + g.ki_pitch_flare * self._i_pitch + g.k_pitch_rate * s.q_radps
        return replace(trim, elevator=clamp(elevator, -1, 1), aileron=clamp(aileron, -1, 1), rudder=clamp(rudder, -1, 1), throttle=throttle)
