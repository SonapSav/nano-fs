"""Procedural terrain height: an exact port of flightsim/viewer/terrain.js `height()`.

The viewer draws this terrain and the physics lands on it, so the two must agree to the
last bit: the same seeded integer hash (JavaScript's 32-bit integer semantics), the same
floating-point operations in the same order, and only correctly rounded functions
(sqrt, floor). tests/test_world_terrain.py compares both on a grid of points.

Frames: the viewer's world x = east, z = south (metres), origin at latitude/longitude
(0, 0), mapped with the viewer's spherical radius R_EARTH_M.
"""

import math

WORLD_SEED = 172  # terrain.js WORLD_SEED
WATER_LEVEL_M = -0.5  # terrain.js WATER_LEVEL_M: lakes are flat water at this level
R_EARTH_M = 6371000.0  # scene.js R_EARTH
AIRFIELD_FLAT_RADIUS_M = 1400.0  # terrain.js AIRFIELD.flatRadiusM (airfield at x = z = 0)
_MASK = 0xFFFFFFFF


def _to_int32(v: float) -> int:
    """JavaScript ToInt32 of a double."""
    n = int(math.trunc(v)) & _MASK
    return n - (1 << 32) if n >= (1 << 31) else n


def _hash2(ix: int, iz: int, seed: int = WORLD_SEED) -> float:
    h = _to_int32(float(ix) * 374761393.0 + float(iz) * 668265263.0 + float(seed) * 2147483647.0)
    u = h & _MASK
    u = ((u ^ (u >> 13)) * 1274126177) & _MASK  # Math.imul
    u ^= u >> 16
    return u / 4294967296.0


def _value_noise(x: float, z: float, seed: int) -> float:
    ix, iz = math.floor(x), math.floor(z)
    fx, fz = x - ix, z - iz
    sx, sz = fx * fx * (3 - 2 * fx), fz * fz * (3 - 2 * fz)
    a, b = _hash2(ix, iz, seed), _hash2(ix + 1, iz, seed)
    c, d = _hash2(ix, iz + 1, seed), _hash2(ix + 1, iz + 1, seed)
    return a + (b - a) * sx + (c - a) * sz + (a - b - c + d) * sx * sz


def _fbm(x: float, z: float, octaves: int, seed: int) -> float:
    total, amp, freq, norm = 0.0, 0.5, 1.0, 0.0
    for o in range(octaves):
        total += amp * _value_noise(x * freq, z * freq, seed + o * 101)
        norm += amp
        amp *= 0.5
        freq *= 2.03
    return total / norm


def _smoothstep(a: float, b: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def _lakeness(x: float, z: float) -> float:
    return 1 - _smoothstep(0.24, 0.31, _fbm(x / 3000, z / 3000, 3, 7))


def height_m(x: float, z: float) -> float:
    """Terrain height (m) at viewer world x (east) and z (south), as terrain.js draws it."""
    n = _fbm(x / 7000, z / 7000, 5, 1)
    t = max(0.0, (n - 0.42) / 0.58)
    h = t * math.sqrt(t) * 350
    h -= _lakeness(x, z) * 25
    r = math.sqrt(x * x + z * z)
    return h * _smoothstep(AIRFIELD_FLAT_RADIUS_M, AIRFIELD_FLAT_RADIUS_M + 1200, r)


def ground_elevation_m(lat_rad: float, lon_rad: float) -> float:
    """Surface the aircraft can touch: terrain, or the water surface over lakes."""
    north, east = lat_rad * R_EARTH_M, lon_rad * R_EARTH_M  # the viewer's mapping (origin 0, 0)
    return max(height_m(east, -north), WATER_LEVEL_M)
