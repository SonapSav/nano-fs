"""Real-world terrain under the aircraft: the height and water of a built scenery region
(world/scenery.py), shared with the viewer. viewer/demCore.js is a bit-identical port:
both interpolate the same float32 posts with the same double-precision operations in the
same order (tests/test_world_dem.py compares them).

Ground = the bilinear height within the post cell, but never below sea level (0 m): the sea
is a surface at 0 m (FABDEM puts it at or a little below 0). Water = the land cover cell
under the point is WorldCover water (the sea and lakes). Outside the region the ground is
sea level and dry.
"""

import hashlib
import math
from functools import lru_cache

import numpy as np

from flightsim.world.scenery import (
    HEIGHT_CELLS, LANDCOVER_CELLS, POST_M, TILE_SIZE_M, Region, heights_name, landcover_name, load_region,
)  # fmt: skip

SEA_LEVEL_M = 0.0
WATER_CLASS = 80  # WorldCover "permanent water bodies" (scenery.WORLDCOVER)
_LC_CELL_M = TILE_SIZE_M / LANDCOVER_CELLS


class DemTerrain:
    """Height and water of a region at map positions (x = east, z = south, metres)."""

    def __init__(self, region: Region, verify: bool = True):
        self.region = region
        self.heights: dict[tuple[int, int], np.ndarray] = {}
        self.landcover: dict[tuple[int, int], np.ndarray] = {}
        files = region.manifest["files"]
        for ix, iz in region.tiles():
            for name, store, dtype, n in (
                (heights_name(ix, iz), self.heights, "<f4", HEIGHT_CELLS + 1),
                (landcover_name(ix, iz), self.landcover, np.uint8, LANDCOVER_CELLS),
            ):
                raw = (region.path / name).read_bytes()
                if verify and hashlib.sha256(raw).hexdigest() != files[name]:
                    raise ValueError(f"scenery {region.name}: {name} differs from its manifest; rebuild the region")
                store[(ix, iz)] = np.frombuffer(raw, dtype=dtype).reshape(n, n)

    def height_at(self, x: float, z: float) -> float:
        """Ground height (m MSL) at map x (east), z (south)."""
        ix, iz = math.floor(x / TILE_SIZE_M), math.floor(z / TILE_SIZE_M)
        posts = self.heights.get((ix, iz))
        if posts is None:
            return SEA_LEVEL_M
        u = (x - ix * TILE_SIZE_M) / POST_M
        v = (z - iz * TILE_SIZE_M) / POST_M
        i = min(math.floor(u), HEIGHT_CELLS - 1)
        j = min(math.floor(v), HEIGHT_CELLS - 1)
        fx, fz = u - i, v - j
        h00, h01 = float(posts[j, i]), float(posts[j, i + 1])
        h10, h11 = float(posts[j + 1, i]), float(posts[j + 1, i + 1])
        a = h00 + (h01 - h00) * fx
        b = h10 + (h11 - h10) * fx
        h = a + (b - a) * fz
        return h if h > SEA_LEVEL_M else SEA_LEVEL_M

    def water_at(self, x: float, z: float) -> bool:
        ix, iz = math.floor(x / TILE_SIZE_M), math.floor(z / TILE_SIZE_M)
        cells = self.landcover.get((ix, iz))
        if cells is None:
            return False
        i = min(math.floor((x - ix * TILE_SIZE_M) / _LC_CELL_M), LANDCOVER_CELLS - 1)
        j = min(math.floor((z - iz * TILE_SIZE_M) / _LC_CELL_M), LANDCOVER_CELLS - 1)
        return int(cells[j, i]) == WATER_CLASS


@lru_cache(maxsize=4)
def load_terrain(name: str) -> DemTerrain:
    """A region's terrain, loaded (and checked against its manifest) once per process."""
    return DemTerrain(load_region(name))
