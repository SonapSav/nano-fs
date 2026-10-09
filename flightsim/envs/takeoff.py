"""Gymnasium environment: takeoff and climb-out.

Each episode starts at rest on the runway centreline near the threshold (randomized per
seed: a small lateral offset and heading), engine idling, brakes off, flaps as
configured. The pilot (or agent) applies power, keeps the centreline, lifts off and
climbs out on the runway heading at the climb speed. The episode ends:

  climbed    `target_height_ft` above the runway: truncated, with a climb bonus
  failed     terminated with `failure_penalty`, reason one of:
               off_runway, overrun (a wheel on the ground off the side or past the end);
               no_liftoff (still on the ground after `max_ground_s`);
               sank_back (a wheel touches again after climbing above SANK_BACK_M);
               tail_strike, wingtip_strike, nose_strike (structure touches the ground);
               lost (too far off the extended centreline once airborne); bank, alpha,
               load_factor (the base envelope, once airborne)

Ground contact is judged at the simulation rate, as in the approach task. A wind, when
configured, is the approach task's low-altitude wind (MIL-F-8785C 3.7.3): steady at the
start (it builds up while the aircraft sits on its brakes before the episode), with the
shear and turbulence applied as gusts.
"""

import math

import gymnasium as gym
import numpy as np

from flightsim.atmosphere.turbulence import wind_at_height_mps
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.core import Controls, InitialConditions, State
from flightsim.envs.altitude_heading import AltitudeHeadingHoldEnv, load_factor
from flightsim.envs.config import EnvConfig
from flightsim.envs.runway import STRIKES, WHEELS, LowAltitudeGusts, Runway, draw_low_altitude_wind, wind_report
from flightsim.world.ground import ground_of

# Observation name -> scale it is divided by.
TAKEOFF_OBS_SCALES = {
    "centreline_dev_m": 30.0,  # + right of the centreline
    "runway_remaining_m": 1000.0,  # to the runway end (negative past it)
    "height_m": 300.0,  # wheels above the runway
    "speed_error_mps": 10.0,  # climb speed - current (calibrated)
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
    "on_ground": 1.0,  # 1 while any wheel touches
}
FIFTY_FT_M = 15.24  # the POH's obstacle height
SANK_BACK_M = 5.0  # wheels this high, then back on the ground: a failure (lower: a skip)


def takeoff_geometry(cfg: EnvConfig) -> dict | None:
    """The runway of a takeoff task, for displays (None for other tasks). Elevation is
    the ground at the threshold (0 m on flat terrain)."""
    t = cfg.takeoff
    if t is None:
        return None
    elevation = (
        ground_of(cfg).elevation_m(*cfg.geodesy.to_geodetic(t.threshold_north_m, t.threshold_east_m))
    )
    return {
        "task": "takeoff",
        "threshold_north_m": t.threshold_north_m, "threshold_east_m": t.threshold_east_m,
        "heading_deg": math.degrees(t.runway_heading_rad), "length_m": t.runway_length_m, "width_m": t.runway_width_m,
        "elevation_m": elevation, "start_along_m": t.start_along_m, "target_height_m": t.target_height_m,
        "geodesy": cfg.geodesy.as_dict(),
    }  # fmt: skip


class TakeoffEnv(AltitudeHeadingHoldEnv):
    def __init__(self, cfg: EnvConfig, record: bool = False):
        if cfg.takeoff is None:
            raise ValueError("the takeoff task needs a `takeoff` section in its config")
        super().__init__(cfg, record)
        t = cfg.takeoff
        self.obs_names = (*TAKEOFF_OBS_SCALES, *(f"prev_{n}" for n in self.action_names))
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(len(self.obs_names),), dtype=np.float32)
        self._scales = np.array([TAKEOFF_OBS_SCALES.get(n, 1.0) for n in self.obs_names])
        self.runway = Runway(t.threshold_north_m, t.threshold_east_m, t.runway_heading_rad, t.runway_length_m, t.runway_width_m, cfg.geodesy)
        self._geometry = takeoff_geometry(cfg)

    def runway_coords(self, s: State | None = None) -> tuple[float, float]:
        """(along, cross): metres past the threshold and right of the centreline."""
        return self.runway.coords(s or self._state)

    def height_m(self, s: State | None = None) -> float:
        """Main wheels above the runway (0 when parked)."""
        s = s or self._state
        return s.alt_msl_m - self.runway_elevation_m - self._parked_cg_m

    # --- Hooks of the base task ------------------------------------------------------

    def _draw_start(self, rng: np.random.Generator) -> tuple[InitialConditions, float, float]:
        t = self.cfg.takeoff
        # Draw order is part of reproducibility: lateral, heading, then the wind.
        lateral = rng.uniform(-t.randomize_lateral_m, t.randomize_lateral_m)
        heading = wrap_angle_rad(t.runway_heading_rad + rng.uniform(-t.randomize_heading_rad, t.randomize_heading_rad))
        self.runway_elevation_m = self._ground_m(*self.cfg.geodesy.to_geodetic(t.threshold_north_m, t.threshold_east_m))
        north, east = self.runway.position(t.start_along_m, lateral)
        start_lat, start_lon = self.cfg.geodesy.to_geodetic(north, east)
        self.takeoff_wind = draw_low_altitude_wind(t.wind, t.runway_heading_rad, rng)
        ic = InitialConditions(
            alt_msl_m=self.runway_elevation_m + 1.4, tas_mps=0.0, heading_rad=heading % (2 * math.pi),
            lat_rad=start_lat, lon_rad=start_lon,
        )  # fmt: skip
        self.start_offsets = {"lateral_m": lateral, "heading_deg": math.degrees(heading)}
        return ic, self.runway_elevation_m + t.target_height_m, t.runway_heading_rad % (2 * math.pi)

    def _start_controls(self) -> Controls:
        return Controls(flaps=self.cfg.takeoff.start_flaps, throttle=0.0)

    def _start_core(self, ic: InitialConditions) -> tuple[Controls, State]:
        """At rest on the wheels, engine idling; the wind (at the parked CG height) builds up
        while the aircraft waits on its brakes. The "trim" is the start controls."""
        w, wind_n, wind_e = self.takeoff_wind, 0.0, 0.0
        if w is not None:
            w["start_mps"] = wind_at_height_mps(w["u20_mps"], ic.alt_msl_m - self.runway_elevation_m)
            wind_n, wind_e = w["start_mps"] * w["to_north"], w["start_mps"] * w["to_east"]
        u = self._start_controls()
        state = self._core.reset_on_ground(
            ic.heading_rad, self.cfg.loading, u, ground_elevation_m=self.runway_elevation_m,
            lat_rad=ic.lat_rad, lon_rad=ic.lon_rad, wind_north_mps=wind_n, wind_east_mps=wind_e,
        )  # fmt: skip
        return u, state

    def _on_reset(self) -> None:
        w, t = self.takeoff_wind, self.cfg.takeoff
        self._gusts = None if w is None else LowAltitudeGusts(w, t.climb_cas_mps, 1.0 / self.cfg.sim_rate_hz)
        self._parked_cg_m = self.trim_state.alt_msl_m - self.runway_elevation_m
        self.failure: str | None = None
        self.liftoff: dict | None = None  # the last time all wheels left the ground
        self.skips = 0  # lift-offs followed by a touch below SANK_BACK_M
        self.on_ground = True
        self.fifty_ft: dict | None = None  # first time the wheels were 50 ft above the runway
        self._max_height_m = 0.0
        self._ground_max_cross_m = 0.0
        self._climbed = False

    def _sim_step(self, u: Controls) -> State:
        prev = self._state
        if self._gusts is not None:
            self._core.set_gust_ned_mps(*self._gusts.step(max(0.0, prev.alt_agl_m)))
        s = super()._sim_step(u)
        self._track(s)
        return s

    # --- Ground contact, lift-off and climb ------------------------------------------

    def _fail(self, reason: str) -> None:
        if self.failure is None:
            self.failure = reason

    def _track(self, s: State) -> None:
        t = self.cfg.takeoff
        contacts = self._core.contacts()
        for point, reason in STRIKES.items():
            if contacts.get(point):
                self._fail(reason)
        along, cross = self.runway_coords(s)
        height = self.height_m(s)
        self._max_height_m = max(self._max_height_m, height)
        self.on_ground = any(contacts[w] for w in WHEELS)
        if self.on_ground:
            self._ground_max_cross_m = max(self._ground_max_cross_m, abs(cross))
            if along > t.runway_length_m:
                self._fail("overrun")
            elif not self.runway.on_surface(along, cross):
                self._fail("off_runway")
            if self._max_height_m > SANK_BACK_M:
                self._fail("sank_back")
            elif self.liftoff is not None:
                self.liftoff = None
                self.skips += 1
            if s.t_s > t.max_ground_s:
                self._fail("no_liftoff")
        elif self.liftoff is None:
            self.liftoff = {
                "t_s": s.t_s, "along_m": along, "cross_m": cross, "cas_mps": s.cas_mps,
                "pitch_deg": math.degrees(s.theta_rad), "ground_roll_m": along - t.start_along_m,
            }  # fmt: skip
        if self.fifty_ft is None and height >= FIFTY_FT_M:
            self.fifty_ft = {"t_s": s.t_s, "along_m": along, "distance_m": along - t.start_along_m, "cas_mps": s.cas_mps}
        if s.alt_msl_m - self.runway_elevation_m >= t.target_height_m:
            self._climbed = True

    # --- Task definition -------------------------------------------------------------

    def errors(self, s: State | None = None) -> tuple[float, float, float]:
        """(altitude to the target m, heading rad, climb speed m/s), each as target - current."""
        s = s or self._state
        return (
            self.targets.alt_msl_m - s.alt_msl_m,
            wrap_angle_rad(self.cfg.takeoff.runway_heading_rad - s.psi_rad),
            self.cfg.takeoff.climb_cas_mps - s.cas_mps,
        )

    def _observation(self) -> np.ndarray:
        s = self._state
        along, cross = self.runway_coords(s)
        _, e_hdg, e_spd = self.errors()
        raw = np.array(
            [
                cross, self.cfg.takeoff.runway_length_m - along, self.height_m(s), e_spd, -s.v_down_mps,
                math.sin(e_hdg), math.cos(e_hdg), s.phi_rad, s.theta_rad, s.alpha_rad, s.beta_rad,
                s.p_radps, s.q_radps, s.r_radps, float(self.on_ground), *self._prev_action,
            ]
        )  # fmt: skip
        return (raw / self._scales).astype(np.float32)

    def _reward(self, action: np.ndarray) -> float:
        rw = self.cfg.takeoff.reward
        clip = rw["clip"]

        def term(e: float, scale: float) -> float:
            return min((e / scale) ** 2, clip)

        _, cross = self.runway_coords()
        cost = rw["w_action_rate"] * float(np.sum((action - self._prev_action) ** 2))
        cost += rw["w_lateral"] * term(cross, rw["lateral_scale_m"])
        if not self.on_ground:  # the climb speed matters once airborne
            cost += rw["w_speed"] * term(self.errors()[2], rw["speed_scale_kt"] * 1852.0 / 3600.0)
        self.last_comfort_cost = self._comfort_cost(term)
        return -(cost + self.last_comfort_cost)

    def _termination_reason(self) -> str | None:
        if self.failure:
            return self.failure
        if self.on_ground:
            return None  # only the ground checks above apply
        s, t = self._state, self.cfg.termination
        _, cross = self.runway_coords(s)
        if abs(cross) > self.cfg.takeoff.lost_lateral_m:
            return "lost"
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
        rw = self.cfg.takeoff.reward
        if terminated:
            reward -= rw["failure_penalty"]
        elif self._climbed:
            reward += rw["climb_bonus"]
            truncated = True
        info["takeoff"] = self.takeoff_summary()
        return obs, reward, terminated, truncated, info

    def runway_info(self) -> dict:
        """Runway and this episode's wind (null when calm), for displays."""
        w = self.takeoff_wind
        wind = None if w is None else {k: w[k] for k in ("u20_mps", "from_deg", "headwind_mps", "crosswind_mps", "turbulence")}
        if wind is not None:
            wind["gust_factor_mps"] = wind_report(w)["gust_factor_mps"]
        return {**self._geometry, "wind": wind}

    def conditions(self) -> dict:
        c = super().conditions()
        w = self.takeoff_wind
        if w is not None:
            c.update(wind_speed_mps=w["u20_mps"], wind_from_deg=w["from_deg"],
                     turbulence="low_altitude" if w["turbulence"] else "none",
                     turbulence_sigma_mps=0.1 * w["u20_mps"] if w["turbulence"] else 0.0)  # fmt: skip
        return c

    def _info(self) -> dict:
        info = super()._info()
        info["runway"] = self._geometry  # for controllers and displays
        info["on_ground"] = self.on_ground
        info["wind_report"] = wind_report(self.takeoff_wind)
        return info

    def takeoff_summary(self) -> dict:
        _, cross = self.runway_coords()
        return {
            "climbed": self._climbed and self.failure is None, "failure": self.failure, "liftoff": self.liftoff,
            "fifty_ft": self.fifty_ft, "skips": self.skips, "ground_max_cross_m": self._ground_max_cross_m,
            "height_m": self.height_m(), "centreline_dev_m": cross,
        }  # fmt: skip
