"""Runway tasks (approach and landing, takeoff): runway frame, contact points and the
low-altitude wind (MIL-F-8785C 3.7.3) shared by the tasks.

Runway frame: `along` is metres past the threshold in the runway direction, `cross`
metres right of the centreline.
"""

import math

import numpy as np

from flightsim.atmosphere.turbulence import LowAltitudeTurbulence, low_altitude_parameters, to_ned, wind_at_height_mps
from flightsim.core import State
from flightsim.envs.config import KT_TO_MPS
from flightsim.world.terrain import R_EARTH_M

WHEELS = ("NOSE", "LEFT_MAIN", "RIGHT_MAIN")
STRIKES = {"TAIL_SKID": "tail_strike", "LEFT_TIP": "wingtip_strike", "RIGHT_TIP": "wingtip_strike", "NOSE_SKID": "nose_strike"}


class Runway:
    def __init__(self, threshold_north_m: float, threshold_east_m: float, heading_rad: float, length_m: float, width_m: float):
        self.threshold_north_m, self.threshold_east_m = threshold_north_m, threshold_east_m
        self.heading_rad, self.length_m, self.width_m = heading_rad, length_m, width_m
        self.along_unit = (math.cos(heading_rad), math.sin(heading_rad))  # (north, east) unit vectors
        self.right_unit = (-math.sin(heading_rad), math.cos(heading_rad))

    def coords(self, s: State) -> tuple[float, float]:
        """(along, cross): metres past the threshold and right of the centreline."""
        dn, de = s.lat_rad * R_EARTH_M - self.threshold_north_m, s.lon_rad * R_EARTH_M - self.threshold_east_m
        return dn * self.along_unit[0] + de * self.along_unit[1], dn * self.right_unit[0] + de * self.right_unit[1]

    def position(self, along: float, cross: float) -> tuple[float, float]:
        """(north, east) in metres of a point in the runway frame."""
        north = self.threshold_north_m + along * self.along_unit[0] + cross * self.right_unit[0]
        east = self.threshold_east_m + along * self.along_unit[1] + cross * self.right_unit[1]
        return north, east

    def on_surface(self, along: float, cross: float) -> bool:
        return 0.0 <= along <= self.length_m and abs(cross) <= self.width_m / 2


def draw_low_altitude_wind(cfg: dict | None, runway_heading_rad: float, rng: np.random.Generator) -> dict | None:
    """Wind at 20 ft and its direction: uniform speed and direction, redrawn while the
    crosswind or tailwind component exceeds its limit (MIL-F-8785C 3.7.3.3 allows
    leaving those out). None (and no draws) when the task has no wind."""
    if cfg is None:
        return None
    lo, hi = (v * KT_TO_MPS for v in cfg["u20_kt"])
    for _ in range(1000):
        u20 = rng.uniform(lo, hi)
        rel = rng.uniform(-math.pi, math.pi)  # wind FROM, relative to the runway heading
        head, cross = u20 * math.cos(rel), u20 * math.sin(rel)
        if abs(cross) <= cfg["max_crosswind_kt"] * KT_TO_MPS and -head <= cfg["max_tailwind_kt"] * KT_TO_MPS:
            break
    from_rad = (runway_heading_rad + rel) % (2 * math.pi)
    return {
        "u20_mps": u20, "from_deg": math.degrees(from_rad), "headwind_mps": head, "crosswind_mps": cross,
        "to_north": -math.cos(from_rad), "to_east": -math.sin(from_rad),
        "turbulence": bool(cfg.get("turbulence", False)), "turbulence_seed": int(rng.integers(2**63)),
    }  # fmt: skip


GUST_PEAK_SIGMAS = 3.0  # project choice: a reported gust is the mean wind plus 3 sigma of the along-wind turbulence
REPORT_HEIGHT_M = 20 * 0.3048


def wind_report(w: dict | None) -> dict | None:
    """What a pilot is told about the wind (as ATIS would): the mean wind at 20 ft, its
    direction, and the gust factor (peak gust minus mean), not the gusts themselves.
    None when calm."""
    if w is None:
        return None
    (sigma_u, _, _), _ = low_altitude_parameters(w["u20_mps"], REPORT_HEIGHT_M)
    gust = GUST_PEAK_SIGMAS * sigma_u if w["turbulence"] else 0.0
    return {"u20_mps": w["u20_mps"], "from_deg": w["from_deg"], "gust_factor_mps": gust}


class LowAltitudeGusts:
    """Gust velocity (NED) on top of the steady start wind `w["start_mps"]`: the shear
    (the mean wind at the current height minus the start wind) plus turbulence aligned
    with the wind, from a wind drawn by `draw_low_altitude_wind`."""

    def __init__(self, w: dict, airspeed_mps: float, dt_s: float):
        self.w = w
        self._turbulence = None
        if w["turbulence"] and w["u20_mps"] > 0:
            self._turbulence = LowAltitudeTurbulence(w["u20_mps"], airspeed_mps, dt_s, np.random.default_rng(w["turbulence_seed"]))

    def step(self, height_m: float) -> tuple[float, float, float]:
        w = self.w
        delta = wind_at_height_mps(w["u20_mps"], height_m) - w["start_mps"]
        gn, ge, gd = delta * w["to_north"], delta * w["to_east"], 0.0
        if self._turbulence is not None:
            tn, te, td = to_ned(*self._turbulence.step(height_m), math.atan2(w["to_east"], w["to_north"]))
            gn, ge, gd = gn + tn, ge + te, td
        return gn, ge, gd
