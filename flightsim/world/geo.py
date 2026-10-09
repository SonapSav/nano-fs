"""Where the world sits on the Earth: geodetic latitude/longitude (what JSBSim integrates)
to the flat map the tasks and the viewer use (metres north and east of an origin, the
airfield), and back.

Two models (the env config's `world.geodesy`):

- "wgs84": the WGS84 ellipsoid (JSBSim's Earth) on a transverse Mercator map with true
  scale along the central meridian through the origin. Accurate to about a millimetre
  within 3000 km of the origin's meridian (Krueger's series to third order in n,
  formulas as in Wikipedia, "Universal Transverse Mercator coordinate system",
  "Simplified formulae", retrieved 2026-10-08; see docs/REFERENCES.md). Within the
  ~25 km the tasks use, distances are true to better than 1e-5 and the map is
  conformal; map (grid) north differs from true north by the grid convergence, zero on
  the origin's meridian and everywhere at the equator.
- "sphere": the original mapping (before 2026-10-08): north = lat x 6371 km, east =
  lon x 6371 km, origin at 0, 0. It is about 0.6 % off north-south and 0.1 % east-west
  against WGS84 at the equator. Configs without a `world` block (all logs recorded
  before then) use it, so they replay and re-fly exactly.

The viewer has an identical port in flightsim/viewer/geo.js (tests compare them).
Altitude is not projected: map height = JSBSim's altitude, taken as height above mean
sea level (real terrain is MSL on the EGM2008 geoid; the geoid-ellipsoid difference is
ignored, CLAUDE.md "Positions").
"""

import math
from dataclasses import asdict, dataclass

R_SPHERE_M = 6371000.0  # the "sphere" model's radius (viewer scene.js R_EARTH before 2026-10-08)

# WGS84 (as in the source above): a = 6378.137 km, 1/f = 298.257223563.
WGS84_A_M = 6378137.0
WGS84_F = 1.0 / 298.257223563

_N = WGS84_F / (2.0 - WGS84_F)
_A = WGS84_A_M / (1.0 + _N) * (1.0 + _N**2 / 4.0 + _N**4 / 64.0)
_ALPHA = (_N / 2.0 - 2.0 * _N**2 / 3.0 + 5.0 * _N**3 / 16.0, 13.0 * _N**2 / 48.0 - 3.0 * _N**3 / 5.0, 61.0 * _N**3 / 240.0)
_BETA = (_N / 2.0 - 2.0 * _N**2 / 3.0 + 37.0 * _N**3 / 96.0, _N**2 / 48.0 + _N**3 / 15.0, 17.0 * _N**3 / 480.0)
_DELTA = (2.0 * _N - 2.0 * _N**2 / 3.0 - 2.0 * _N**3, 7.0 * _N**2 / 3.0 - 8.0 * _N**3 / 5.0, 56.0 * _N**3 / 15.0)
_C = 2.0 * math.sqrt(_N) / (1.0 + _N)

MODELS = ("wgs84", "sphere")


def _tm_forward(lat: float, dlon: float) -> tuple[float, float, float]:
    """(northing from the equator, easting from the central meridian, convergence) in
    metres and radians, scale 1 on the central meridian; `dlon` = lon - central meridian."""
    s = math.sin(lat)
    t = math.sinh(math.atanh(s) - _C * math.atanh(_C * s))
    xi_p = math.atan2(t, math.cos(dlon))
    eta_p = math.atanh(math.sin(dlon) / math.sqrt(1.0 + t * t))
    north, east, sigma, tau = xi_p, eta_p, 1.0, 0.0
    for j, a in enumerate(_ALPHA, start=1):
        c2, s2 = math.cos(2 * j * xi_p), math.sin(2 * j * xi_p)
        ch, sh = math.cosh(2 * j * eta_p), math.sinh(2 * j * eta_p)
        north += a * s2 * ch
        east += a * c2 * sh
        sigma += 2 * j * a * c2 * ch
        tau += 2 * j * a * s2 * sh
    tl = math.tan(dlon)
    gamma = math.atan2(tau * math.sqrt(1.0 + t * t) + sigma * t * tl, sigma * math.sqrt(1.0 + t * t) - tau * t * tl)
    return _A * north, _A * east, gamma


def _tm_inverse(north: float, east: float) -> tuple[float, float]:
    """(lat, lon - central meridian) of a northing (from the equator) and an easting."""
    xi, eta = north / _A, east / _A
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, start=1))
    return lat, math.atan2(math.sinh(eta_p), math.cos(xi_p))


@dataclass(frozen=True)
class Geodesy:
    """The world's placement and map. `to_map`/`to_geodetic` convert positions;
    `convergence_rad` turns true directions into map directions:
    map bearing = true bearing - convergence."""

    model: str = "sphere"
    origin_lat_deg: float = 0.0
    origin_lon_deg: float = 0.0

    def __post_init__(self):
        if self.model not in MODELS:
            raise ValueError(f"world.geodesy must be one of {MODELS}, not {self.model!r}")
        if self.model == "sphere" and (self.origin_lat_deg or self.origin_lon_deg):
            raise ValueError("the sphere model (the original mapping) has its origin at 0, 0")
        if not -80.0 <= self.origin_lat_deg <= 80.0:
            raise ValueError("world.origin_lat_deg must be within +/-80 deg")
        lat0, lon0 = math.radians(self.origin_lat_deg), math.radians(self.origin_lon_deg)
        object.__setattr__(self, "_lat0", lat0)
        object.__setattr__(self, "_lon0", lon0)
        object.__setattr__(self, "_north0", _tm_forward(lat0, 0.0)[0] if self.model == "wgs84" else 0.0)

    def to_map(self, lat_rad: float, lon_rad: float) -> tuple[float, float]:
        """(north, east) in metres from the origin."""
        if self.model == "sphere":
            return lat_rad * R_SPHERE_M, lon_rad * R_SPHERE_M
        north, east, _ = _tm_forward(lat_rad, _wrap_pi(lon_rad - self._lon0))
        return north - self._north0, east

    def to_geodetic(self, north_m: float, east_m: float) -> tuple[float, float]:
        """(lat, lon) in radians of a map position."""
        if self.model == "sphere":
            return north_m / R_SPHERE_M, east_m / R_SPHERE_M
        lat, dlon = _tm_inverse(north_m + self._north0, east_m)
        return lat, _wrap_pi(dlon + self._lon0)

    def convergence_rad(self, lat_rad: float, lon_rad: float) -> float:
        """Angle from true north to map north, clockwise (map bearing = true bearing -
        convergence): about (lon - lon0) x sin(lat); 0 for the sphere model."""
        if self.model == "sphere":
            return 0.0
        return _tm_forward(lat_rad, _wrap_pi(lon_rad - self._lon0))[2]

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_config(cls, world: dict | None) -> "Geodesy":
        """From an env config's `world` block; none (logs before 2026-10-08): the sphere."""
        if not world:
            return cls()
        unknown = set(world) - {"geodesy", "origin_lat_deg", "origin_lon_deg", "scenery"}  # scenery: envs/config.py
        if unknown:
            raise ValueError(f"unknown world keys {sorted(unknown)}")
        return cls(str(world.get("geodesy", "wgs84")), float(world.get("origin_lat_deg", 0.0)), float(world.get("origin_lon_deg", 0.0)))


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


SPHERE = Geodesy()  # the original mapping
