"""Circuit autopilot: takeoff, a left-hand traffic pattern, approach and landing.

Legs (runway frame: `along` past the threshold, `cross` right of the centreline; left
traffic flies the pattern on the left, cross < 0):
  takeoff    the takeoff autopilot (control/takeoff.py) until past the departure end and
             within `crosswind_below_ft` of pattern altitude (AC 90-66B 11.7, 11.8)
  crosswind  left turn, climbing to pattern altitude
  downwind   parallel to the runway `downwind_offset_m` out, at pattern altitude until
             abeam the threshold (AC 90-66B 11.5), then flaps 10 and the descent
  base       turned at 45 deg from the threshold (along = -offset); flaps 20
  final      turned onto the extended centreline; flaps 30; once established (close to
             the centreline and on track) the approach autopilot (control/approach.py)
             flies the glide path, flare and rollout
The descent follows one profile: the glide path angle measured along the remaining
pattern path to the aim point (abeam the threshold to downwind the profile starts near
pattern altitude when the offset is 1 nm), so the final starts on the glide path.

Pattern legs are flown by line following (track onto the leg line, bank-limited turns
with lead), a ball-centring rudder, speed on pitch when climbing at full power, and
otherwise climb rate on pitch with the throttle holding the speed. Pitch trim moves
slowly from the takeoff setting to the landing configuration's.
"""

import math
from dataclasses import dataclass, replace
from pathlib import Path

from flightsim.config import load_raw
from flightsim.control.approach import ApproachAutopilot, ApproachGains, approach_gains_from_raw
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.control.takeoff import TakeoffAutopilot, TakeoffGains, takeoff_gains_from_raw
from flightsim.core import Controls, State
from flightsim.world.terrain import R_EARTH_M

KT_TO_MPS = 1852.0 / 3600.0
G = 9.80665


@dataclass(frozen=True)
class PatternGains:
    pattern_height_m: float
    crosswind_below_m: float  # turn crosswind within this of pattern altitude
    downwind_offset_m: float
    climb_cas_mps: float
    downwind_cas_mps: float
    descent_cas_mps: float  # after abeam the threshold (flaps 10) and on base (flaps 20)
    flaps_abeam: float
    flaps_base: float
    flaps_final: float
    vfe_10_mps: float  # flap limits (POH): flaps up to 10 deg below this ...
    vfe_full_mps: float  # ... beyond 10 deg below this
    # Lateral
    intercept_m: float
    k_track: float
    max_bank_rad: float
    k_bank: float
    k_roll_rate: float
    k_beta: float
    ki_beta: float
    # Vertical and speed
    climb_margin_m: float  # this far below the target: climb at full power, speed on pitch
    k_alt: float  # (m/s climb) per m height error
    max_climb_mps: float
    max_descent_mps: float
    k_vs: float  # rad pitch per m/s climb-rate error
    ki_vs: float
    k_speed_pitch: float  # climbing: rad pitch per m/s above the climb speed
    ki_speed_pitch: float
    k_pitch: float  # elevator per rad pitch error
    ki_pitch: float
    k_pitch_rate: float
    max_pitch_rad: float
    min_pitch_rad: float
    k_throttle: float  # throttle per m/s speed error
    ki_throttle: float
    pitch_trim_rate: float  # per s
    # Hand-over to the approach autopilot on final
    established_cross_m: float
    established_track_rad: float


def pattern_gains_from_raw(raw: dict) -> PatternGains:
    rad, kt = math.radians, lambda v: float(v) * KT_TO_MPS
    lat, ver = raw["lateral"], raw["vertical"]
    return PatternGains(
        pattern_height_m=float(raw["pattern_height_ft"]) * 0.3048,
        crosswind_below_m=float(raw["crosswind_below_ft"]) * 0.3048,
        downwind_offset_m=float(raw["downwind_offset_m"]),
        climb_cas_mps=kt(raw["climb_kias"]),
        downwind_cas_mps=kt(raw["downwind_kias"]),
        descent_cas_mps=kt(raw["descent_kias"]),
        flaps_abeam=float(raw["flaps_abeam"]),
        flaps_base=float(raw["flaps_base"]),
        flaps_final=float(raw["flaps_final"]),
        vfe_10_mps=kt(raw["vfe_10_kias"]),
        vfe_full_mps=kt(raw["vfe_full_kias"]),
        intercept_m=float(lat["intercept_m"]),
        k_track=float(lat["k_track"]),
        max_bank_rad=rad(lat["max_bank_deg"]),
        k_bank=float(lat["k_bank"]),
        k_roll_rate=float(lat["k_roll_rate"]),
        k_beta=float(lat["k_beta"]),
        ki_beta=float(lat["ki_beta"]),
        climb_margin_m=float(ver["climb_margin_m"]),
        k_alt=float(ver["k_alt"]),
        max_climb_mps=float(ver["max_climb_fpm"]) * 0.3048 / 60,
        max_descent_mps=float(ver["max_descent_fpm"]) * 0.3048 / 60,
        k_vs=float(ver["k_vs"]),
        ki_vs=float(ver["ki_vs"]),
        k_speed_pitch=float(ver["k_speed_pitch"]),
        ki_speed_pitch=float(ver["ki_speed_pitch"]),
        k_pitch=float(ver["k_pitch"]),
        ki_pitch=float(ver["ki_pitch"]),
        k_pitch_rate=float(ver["k_pitch_rate"]),
        max_pitch_rad=rad(ver["max_pitch_deg"]),
        min_pitch_rad=rad(ver["min_pitch_deg"]),
        k_throttle=float(ver["k_throttle"]),
        ki_throttle=float(ver["ki_throttle"]),
        pitch_trim_rate=float(raw["pitch_trim_rate"]),
        established_cross_m=float(raw["established"]["cross_m"]),
        established_track_rad=rad(raw["established"]["track_deg"]),
    )


@dataclass(frozen=True)
class CircuitGains:
    takeoff: TakeoffGains
    pattern: PatternGains
    approach: ApproachGains


def load_circuit_raw(path: str | Path) -> dict:
    """The circuit autopilot config with its takeoff and approach autopilot files inlined
    (so the config hash covers every gain)."""
    raw = load_raw(path)
    base = Path(path).parent
    return {**raw, "takeoff": load_raw(base / raw["takeoff"]), "approach": load_raw(base / raw["approach"])}


def circuit_gains_from_raw(raw: dict) -> CircuitGains:
    return CircuitGains(
        takeoff_gains_from_raw(raw["takeoff"]), pattern_gains_from_raw(raw["pattern"]), approach_gains_from_raw(raw["approach"])
    )


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class CircuitAutopilot:
    LEGS = ("takeoff", "crosswind", "downwind", "base", "final", "approach")

    def __init__(
        self, gains: CircuitGains, geometry: dict, trim: Controls, trim_state: State,
        approach_trim: Controls, approach_trim_state: State, dt_s: float, wind_report: dict | None = None,
    ):  # fmt: skip
        self.g, self.geo, self.dt_s = gains, geometry, dt_s
        self.trim, self.approach_trim, self.approach_trim_state = trim, approach_trim, approach_trim_state
        self.wind_report = wind_report
        h = math.radians(geometry["heading_deg"])
        self._rwy = h
        self._along = (math.cos(h), math.sin(h))
        self._right = (-math.sin(h), math.cos(h))
        self._tan_gp = math.tan(math.radians(geometry["glide_path_deg"]))
        self._elev = geometry["elevation_m"]
        self.side = -1.0  # left traffic: the pattern is left of the runway (cross < 0)
        self.leg = "takeoff"
        self._takeoff = TakeoffAutopilot(gains.takeoff, geometry, trim, trim_state, dt_s)
        self._approach: ApproachAutopilot | None = None
        self._crosswind_along = 0.0
        self._rudder_bias: float | None = None
        self._theta_bias: float | None = None  # climb-rate (or speed) loop integral, as a pitch
        self._climbing: bool | None = None
        self._i_pitch = 0.0
        self._throttle: float | None = None
        self._pitch_trim = trim.pitch_trim
        self._last = trim

    def runway_coords(self, s: State) -> tuple[float, float]:
        g = self.geo
        dn, de = s.lat_rad * R_EARTH_M - g["threshold_north_m"], s.lon_rad * R_EARTH_M - g["threshold_east_m"]
        return dn * self._along[0] + de * self._along[1], dn * self._right[0] + de * self._right[1]

    # --- Leg geometry ----------------------------------------------------------------

    def _turn_radius(self, s: State) -> float:
        return s.tas_mps**2 / (G * math.tan(self.g.pattern.max_bank_rad))

    def _profile_height(self, along: float, cross: float) -> float:
        """Target height above the runway: pattern altitude, or the glide path angle along
        the remaining pattern path to the aim point once the descent has started."""
        p, d, aim = self.g.pattern, self.g.pattern.downwind_offset_m, self.geo["aim_point_m"]
        if self.leg == "downwind":
            if along > 0.0:
                return p.pattern_height_m  # until abeam the threshold
            remaining = along + 3 * d + aim
        elif self.leg == "base":
            remaining = abs(cross) + d + aim
        else:  # final
            remaining = aim - along
        return min(p.pattern_height_m, max(0.0, remaining) * self._tan_gp)

    def _line(self, along: float, cross: float) -> tuple[float, float]:
        """(course, signed distance right of the leg line) for the current leg."""
        d, side = self.g.pattern.downwind_offset_m, self.side
        if self.leg in ("takeoff", "final"):
            return self._rwy, cross
        if self.leg == "crosswind":  # toward the pattern side
            return self._rwy + side * math.pi / 2, (along - self._crosswind_along) * side * -1.0
        if self.leg == "downwind":  # opposite to the runway direction
            return self._rwy + math.pi, -(cross - side * d)
        # base: back toward the centreline
        return self._rwy - side * math.pi / 2, (along + d) * side

    def _advance(self, s: State, along: float, cross: float, height: float) -> None:
        p, d = self.g.pattern, self.g.pattern.downwind_offset_m
        r = self._turn_radius(s)
        if self.leg == "takeoff":
            if along > self.geo["length_m"] and height > p.pattern_height_m - p.crosswind_below_m:
                self.leg, self._crosswind_along = "crosswind", along
                self._rudder_bias = self._takeoff._last_rudder
        elif self.leg == "crosswind":
            if cross * self.side > d - r:
                self.leg = "downwind"
        elif self.leg == "downwind":
            if along < -d + r:
                self.leg = "base"
        elif self.leg == "base":
            if cross * self.side < r:
                self.leg = "final"
        elif self.leg == "final":
            track = math.atan2(s.v_east_mps, s.v_north_mps)
            if abs(cross) < p.established_cross_m and abs(wrap_angle_rad(track - self._rwy)) < p.established_track_rad:
                self.leg = "approach"
                self._approach = ApproachAutopilot(
                    self.g.approach, self.geo, self.approach_trim, self.approach_trim_state, self.dt_s, self.wind_report
                )

    # --- Control ---------------------------------------------------------------------

    def __call__(self, s: State, on_ground: bool, touched_down: bool = False, nose_wheel_down: bool = False) -> Controls:
        along, cross = self.runway_coords(s)
        height = s.alt_msl_m - self._elev
        self._advance(s, along, cross, height)
        p, dt = self.g.pattern, self.dt_s
        trim_target = self.trim.pitch_trim if self.leg == "takeoff" else self.approach_trim.pitch_trim
        step = p.pitch_trim_rate * dt
        self._pitch_trim = clamp(trim_target, self._pitch_trim - step, self._pitch_trim + step)
        if self.leg == "takeoff":
            u = self._takeoff(s, on_ground)
        elif self.leg == "approach":
            u = self._approach(s, touched_down, nose_wheel_down)
        else:
            u = self._pattern(s, along, cross, height)
        self._last = u
        return replace(u, pitch_trim=self._pitch_trim)

    def _flaps(self, s: State) -> float:
        p = self.g.pattern
        want = {"crosswind": 0.0, "downwind": p.flaps_abeam if self.runway_coords(s)[0] <= 0.0 else 0.0,
                "base": p.flaps_base, "final": p.flaps_final}[self.leg]  # fmt: skip
        # Within the flap limit speeds (POH): 10 deg up to VFE(10), more only below VFE(full).
        if want > 1 / 3 + 1e-9 and s.cas_mps > p.vfe_full_mps:
            want = 1 / 3
        if want > 0.0 and s.cas_mps > p.vfe_10_mps:
            want = 0.0
        return want

    def _pattern(self, s: State, along: float, cross: float, height: float) -> Controls:
        g, p, dt = self.g, self.g.pattern, self.dt_s
        # Lateral: track onto the leg line, bank-limited; ball-centring rudder.
        course, off = self._line(along, cross)
        track = math.atan2(s.v_east_mps, s.v_north_mps)
        track_cmd = course + math.atan2(-off, p.intercept_m)
        bank_cmd = clamp(p.k_track * wrap_angle_rad(track_cmd - track), -p.max_bank_rad, p.max_bank_rad)
        aileron = self.trim.aileron + p.k_bank * (bank_cmd - s.phi_rad) - p.k_roll_rate * s.p_radps
        if self._rudder_bias is None:
            self._rudder_bias = self._last.rudder
        self._rudder_bias = clamp(self._rudder_bias - p.ki_beta * s.beta_rad * dt, -0.5, 0.5)
        rudder = self._rudder_bias - p.k_beta * s.beta_rad

        # Vertical and speed.
        target_h = p.pattern_height_m if self.leg == "crosswind" else self._profile_height(along, cross)
        speed = {"crosswind": p.climb_cas_mps, "base": p.descent_cas_mps, "final": g.approach.target_kias * KT_TO_MPS}.get(self.leg)
        if speed is None:  # downwind: pattern speed, slower once abeam the threshold
            speed = p.descent_cas_mps if along <= 0.0 else p.downwind_cas_mps
        climbing = target_h - height > p.climb_margin_m
        if climbing != self._climbing:  # bumpless: restart the outer loop from the current attitude
            self._climbing, self._theta_bias = climbing, s.theta_rad
            if self._throttle is None:
                self._throttle = self._last.throttle
        if climbing:  # full power, speed on pitch (too fast: nose up)
            err = s.cas_mps - speed
            self._theta_bias += p.ki_speed_pitch * err * dt
            theta_cmd = self._theta_bias + p.k_speed_pitch * err
            throttle = 1.0
            self._throttle = 1.0
        else:
            ground_speed = math.hypot(s.v_north_mps, s.v_east_mps)
            descending = target_h < p.pattern_height_m - 1.0
            vs_cmd = clamp((-ground_speed * self._tan_gp if descending else 0.0) + p.k_alt * (target_h - height), -p.max_descent_mps, p.max_climb_mps)
            err = vs_cmd + s.v_down_mps  # climb-rate command - climb rate
            self._theta_bias += p.ki_vs * err * dt
            theta_cmd = self._theta_bias + p.k_vs * err
            speed_err = speed - s.cas_mps
            self._throttle = clamp(self._throttle + p.ki_throttle * speed_err * dt, 0.0, 1.0)
            throttle = clamp(self._throttle + p.k_throttle * speed_err, 0.0, 1.0)
        self._theta_bias = clamp(self._theta_bias, p.min_pitch_rad, p.max_pitch_rad)
        theta_cmd = clamp(theta_cmd, p.min_pitch_rad, p.max_pitch_rad)
        theta_cmd = min(theta_cmd, s.theta_rad + g.approach.max_alpha_rad - s.alpha_rad)  # stall protection
        e = s.theta_rad - theta_cmd  # elevator + = nose down
        self._i_pitch = clamp(self._i_pitch + e * dt, -0.3 / max(p.ki_pitch, 1e-9), 0.3 / max(p.ki_pitch, 1e-9))
        elevator = p.k_pitch * e + p.ki_pitch * self._i_pitch + p.k_pitch_rate * s.q_radps
        return replace(
            self.trim, elevator=clamp(elevator, -1, 1), aileron=clamp(aileron, -1, 1), rudder=clamp(rudder, -1, 1),
            throttle=throttle, flaps=self._flaps(s), brake=0.0,
        )  # fmt: skip
