"""Route autopilot for the navigation task: lateral navigation (LNAV) on top of the PID
autopilot's altitude, airspeed and heading loops.

Guidance on the map (envs/route.py frame): the commanded track is the active leg's
desired track turned toward the leg by an intercept angle atan(xtk / L), at most
`max_intercept_deg`, with the look-ahead L = max(min_lookahead_m, ground speed x
lookahead_s), so the aircraft closes the cross-track error smoothly and flies the line.
The heading command adds the current drift (heading - track, low-pass filtered), so a
crosswind is corrected by crabbing, and in a turn the bank the arc needs (feed-forward,
passed to the heading loop as a heading offset of bank / k_heading). Leg changes come from the task's sequencing (fly-by
turn anticipation), so turns start early and roll out on the next leg.
"""

import math
from dataclasses import dataclass, replace
from pathlib import Path

from flightsim.config import load_raw
from flightsim.control.autopilot import Autopilot, AutopilotGains, Targets, autopilot_gains_from_raw
from flightsim.core import Controls, State
from flightsim.world.geo import Geodesy


@dataclass(frozen=True)
class RouteGains:
    inner: AutopilotGains  # the PID autopilot (configs/autopilot.yaml, inlined)
    lookahead_s: float
    min_lookahead_m: float
    max_intercept_rad: float
    drift_time_constant_s: float


def load_route_raw(path: str | Path) -> dict:
    """The route autopilot config with its inner autopilot file inlined (so the config hash
    covers every gain)."""
    raw = load_raw(path)
    return {**raw, "inner": load_raw(Path(path).parent / raw["inner"])}


def route_gains_from_raw(raw: dict) -> RouteGains:
    return RouteGains(
        inner=autopilot_gains_from_raw(raw["inner"]),
        lookahead_s=float(raw["lookahead_s"]),
        min_lookahead_m=float(raw["min_lookahead_m"]),
        max_intercept_rad=math.radians(float(raw["max_intercept_deg"])),
        drift_time_constant_s=float(raw["drift_time_constant_s"]),
    )


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class RouteAutopilot:
    def __init__(self, gains: RouteGains, trim: Controls, trim_state: State, targets: Targets, geodesy: Geodesy, dt_s: float):
        self.g, self.geodesy, self.dt_s = gains, geodesy, dt_s
        self._inner = Autopilot(gains.inner, trim, trim_state.theta_rad, targets, dt_s)
        self._drift = 0.0

    def track_command_map(self, nav: dict, ground_speed_mps: float) -> float:
        """Commanded track on the map: the leg's track turned toward the leg (xtk + right)."""
        g = self.g
        lookahead = max(g.min_lookahead_m, ground_speed_mps * g.lookahead_s)
        intercept = max(-g.max_intercept_rad, min(g.max_intercept_rad, math.atan2(nav["xtk_m"], lookahead)))
        return nav["dtk_map_rad"] - intercept

    def __call__(self, s: State, nav: dict, targets: Targets) -> Controls:
        gs = math.hypot(s.v_north_mps, s.v_east_mps)
        if gs > 10.0:  # drift = heading - track (true), filtered
            drift = _wrap_pi(s.psi_rad - math.atan2(s.v_east_mps, s.v_north_mps))
            self._drift += (drift - self._drift) * min(1.0, self.dt_s / self.g.drift_time_constant_s)
        conv = self.geodesy.convergence_rad(s.lat_rad, s.lon_rad)
        heading_cmd = self.track_command_map(nav, gs) + conv + self._drift
        if nav.get("turning"):
            # Feed-forward: the bank the arc needs (for the heading loop: a heading offset of
            # bank / k_heading), so the turn starts with the arc instead of after a lag.
            bank_ff = math.atan(gs * gs / (9.80665 * nav["arc_radius_m"]))
            heading_cmd += nav["arc_side"] * bank_ff / self.g.inner.heading.k_heading
        heading_cmd %= 2 * math.pi
        self._inner.targets = replace(targets, heading_rad=heading_cmd)
        self._inner._heading.target_heading_rad = heading_cmd
        return self._inner(s)
