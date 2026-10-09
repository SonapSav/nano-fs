"""The ground the physics flies over, for an env config's `terrain`:

- "flat": 0 m everywhere, no water;
- "procedural": the viewer's procedural terrain (world/terrain.py; its lakes are visual
  only, so no water);
- "dem": a built real-world region (world/dem.py, the config's `world.scenery`), with
  its sea and lakes as water.
"""

from functools import lru_cache

from flightsim.world.geo import Geodesy
from flightsim.world.terrain import ground_elevation_at_m


class Ground:
    def __init__(self, terrain: str, geodesy: Geodesy, scenery: str | None = None):
        self.terrain, self.geodesy = terrain, geodesy
        self.dem = None
        self.scenery = None  # {"name", "hash"} of a real-world region
        if terrain == "dem":
            from flightsim.world.dem import load_terrain

            if not scenery:
                raise ValueError("terrain: dem needs world.scenery (a built region, scripts/build_scenery.py)")
            try:
                self.dem = load_terrain(scenery)
            except FileNotFoundError:
                raise ValueError(f"scenery {scenery!r} is not built here: uv run --group scenery python scripts/build_scenery.py configs/scenery/{scenery}.yaml") from None
            r = self.dem.region
            if geodesy.model != "wgs84" or (geodesy.origin_lat_deg, geodesy.origin_lon_deg) != (r.origin_lat_deg, r.origin_lon_deg):
                raise ValueError(
                    f"world origin {geodesy.origin_lat_deg}, {geodesy.origin_lon_deg} ({geodesy.model}) differs from scenery "
                    f"{r.name}'s {r.origin_lat_deg}, {r.origin_lon_deg} (wgs84)"
                )
            self.scenery = {"name": r.name, "hash": r.scenery_hash}

    def elevation_m(self, lat_rad: float, lon_rad: float) -> float:
        if self.terrain == "flat":
            return 0.0
        north, east = self.geodesy.to_map(lat_rad, lon_rad)
        return self.elevation_at_m(north, east)

    def elevation_at_m(self, north_m: float, east_m: float) -> float:
        if self.terrain == "flat":
            return 0.0
        if self.dem is not None:
            return self.dem.height_at(east_m, -north_m)
        return ground_elevation_at_m(north_m, east_m)

    def water(self, lat_rad: float, lon_rad: float) -> bool:
        if self.dem is None:
            return False
        north, east = self.geodesy.to_map(lat_rad, lon_rad)
        return self.dem.water_at(east, -north)


@lru_cache(maxsize=16)
def _ground(terrain: str, geodesy: Geodesy, scenery: str | None) -> Ground:
    return Ground(terrain, geodesy, scenery)


def ground_of(cfg) -> Ground:
    """The ground of an env config (one per terrain, map and region per process)."""
    return _ground(cfg.terrain, cfg.geodesy, cfg.scenery)
