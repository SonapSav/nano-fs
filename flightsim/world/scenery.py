"""Real-world scenery regions built from open data (scripts/build_scenery.py; CLAUDE.md
Decisions, "Real-world scenery"): the tile grid and the files a built region holds. This
module needs only numpy, so the physics and the stream server can use it; the build
itself (rasterio, osmium) is in scenery_build.py.

A region sits on the "wgs84" map (world/geo.py) around its origin. It is cut into the
viewer's 4 km terrain tiles (`TILE_SIZE_M`, terrainCore.js): tile (ix, iz) covers
east x in [ix, ix + 1) x 4 km and south z in [iz, iz + 1) x 4 km (z = -north, the
viewer's world frame). Per tile:

- heights: (HEIGHT_CELLS + 1)^2 posts every POST_M metres, float32 little-endian,
  row-major with rows going south (z) and columns east (x); edge posts repeat the
  neighbour's. Metres above mean sea level (EGM2008), bare earth.
- land cover: LANDCOVER_CELLS^2 cells, uint8 ESA WorldCover classes (WORLDCOVER), the
  class at each cell's centre, same row order.
- shore distance: LANDCOVER_CELLS^2 uint8, per land cover cell: 0 on land, else 1 + the
  distance to the nearest land in SHORE_STEP_M steps (at most 254: ~2 km), over the whole
  region (the viewer's shallow water colour). Visual only.
- imagery (optional): IMAGERY_PX^2 JPEG, natural colour from Sentinel-2 at ~10 m, rows
  going south as the other files. Visual only.
- features (JSON, OpenStreetMap; world/scenery_osm.extract_features): roads by class,
  railways and taxiways (polylines cut at the tile's edges), aprons and buildings
  (footprints, with a height and its source) whose centroid is in the tile; world x
  (east), z (south) metres.

`manifest.json` lists the region, its sources (with licences and attribution) and every
file's sha256; `scenery_hash` is the hash of the manifest, which logs record.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

TILE_SIZE_M = 4000.0  # terrainCore.js TILE_SIZE_M
HEIGHT_CELLS = 128  # height cells per tile side (129 posts)
POST_M = TILE_SIZE_M / HEIGHT_CELLS  # 31.25 m, about FABDEM's 1 arc-second
LANDCOVER_CELLS = 256  # land cover cells per tile side (15.625 m, WorldCover is 10 m)
FORMAT = 1  # bump when the files change meaning
SHORE_STEP_M = 8.0  # shore distance file resolution
IMAGERY_PX = 400  # imagery pixels per tile side (10 m, Sentinel-2's resolution)

SCENERY_DIR = Path(__file__).resolve().parents[2] / "data" / "scenery"

# ESA WorldCover v200 map classes (Earth Engine catalogue ESA/WorldCover/v200, retrieved
# 2026-10-09; docs/REFERENCES.md).
WORLDCOVER = {
    10: "tree_cover", 20: "shrubland", 30: "grassland", 40: "cropland", 50: "built_up",
    60: "bare_sparse", 70: "snow_ice", 80: "water", 90: "herbaceous_wetland", 95: "mangroves",
    100: "moss_lichen",
}  # fmt: skip


def heights_name(ix: int, iz: int) -> str:
    return f"tiles/h_{ix}_{iz}.f32"


def landcover_name(ix: int, iz: int) -> str:
    return f"tiles/lc_{ix}_{iz}.u8"


def shore_name(ix: int, iz: int) -> str:
    return f"tiles/w_{ix}_{iz}.u8"


def imagery_name(ix: int, iz: int) -> str:
    return f"tiles/i_{ix}_{iz}.jpg"


def features_name(ix: int, iz: int) -> str:
    return f"tiles/f_{ix}_{iz}.json"


@dataclass(frozen=True)
class Region:
    """A built region (its manifest)."""

    name: str
    origin_lat_deg: float
    origin_lon_deg: float
    ix_min: int
    ix_max: int  # inclusive
    iz_min: int
    iz_max: int
    manifest: dict
    path: Path

    @property
    def scenery_hash(self) -> str:
        return manifest_hash(self.manifest)

    def tiles(self):
        for iz in range(self.iz_min, self.iz_max + 1):
            for ix in range(self.ix_min, self.ix_max + 1):
                yield ix, iz


def manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_region(name: str, root: Path = SCENERY_DIR) -> Region:
    path = root / name
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("format") != FORMAT:
        raise ValueError(f"scenery {name}: format {manifest.get('format')}, this code reads {FORMAT}; rebuild it")
    t = manifest["tiles"]
    return Region(
        name=manifest["name"], origin_lat_deg=manifest["origin_lat_deg"], origin_lon_deg=manifest["origin_lon_deg"],
        ix_min=t["ix_min"], ix_max=t["ix_max"], iz_min=t["iz_min"], iz_max=t["iz_max"], manifest=manifest, path=path,
    )  # fmt: skip
