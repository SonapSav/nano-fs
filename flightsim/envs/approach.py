"""Gymnasium environment: final approach and landing.

Each episode starts on the extended centreline of the configured runway, on the glide
path (randomized per seed: offset left/right and up/down, airspeed, heading), trimmed in a
steady descent in landing configuration. The pilot (or agent) follows the glide path and
centreline, then lands main wheels first in the touchdown zone. The episode ends:

  landed     with a `rollout` block: stopped on the runway (ground speed below
             `stop_speed_kt`); otherwise all three wheels down for `settle_s` (or
             `max_ground_s` after first contact). Truncated, with a landing bonus minus
             touchdown quality costs
  failed     terminated with `failure_penalty`, reason one of:
               undershoot, off_runway, hard_landing, nose_first, wing_low, side_load (at
               touchdown); off_runway, overrun (past the runway end), no_stop (still
               rolling `max_rollout_s` after the first contact) on the rollout;
               tail_strike, wingtip_strike, nose_strike (structure touches the ground);
               lost_approach (too far off the glide path or centreline); bank, alpha,
               load_factor (the base envelope, while airborne)

Touchdown is judged at the simulation rate (contacts are checked every step), using the
core's contact points: weight on the three wheels from JSBSim, the skids and wingtips
geometrically. Actions, recording, logging and re-flying work as in the base task.

Runway frame: `along` is metres past the threshold in the landing direction, `cross`
metres right of the centreline; the glide path meets the runway at the aim point.
"""

import math
from dataclasses import replace

import gymnasium as gym
import numpy as np

from flightsim.atmosphere.turbulence import wind_at_height_mps
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.core import Controls, InitialConditions, State
from flightsim.envs.altitude_heading import AltitudeHeadingHoldEnv, load_factor
from flightsim.envs.config import FPM_TO_MPS, KT_TO_MPS, EnvConfig
from flightsim.envs.runway import STRIKES, WHEELS, LowAltitudeGusts, Runway, draw_low_altitude_wind, wind_report
from flightsim.world import ground_elevation_m
from flightsim.world.terrain import R_EARTH_M

# Observation name -> scale it is divided by.
APPROACH_OBS_SCALES = {
    "glide_path_dev_m": 30.0,  # + above the glide path
    "centreline_dev_m": 50.0,  # + right of the centreline
    "distance_to_aim_m": 5000.0,
    "height_agl_m": 300.0,
    "speed_error_mps": 5.0,  # target - current (calibrated)
    "climb_rate_mps": 5.0,
    "heading_error_sin": 1.0,  # runway heading - current
    "heading_error_cos": 1.0,
    "phi_rad": 1.0,
    "theta_rad": 1.0,
    "alpha_rad": 0.2,
    "beta_rad": 0.2,
    "p_radps": 1.0,
    "q_radps": 1.0,
    "r_radps": 1.0,
    "on_ground": 1.0,  # 1 once any wheel touches
}
BOUNCE_S = 0.3  # airborne this long after a touchdown counts as a bounce


def approach_geometry(cfg: EnvConfig) -> dict | None:
    """The runway and glide path of an approach task, for displays (None for other tasks).
    Elevation is the ground at the threshold (0 m on flat terrain)."""
    a = cfg.approach
    if a is None:
        return None
    elevation = (
        ground_elevation_m(a.threshold_north_m / R_EARTH_M, a.threshold_east_m / R_EARTH_M) if cfg.terrain == "procedural" else 0.0
    )
    return {
        "threshold_north_m": a.threshold_north_m, "threshold_east_m": a.threshold_east_m,
        "heading_deg": math.degrees(a.runway_heading_rad), "length_m": a.runway_length_m, "width_m": a.runway_width_m,
        "aim_point_m": a.aim_point_m, "glide_path_deg": math.degrees(a.glide_path_rad), "elevation_m": elevation,
        "touchdown_zone_m": list(a.touchdown_zone_m), "target_kias": a.target_cas_mps / KT_TO_MPS,
    }  # fmt: skip


def isa_density_ratio(alt_m: float) -> float:
    """Density / sea-level density in the ISA troposphere."""
    return (1.0 - 2.25577e-5 * alt_m) ** 4.25588


class ApproachLandingEnv(AltitudeHeadingHoldEnv):
    def __init__(self, cfg: EnvConfig, record: bool = False):
        if cfg.approach is None:
            raise ValueError("the approach task needs an `approach` section in its config")
        super().__init__(cfg, record)
        a = cfg.approach
        self.obs_names = (*APPROACH_OBS_SCALES, *(f"prev_{n}" for n in self.action_names))
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(len(self.obs_names),), dtype=np.float32)
        self._scales = np.array([APPROACH_OBS_SCALES.get(n, 1.0) for n in self.obs_names])
        self.runway = Runway(a.threshold_north_m, a.threshold_east_m, a.runway_heading_rad, a.runway_length_m, a.runway_width_m)
        self._along, self._right = self.runway.along_unit, self.runway.right_unit
        self._tan_gp = math.tan(a.glide_path_rad)
        self._geometry = approach_geometry(cfg)

    # --- Runway geometry -----------------------------------------------------------

    def runway_coords(self, s: State | None = None) -> tuple[float, float]:
        """(along, cross): metres past the threshold and right of the centreline."""
        return self.runway.coords(s or self._state)

    def glide_path_deviation_m(self, s: State | None = None) -> float:
        """Height above (+) or below the glide path."""
        s = s or self._state
        along, _ = self.runway_coords(s)
        return s.alt_msl_m - (self.runway_elevation_m + max(0.0, self.cfg.approach.aim_point_m - along) * self._tan_gp)

    # --- Hooks of the base task ------------------------------------------------------

    def _draw_start(self, rng: np.random.Generator) -> tuple[InitialConditions, float, float]:
        a = self.cfg.approach
        # Draw order is part of reproducibility: lateral, vertical, airspeed, heading.
        lateral = rng.uniform(-a.randomize_lateral_m, a.randomize_lateral_m)
        vertical = rng.uniform(-a.randomize_vertical_m, a.randomize_vertical_m)
        kias = a.start_kias + rng.uniform(-a.randomize_kias, a.randomize_kias)
        heading = wrap_angle_rad(a.runway_heading_rad + rng.uniform(-a.randomize_heading_rad, a.randomize_heading_rad))
        self.runway_elevation_m = self._ground_m(a.threshold_north_m / R_EARTH_M, a.threshold_east_m / R_EARTH_M)
        back = a.aim_point_m - a.start_distance_m  # along-runway position of the start (negative: before the threshold)
        north = a.threshold_north_m + back * self._along[0] + lateral * self._right[0]
        east = a.threshold_east_m + back * self._along[1] + lateral * self._right[1]
        alt = self.runway_elevation_m + a.start_distance_m * self._tan_gp + vertical
        tas = kias * KT_TO_MPS / math.sqrt(isa_density_ratio(alt))  # the model has no position error: IAS = CAS
        # Wind (drawn last, and only when configured, so calm episodes keep their draws).
        self.approach_wind = draw_low_altitude_wind(a.wind, a.runway_heading_rad, rng)
        w = self.approach_wind
        wind_n = wind_e = 0.0
        if w is not None:
            # The mean wind at the start height; the shear below it is applied as a gust.
            w["start_mps"] = wind_at_height_mps(w["u20_mps"], alt - self.runway_elevation_m)
            wind_n, wind_e = w["start_mps"] * w["to_north"], w["start_mps"] * w["to_east"]
            # Crab into the wind so the ground track is the drawn heading.
            track = heading
            across = -wind_n * math.sin(track) + wind_e * math.cos(track)
            heading = wrap_angle_rad(track + math.asin(max(-0.9, min(0.9, -across / tas))))
            # The glide path is fixed to the ground: descend at ground speed x tan(3 deg),
            # not airspeed x sin(3 deg) (into a strong headwind the latter cannot be trimmed).
            along_wind = wind_n * math.cos(track) + wind_e * math.sin(track)
            ground_speed = tas * math.cos(wrap_angle_rad(heading - track)) + along_wind
            flight_path = -math.asin(min(0.2, ground_speed * self._tan_gp / tas))
        else:
            flight_path = -a.glide_path_rad
        ic = InitialConditions(
            alt_msl_m=alt,
            tas_mps=tas,
            heading_rad=heading % (2 * math.pi),
            flight_path_rad=flight_path,
            lat_rad=north / R_EARTH_M,
            lon_rad=east / R_EARTH_M,
            wind_north_mps=wind_n,
            wind_east_mps=wind_e,
        )
        self.start_offsets = {"lateral_m": lateral, "vertical_m": vertical, "kias": kias, "heading_deg": math.degrees(heading)}
        return ic, self.runway_elevation_m, a.runway_heading_rad % (2 * math.pi)

    def _start_controls(self) -> Controls:
        return Controls(flaps=self.cfg.approach.start_flaps)

    def _start_core(self, ic: InitialConditions) -> tuple[Controls, State]:
        if self.approach_wind is None:
            return super()._start_core(ic)
        # Trim in calm air with the same air-relative start, then add the steady wind:
        # JSBSim's trim in wind fails for strong headwinds at approach speeds.
        trim, _ = super()._start_core(replace(ic, wind_north_mps=0.0, wind_east_mps=0.0))
        return trim, self._core.add_steady_wind(ic.wind_north_mps, ic.wind_east_mps)

    def _on_reset(self) -> None:
        w = self.approach_wind
        self._gusts = None if w is None else LowAltitudeGusts(w, self.trim_state.tas_mps, 1.0 / self.cfg.sim_rate_hz)
        self.touchdown: dict | None = None  # the first ground contact, judged
        self.failure: str | None = None
        self.bounces = 0
        self._all_down_s = 0.0
        self._airborne_after_touch_s = 0.0
        self._landed = False
        self.nose_wheel_down = False
        self.rollout: dict | None = None  # full-stop task: where and when the aircraft stopped
        self._rollout_max_cross_m = 0.0
        self._prev_sim_state = self._state

    def _sim_step(self, u: Controls) -> State:
        prev = self._state
        if self._gusts is not None:
            # Shear and turbulence as gusts on top of the steady start wind.
            self._core.set_gust_ned_mps(*self._gusts.step(max(0.0, prev.alt_agl_m)))
        s = super()._sim_step(u)
        self._track_ground(prev, s)
        return s

    # --- Touchdown and rollout -------------------------------------------------------

    def _fail(self, reason: str) -> None:
        if self.failure is None:
            self.failure = reason

    def _track_ground(self, prev: State, s: State) -> None:
        a, dt = self.cfg.approach, 1.0 / self.cfg.sim_rate_hz
        contacts = self._core.contacts()
        for point, reason in STRIKES.items():
            if contacts.get(point):
                self._fail(reason)
        wheels = [contacts[w] for w in WHEELS]
        along, cross = self.runway_coords(s)
        on_runway = 0.0 <= along <= a.runway_length_m and abs(cross) <= a.runway_width_m / 2
        if any(wheels):
            if self.touchdown is None:
                sink = max(prev.v_down_mps, s.v_down_mps)
                self.touchdown = {
                    "t_s": s.t_s, "along_m": along, "cross_m": cross, "sink_mps": sink, "cas_mps": s.cas_mps,
                    "bank_deg": math.degrees(s.phi_rad), "pitch_deg": math.degrees(s.theta_rad),
                    "nose_first": contacts["NOSE"] and not (contacts["LEFT_MAIN"] or contacts["RIGHT_MAIN"]),
                    # Crab at touchdown: ground track minus heading (sideways load on the gear).
                    "drift_deg": math.degrees(wrap_angle_rad(math.atan2(s.v_east_mps, s.v_north_mps) - s.psi_rad)),
                    "in_zone": a.touchdown_zone_m[0] <= along <= a.touchdown_zone_m[1],
                }  # fmt: skip
                if along < 0.0:
                    self._fail("undershoot")
                elif not on_runway:
                    self._fail("off_runway")
                elif sink > a.max_sink_mps:
                    self._fail("hard_landing")
                elif self.touchdown["nose_first"]:
                    self._fail("nose_first")
                elif abs(s.phi_rad) > a.max_bank_rad:
                    self._fail("wing_low")
                elif abs(math.radians(self.touchdown["drift_deg"])) > a.max_drift_rad:
                    self._fail("side_load")
            elif along > a.runway_length_m:
                self._fail("overrun")
            elif not on_runway:
                self._fail("off_runway")
            self._rollout_max_cross_m = max(self._rollout_max_cross_m, abs(cross))
            if self._airborne_after_touch_s >= BOUNCE_S:
                self.bounces += 1
            self._airborne_after_touch_s = 0.0
        elif self.touchdown is not None:
            self._airborne_after_touch_s += dt
        self.nose_wheel_down = contacts["NOSE"]
        self._all_down_s = self._all_down_s + dt if all(wheels) else 0.0
        if self.touchdown is None or self._landed:
            return
        if a.stop_speed_mps is None:
            if self._all_down_s >= a.settle_s or s.t_s - self.touchdown["t_s"] >= a.max_ground_s:
                self._landed = True
        elif all(wheels) and math.hypot(s.v_north_mps, s.v_east_mps) < a.stop_speed_mps:
            self._landed = True
            self.rollout = {
                "t_s": s.t_s, "stop_along_m": along, "stop_cross_m": cross,
                "ground_roll_m": along - self.touchdown["along_m"], "max_cross_m": self._rollout_max_cross_m,
            }  # fmt: skip
        elif s.t_s - self.touchdown["t_s"] >= a.max_ground_s:
            self._fail("no_stop")

    # --- Task definition -------------------------------------------------------------

    def errors(self, s: State | None = None) -> tuple[float, float, float]:
        """(glide path m, heading rad, airspeed m/s), each as target - current."""
        s = s or self._state
        return (
            -self.glide_path_deviation_m(s),
            wrap_angle_rad(self.cfg.approach.runway_heading_rad - s.psi_rad),
            self.cfg.approach.target_cas_mps - s.cas_mps,
        )

    def _observation(self) -> np.ndarray:
        s = self._state
        along, cross = self.runway_coords(s)
        _, e_hdg, e_spd = self.errors()
        raw = np.array(
            [
                self.glide_path_deviation_m(s), cross, self.cfg.approach.aim_point_m - along, s.alt_agl_m, e_spd,
                -s.v_down_mps, math.sin(e_hdg), math.cos(e_hdg), s.phi_rad, s.theta_rad, s.alpha_rad, s.beta_rad,
                s.p_radps, s.q_radps, s.r_radps, float(self.touchdown is not None), *self._prev_action,
            ]
        )  # fmt: skip
        return (raw / self._scales).astype(np.float32)

    def _reward(self, action: np.ndarray) -> float:
        rw = self.cfg.approach.reward
        clip = rw["clip"]

        def term(e: float, scale: float) -> float:
            return min((e / scale) ** 2, clip)

        cost = rw["w_action_rate"] * float(np.sum((action - self._prev_action) ** 2))
        if self.touchdown is None:  # track the approach until the first contact
            _, cross = self.runway_coords()
            cost += (
                rw["w_vertical"] * term(self.glide_path_deviation_m(), rw["vertical_scale_m"])
                + rw["w_lateral"] * term(cross, rw["lateral_scale_m"])
                + rw["w_speed"] * term(self.errors()[2], rw["speed_scale_kt"] * KT_TO_MPS)
            )
        self.last_comfort_cost = self._comfort_cost(term)
        return -(cost + self.last_comfort_cost)

    def touchdown_cost(self) -> float:
        """Landing quality: distance from the aim point, sink rate and centreline offset."""
        td, rw = self.touchdown, self.cfg.approach.reward
        if td is None:
            return 0.0
        t, clip = rw["touchdown"], rw["clip"]
        return (
            t["w_distance"] * min(((td["along_m"] - self.cfg.approach.aim_point_m) / t["distance_scale_m"]) ** 2, clip)
            + t["w_sink"] * min((td["sink_mps"] / (t["sink_scale_fpm"] * FPM_TO_MPS)) ** 2, clip)
            + t["w_lateral"] * min((td["cross_m"] / t["lateral_scale_m"]) ** 2, clip)
        )

    def _termination_reason(self) -> str | None:
        if self.failure:
            return self.failure
        if self.touchdown is not None:
            return None  # on the runway: only the ground checks above apply
        s, t, a = self._state, self.cfg.termination, self.cfg.approach
        _, cross = self.runway_coords(s)
        if abs(self.glide_path_deviation_m(s)) > a.lost_vertical_m or abs(cross) > a.lost_lateral_m:
            return "lost_approach"
        if abs(s.phi_rad) > t.max_bank_rad:
            return "bank"
        if s.alpha_rad > t.max_alpha_rad:
            return "alpha"
        if t.max_load_factor is not None and load_factor(s) > t.max_load_factor:
            return "load_factor"
        if t.min_load_factor is not None and load_factor(s) < t.min_load_factor:
            return "load_factor"
        return None

    def step(self, action):
        obs, reward, terminated, truncated, info = super().step(action)
        rw = self.cfg.approach.reward
        if terminated:
            reward -= rw["failure_penalty"]
        elif self._landed:
            reward += rw["landing_bonus"] - self.touchdown_cost()
            truncated = True
        info["landing"] = self.landing_summary()
        return obs, reward, terminated, truncated, info

    def approach_info(self) -> dict:
        """Runway, glide path and this episode's wind (null when calm), for displays."""
        w = self.approach_wind
        wind = None if w is None else {k: w[k] for k in ("u20_mps", "from_deg", "headwind_mps", "crosswind_mps", "turbulence")}
        if wind is not None:
            wind["gust_factor_mps"] = wind_report(w)["gust_factor_mps"]
        return {**self._geometry, "wind": wind}

    def conditions(self) -> dict:
        c = super().conditions()
        w = self.approach_wind
        if w is not None:
            c.update(wind_speed_mps=w["u20_mps"], wind_from_deg=w["from_deg"],
                     turbulence="low_altitude" if w["turbulence"] else "none",
                     turbulence_sigma_mps=0.1 * w["u20_mps"] if w["turbulence"] else 0.0)  # fmt: skip
        return c

    def _info(self) -> dict:
        info = super()._info()
        info["approach"] = self._geometry  # runway and glide path, for controllers and displays
        info["touched_down"] = self.touchdown is not None
        info["nose_wheel_down"] = self.nose_wheel_down
        info["wind_report"] = wind_report(self.approach_wind)
        return info

    def landing_summary(self) -> dict:
        along, cross = self.runway_coords()
        return {
            "glide_path_dev_m": self.glide_path_deviation_m(), "centreline_dev_m": cross,
            "distance_to_threshold_m": -along, "touchdown": self.touchdown, "bounces": self.bounces,
            "landed": self._landed and self.failure is None, "failure": self.failure,
            "touchdown_cost": self.touchdown_cost(), "rollout": self.rollout,
        }  # fmt: skip
