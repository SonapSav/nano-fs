"""Gymnasium environment: a circuit (takeoff, traffic pattern, landing) in one episode.

The episode starts like the takeoff task (at rest near the runway 09 threshold, engine
idling) and ends like the approach task with a rollout: stopped on the runway. In
between, the pilot (or agent) flies a traffic pattern of their choosing; the task only
requires climbing at least `min_height_ft` before landing, and staying within
`lost_distance_m` of the runway. The landing is judged exactly as in the approach task
(its `approach` section: runway, glide path, limits, rollout, low-altitude wind).

Failures (terminated, `failure_penalty`):
  departure  off_runway, overrun, no_liftoff (as in the takeoff task); sank_back (a wheel
             touches before `min_height_ft` was reached)
  landing    as in the approach task (undershoot, off_runway, hard_landing, nose_first,
             wing_low, side_load, overrun, no_stop)
  any time   tail/wingtip/nose strike; lost; bank, alpha, load_factor while airborne

The per-step reward has no tracking terms (the pattern is the pilot's choice): action
rate and comfort only, plus the approach task's landing bonus and touchdown cost.

For controllers the info also carries reference trims for the landing configuration
(`approach_trim`, `approach_trim_state`: flaps and speed of the approach task's start,
on the glide path, calm air), computed on a separate core at reset.
"""

import math

import numpy as np

from flightsim.atmosphere.turbulence import wind_at_height_mps
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.core import Controls, InitialConditions, JSBSimCore, State
from flightsim.envs.altitude_heading import load_factor
from flightsim.envs.approach import ApproachLandingEnv, isa_density_ratio
from flightsim.envs.config import KT_TO_MPS, EnvConfig
from flightsim.envs.runway import STRIKES, WHEELS, LowAltitudeGusts, draw_low_altitude_wind
from flightsim.envs.takeoff import FIFTY_FT_M, SANK_BACK_M
from flightsim.world.terrain import R_EARTH_M


class CircuitEnv(ApproachLandingEnv):
    def __init__(self, cfg: EnvConfig, record: bool = False):
        if cfg.circuit is None:
            raise ValueError("the circuit task needs a `circuit` section in its config")
        super().__init__(cfg, record)
        self._geometry = {**self._geometry, "task": "circuit"}

    def height_m(self, s: State | None = None) -> float:
        """Main wheels above the runway (0 when parked)."""
        s = s or self._state
        return s.alt_msl_m - self.runway_elevation_m - self._parked_cg_m

    # --- Start: at rest on the runway (as the takeoff task) ----------------------------

    def _draw_start(self, rng: np.random.Generator) -> tuple[InitialConditions, float, float]:
        c, a = self.cfg.circuit, self.cfg.approach
        # Draw order is part of reproducibility: lateral, heading, then the wind.
        lateral = rng.uniform(-c.randomize_lateral_m, c.randomize_lateral_m)
        heading = wrap_angle_rad(a.runway_heading_rad + rng.uniform(-c.randomize_heading_rad, c.randomize_heading_rad))
        self.runway_elevation_m = self._ground_m(a.threshold_north_m / R_EARTH_M, a.threshold_east_m / R_EARTH_M)
        north, east = self.runway.position(c.start_along_m, lateral)
        self.approach_wind = draw_low_altitude_wind(a.wind, a.runway_heading_rad, rng)
        ic = InitialConditions(
            alt_msl_m=self.runway_elevation_m + 1.4, tas_mps=0.0, heading_rad=heading % (2 * math.pi),
            lat_rad=north / R_EARTH_M, lon_rad=east / R_EARTH_M,
        )  # fmt: skip
        self.start_offsets = {"lateral_m": lateral, "heading_deg": math.degrees(heading)}
        return ic, self.runway_elevation_m + c.min_height_m, a.runway_heading_rad % (2 * math.pi)

    def _start_controls(self) -> Controls:
        return Controls(flaps=self.cfg.circuit.start_flaps, throttle=0.0)

    def _start_core(self, ic: InitialConditions) -> tuple[Controls, State]:
        w, wind_n, wind_e = self.approach_wind, 0.0, 0.0
        if w is not None:
            w["start_mps"] = wind_at_height_mps(w["u20_mps"], ic.alt_msl_m - self.runway_elevation_m)
            wind_n, wind_e = w["start_mps"] * w["to_north"], w["start_mps"] * w["to_east"]
        u = self._start_controls()
        state = self._core.reset_on_ground(
            ic.heading_rad, self.cfg.loading, u, ground_elevation_m=self.runway_elevation_m,
            lat_rad=ic.lat_rad, lon_rad=ic.lon_rad, wind_north_mps=wind_n, wind_east_mps=wind_e,
        )  # fmt: skip
        self.approach_trim, self.approach_trim_state = self._landing_reference()
        return u, state

    def _landing_reference(self) -> tuple[Controls, State]:
        """Trim in the landing configuration on the glide path, calm air, on a separate core."""
        cfg, a = self.cfg, self.cfg.approach
        alt = self.runway_elevation_m + 150.0
        core = JSBSimCore(cfg.aircraft, 1.0 / cfg.sim_rate_hz)
        tas = a.start_kias * KT_TO_MPS / math.sqrt(isa_density_ratio(alt))
        core.reset(InitialConditions(alt, tas, a.runway_heading_rad, flight_path_rad=-a.glide_path_rad), cfg.loading, Controls(flaps=a.start_flaps))
        return core.trim(), core.state()

    def _on_reset(self) -> None:
        super()._on_reset()
        w = self.approach_wind
        # Turbulence time scales at the approach speed (the aircraft starts at rest).
        self._gusts = None if w is None else LowAltitudeGusts(w, self.cfg.approach.target_cas_mps, 1.0 / self.cfg.sim_rate_hz)
        self._parked_cg_m = self.trim_state.alt_msl_m - self.runway_elevation_m
        self.departing = True  # on the ground or below SANK_BACK_M since the start
        self.on_ground = True
        self.liftoff: dict | None = None
        self.fifty_ft: dict | None = None
        self.max_height_m = 0.0
        self.established = False  # climbed min_height_ft: from now on, ground contact is a landing

    # --- Departure, pattern and landing --------------------------------------------------

    def _track_ground(self, prev: State, s: State) -> None:
        c = self.cfg.circuit
        height = self.height_m(s)
        self.max_height_m = max(self.max_height_m, height)
        if self.established:
            super()._track_ground(prev, s)  # landing: judged as in the approach task
            self.on_ground = self.touchdown is not None
            return
        contacts = self._core.contacts()
        for point, reason in STRIKES.items():
            if contacts.get(point):
                self._fail(reason)
        along, cross = self.runway_coords(s)
        self.on_ground = any(contacts[w] for w in WHEELS)
        if self.departing:
            if self.on_ground:
                if along > self.cfg.approach.runway_length_m:
                    self._fail("overrun")
                elif not self.runway.on_surface(along, cross):
                    self._fail("off_runway")
                self.liftoff = None
                if s.t_s > c.max_ground_s:
                    self._fail("no_liftoff")
            elif self.liftoff is None:
                self.liftoff = {
                    "t_s": s.t_s, "along_m": along, "cross_m": cross, "cas_mps": s.cas_mps,
                    "pitch_deg": math.degrees(s.theta_rad), "ground_roll_m": along - c.start_along_m,
                }  # fmt: skip
            if height > SANK_BACK_M:
                self.departing = False
        elif self.on_ground:
            self._fail("sank_back")
        if self.fifty_ft is None and height >= FIFTY_FT_M:
            self.fifty_ft = {"t_s": s.t_s, "along_m": along, "distance_m": along - c.start_along_m, "cas_mps": s.cas_mps}
        if height >= c.min_height_m:
            self.established = True

    def _termination_reason(self) -> str | None:
        if self.failure:
            return self.failure
        if self.on_ground:
            return None  # departure roll or landing rollout: only the ground checks apply
        s, t = self._state, self.cfg.termination
        a = self.cfg.approach
        along, cross = self.runway_coords(s)
        if math.hypot(along - a.runway_length_m / 2, cross) > self.cfg.circuit.lost_distance_m:
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

    def _reward(self, action: np.ndarray) -> float:
        rw = self.cfg.approach.reward
        clip = rw["clip"]

        def term(e: float, scale: float) -> float:
            return min((e / scale) ** 2, clip)

        self.last_comfort_cost = self._comfort_cost(term)
        return -(rw["w_action_rate"] * float(np.sum((action - self._prev_action) ** 2)) + self.last_comfort_cost)

    def step(self, action):
        obs, reward, terminated, truncated, info = super().step(action)
        info["circuit"] = self.circuit_summary()
        return obs, reward, terminated, truncated, info

    def _info(self) -> dict:
        info = super()._info()
        info["on_ground"] = self.on_ground
        info["established"] = self.established
        info["approach_trim"] = self.approach_trim
        info["approach_trim_state"] = self.approach_trim_state
        return info

    def takeoff_summary(self) -> dict:
        """The departure, in the takeoff task's terms ("climbed" = reached min_height_ft)."""
        return {
            "climbed": self.established, "failure": None if self.established else self.failure, "liftoff": self.liftoff,
            "fifty_ft": self.fifty_ft, "skips": 0, "ground_max_cross_m": None, "height_m": self.height_m(),
            "centreline_dev_m": self.runway_coords()[1],
        }  # fmt: skip

    def circuit_summary(self) -> dict:
        return {"established": self.established, "max_height_m": self.max_height_m, "liftoff": self.liftoff, "landing": self.landing_summary()}
