"""Gymnasium environment: fly a route of waypoints (GPS navigation).

Each episode starts in cruise at the route's start point, heading along the first leg
(+/- `randomize_heading_deg`), at the route's cruise altitude and the trim airspeed.
Legs are sequenced as a GPS navigator does (envs/route.py: fly-by turn anticipation,
fly-over and last waypoints when passed abeam). The task: stay on the active leg, at the
cruise altitude and the trim airspeed.

Observation: the base task's (altitude, heading against the leg's desired track,
airspeed, attitude and rates) plus the navigation quantities of NAV_OBS_SCALES, then the
previous action. Reward per decision: -(w_alt, w_tas terms of the base task + w_xtk
(cross-track error) + w_track (track angle error) + action rate + comfort), each squared
and clipped as in the base task; a completion bonus at the last waypoint.

Ends: the last waypoint passed (truncated, with the bonus); off_course (further than
`max_xtk_nm` from the active leg) or the base task's limits (terminated); the episode time.
"""

import math
from dataclasses import replace

import numpy as np

from flightsim.core import InitialConditions, State
from flightsim.envs.altitude_heading import OBS_SCALES, AltitudeHeadingHoldEnv
from flightsim.envs.config import EnvConfig
from flightsim.envs.route import Navigator, Route, random_route

NAV_OBS_SCALES = {
    "xtk_m": 500.0,  # + right of the leg
    "track_error_sin": 1.0,  # desired - actual track
    "track_error_cos": 1.0,
    "to_go_m": 5000.0,  # along the leg to its waypoint
    "next_turn_sin": 1.0,  # the turn at the leg's end (+ right)
    "next_turn_cos": 1.0,
}


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class NavigationEnv(AltitudeHeadingHoldEnv):
    def __init__(self, cfg: EnvConfig, record: bool = False):
        if cfg.route is None:
            raise ValueError("the navigation task needs a `route` section in its config")
        super().__init__(cfg, record)
        self.obs_names = (*OBS_SCALES, *NAV_OBS_SCALES, *(f"prev_{a}" for a in self.action_names))
        import gymnasium as gym

        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(len(self.obs_names),), dtype=np.float32)
        self._scales = np.array([{**OBS_SCALES, **NAV_OBS_SCALES}.get(n, 1.0) for n in self.obs_names])

    # --- Start ----------------------------------------------------------------------

    def _draw_start(self, rng: np.random.Generator) -> tuple[InitialConditions, float, float]:
        cfg, r = self.cfg, self.cfg.route
        geo = cfg.geodesy
        # Draw order is part of reproducibility: airspeed, heading offset, then the route.
        tas = cfg.nominal.tas_mps + rng.uniform(-cfg.randomize_tas_mps, cfg.randomize_tas_mps)
        offset = rng.uniform(-r.randomize_heading_rad, r.randomize_heading_rad)
        if r.waypoints is not None:
            self.route = Route(r.start_north_m, r.start_east_m, list(r.waypoints))
        else:
            course = r.start_course_rad if r.start_course_rad is not None else rng.uniform(-math.pi, math.pi)
            q = r.random
            self.route = random_route(rng, r.start_north_m, r.start_east_m, course, tuple(q["waypoints"]), tuple(q["leg_m"]), tuple(q["turn_deg"]))
        self.navigator = Navigator(self.route, r.turn_bank_rad)
        lat, lon = geo.to_geodetic(r.start_north_m, r.start_east_m)
        conv = geo.convergence_rad(lat, lon)
        heading = (self.route.courses[0] + conv + offset) % (2 * math.pi)
        ic = InitialConditions(alt_msl_m=r.cruise_alt_m, tas_mps=tas, heading_rad=heading, lat_rad=lat, lon_rad=lon)
        return ic, r.cruise_alt_m, (self.route.courses[0] + conv) % (2 * math.pi)

    def _on_reset(self) -> None:
        # Turns are planned for the fastest ground speed of a turn with the flight's mean
        # wind (trim TAS + the steady wind speed), fixed for the flight as a flight
        # management system plans with its forecast wind: free of gust noise, and the
        # viewer gets the same value with the route.
        self.turn_speed_mps = self.trim_state.tas_mps + self.wind["speed_mps"]
        self.route_done = False
        self._bonus_paid = False
        self._xtk_sq = self._alt_sq = 0.0
        self._xtk_max = 0.0
        self._samples = 0
        self._nav = self._quantities(self._state)

    # --- Navigation -------------------------------------------------------------------

    def _map_track(self, s: State) -> tuple[float, float, float]:
        """(north, east, track on the map) of a state."""
        geo = self.cfg.geodesy
        north, east = geo.to_map(s.lat_rad, s.lon_rad)
        track = math.atan2(s.v_east_mps, s.v_north_mps) - geo.convergence_rad(s.lat_rad, s.lon_rad)
        return north, east, track

    def _quantities(self, s: State) -> dict:
        north, east, track = self._map_track(s)
        return self.navigator.quantities(north, east, track)

    def _sim_step(self, u) -> State:
        s = super()._sim_step(u)
        north, east, _ = self._map_track(s)
        leg = self.navigator.active
        self.navigator.update(north, east, math.hypot(s.v_north_mps, s.v_east_mps), s.t_s, self.turn_speed_mps)
        if self.navigator.active != leg:
            # The new leg's desired track (true) becomes the heading target.
            conv = self.cfg.geodesy.convergence_rad(s.lat_rad, s.lon_rad)
            self.targets = replace(self.targets, heading_rad=(self.route.courses[self.navigator.active] + conv) % (2 * math.pi))
        self.route_done = self.navigator.done
        return s

    # --- Task -------------------------------------------------------------------------

    def step(self, action):
        obs, reward, terminated, truncated, info = super().step(action)
        self._samples += 1
        self._xtk_sq += self._nav["xtk_m"] ** 2
        self._xtk_max = max(self._xtk_max, abs(self._nav["xtk_m"]))
        self._alt_sq += (self.targets.alt_msl_m - self._state.alt_msl_m) ** 2
        if self.route_done and not terminated:
            truncated = True
            if not self._bonus_paid:
                reward += float(self.cfg.route.reward.get("completion_bonus", 0.0))
                self._bonus_paid = True
        info["route_done"] = self.route_done
        return obs, reward, terminated, truncated, info

    def _observation(self) -> np.ndarray:
        s = self._state
        self._nav = q = self._quantities(s)
        e_alt, e_hdg, e_tas = self.errors()
        raw = np.array(
            [
                e_alt, math.sin(e_hdg), math.cos(e_hdg), e_tas, -s.v_down_mps,
                s.phi_rad, s.theta_rad, s.alpha_rad, s.beta_rad, s.p_radps, s.q_radps, s.r_radps,
                q["xtk_m"], math.sin(q["track_error_rad"]), math.cos(q["track_error_rad"]), q["to_go_m"],
                math.sin(q["next_turn_rad"]), math.cos(q["next_turn_rad"]),
                *self._prev_action,
            ]
        )  # fmt: skip
        return (raw / self._scales).astype(np.float32)

    def _reward(self, action: np.ndarray) -> float:
        r, w = self.cfg.reward, self.cfg.route.reward
        q = self._nav = self._quantities(self._state)
        e_alt, _, e_tas = self.errors()

        def term(e: float, scale: float) -> float:
            return min((e / scale) ** 2, r.clip)

        cost = (
            r.w_alt * term(e_alt, r.alt_scale_m)
            + r.w_tas * term(e_tas, r.tas_scale_mps)
            + float(w.get("w_xtk", 1.0)) * term(q["xtk_m"], float(w.get("xtk_scale_m", 200.0)))
            + float(w.get("w_track", 0.5)) * term(q["track_error_rad"], math.radians(float(w.get("track_scale_deg", 30.0))))
            + r.w_action_rate * float(np.sum((action - self._prev_action) ** 2))
        )
        self.last_comfort_cost = self._comfort_cost(term)
        return -(cost + self.last_comfort_cost)

    def _termination_reason(self) -> str | None:
        reason = super()._termination_reason()
        if reason:
            return reason
        if abs(self._quantities(self._state)["xtk_m"]) > self.cfg.route.max_xtk_m:
            return "off_course"
        return None

    def _info(self) -> dict:
        info = super()._info()
        info["route"] = self.route_info()
        info["nav"] = dict(self._nav) if hasattr(self, "_nav") else None
        return info

    # --- For displays and summaries --------------------------------------------------------

    def route_info(self) -> dict:
        """The route for displays and controllers (map metres; the viewer's nav.js sequences it)."""
        r = self.cfg.route
        return {"name": r.name, **self.route.as_dict(), "turn_bank_deg": math.degrees(r.turn_bank_rad), "turn_speed_mps": self.turn_speed_mps,
                "geodesy": self.cfg.geodesy.as_dict()}  # fmt: skip

    def route_summary(self) -> dict:
        n = max(1, self._samples)
        nav = self.navigator
        return {
            "completed": self.route_done, "legs": self.route.legs, "legs_done": len(nav.sequenced_at),
            "time_s": nav.sequenced_at[-1] if self.route_done else None,
            "length_m": sum(self.route.lengths), "xtk_rms_m": math.sqrt(self._xtk_sq / n), "xtk_max_m": self._xtk_max,
            "alt_rms_m": math.sqrt(self._alt_sq / n),
        }  # fmt: skip

