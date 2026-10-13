"""Trees of a real-world region from a canopy height map (the scenery build, visual only):
the region file's `canopy_height` (Meta and WRI 2024, canopy height in whole metres at
~1.2 m in Web Mercator). Per 4 km tile the map is sampled on the 1 m map grid; a tree is
a local maximum of at least `min_tree_m`, the highest within `spacing_m` (plateaus of
equal pixels give one tree, at their centre). Stored in the tile's features file as
"trees": [[x, z, height_m], ...] (world x east, z south); the viewer draws a date palm,
or a low round tree under 5 m, scaled to the height. Lawns, pitches and gardens have no
canopy in the map and get no trees.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import multiprocessing
from pathlib import Path

import numpy as np

from flightsim.world.scenery import TILE_SIZE_M, features_name

WEB_MERCATOR_R = 6378137.0
STRIP_ROWS = 500  # canopy_grid's rows at a time
WORKER_GB = 0.6  # a tile worker's peak memory (measured 2026-10-12: 12 workers 6.3 GB in all; 15.2 GB before the strips)


def canopy_grid(sources: list[Path], geodesy, ix: int, iz: int) -> np.ndarray:
    """The canopy height (m, float32, 4000 x 4000; rows going south) on the tile's 1 m map
    grid, nearest pixel; 0 where no map tile covers."""
    import rasterio
    from rasterio.windows import Window

    from flightsim.world.scenery_build import to_geodetic_arrays

    n = int(TILE_SIZE_M)
    out = np.zeros((n, n), np.float32)
    # Exact every 16 m, bilinear between (as scenery_hires._utm_grid).
    step = 16.0
    m = n // int(step) + 1
    k = np.arange(m) * step
    X, Z = np.meshgrid(ix * TILE_SIZE_M + k, iz * TILE_SIZE_M + k)
    lat, lon = to_geodetic_arrays(geodesy, -Z.ravel(), X.ravel())
    mx = (WEB_MERCATOR_R * np.radians(lon)).reshape(m, m)
    my = (WEB_MERCATOR_R * np.log(np.tan(math.pi / 4 + np.radians(lat) / 2))).reshape(m, m)
    from scipy.ndimage import map_coordinates

    c = (np.arange(n) + 0.5) / step
    files = [rasterio.open(path) for path in sources]
    try:
        # In strips of STRIP_ROWS rows: the whole tile's float64 coordinate grids took ~1 GB
        # a worker (the build ran out of memory on a 16 GB PC; the same values either way).
        for a in range(0, n, STRIP_ROWS):
            rr, cc = np.meshgrid(c[a : a + STRIP_ROWS], c, indexing="ij")
            MX = map_coordinates(mx, [rr, cc], order=1)
            MY = map_coordinates(my, [rr, cc], order=1)
            strip = out[a : a + STRIP_ROWS]
            for ds in files:
                inv = ~ds.transform
                col, row = inv * (MX, MY)
                col, row = np.floor(col).astype(np.int64), np.floor(row).astype(np.int64)
                ok = (col >= 0) & (col < ds.width) & (row >= 0) & (row < ds.height)
                if not ok.any():
                    continue
                r0, r1, c0, c1 = row[ok].min(), row[ok].max() + 1, col[ok].min(), col[ok].max() + 1
                win = ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
                strip[ok] = win[row[ok] - r0, col[ok] - c0]
    finally:
        for ds in files:
            ds.close()
    return out


def find_trees(h: np.ndarray, min_m: float, spacing_m: float) -> np.ndarray:
    """[[col, row, height], ...] of the tree tops in a canopy grid (1 m cells)."""
    from scipy.ndimage import center_of_mass, label, maximum_filter

    size = max(3, int(round(spacing_m * 2)) | 1)
    peak = (h >= min_m) & (h == maximum_filter(h, size=size, mode="constant"))
    lab, n = label(peak)
    if not n:
        return np.zeros((0, 3))
    cm = np.asarray(center_of_mass(peak, lab, np.arange(1, n + 1)))
    rows, cols = cm[:, 0], cm[:, 1]
    hts = h[np.round(rows).astype(int), np.round(cols).astype(int)]
    return np.column_stack([cols, rows, hts])


def cache_name(ix: int, iz: int) -> str:
    """A tile's trees in the build's cache folder (scenery_build: kept between builds, as
    the canopy map rarely changes; merged into the features files)."""
    return f"t_{ix}_{iz}.json"


def _tile(args) -> int:
    cache, ix, iz, sources, geodesy, min_m, spacing = args
    h = canopy_grid(sources, geodesy, ix, iz)
    t = find_trees(h, min_m, spacing)
    trees = [[round(ix * TILE_SIZE_M + c + 0.5, 1), round(iz * TILE_SIZE_M + r + 0.5, 1), round(float(v), 1)] for c, r, v in t]
    part = cache / (cache_name(ix, iz) + ".part")
    part.write_text(json.dumps(trees, separators=(",", ":")))
    part.rename(cache / cache_name(ix, iz))
    return len(t)


def build_trees(spec, out: Path, cache: Path, log=print, workers: int | None = None) -> None:
    """Every tile's trees into `cache` (merge_trees puts them into the features files)."""
    from flightsim.world import geo

    chm = spec.sources["canopy_height"]
    sources = [out / "sources" / f"chm_{t}.tif" for t in chm["tiles"]]
    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    lo, hi = spec.ix_range
    cache.mkdir(parents=True, exist_ok=True)
    jobs = [(cache, ix, iz, sources, g, float(chm["min_tree_m"]), float(chm["spacing_m"])) for iz in range(lo, hi + 1) for ix in range(lo, hi + 1)]
    from flightsim.world.scenery import build_workers

    workers = build_workers(WORKER_GB, workers)
    log(f"trees: {workers} workers")
    with concurrent.futures.ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as ex:
        counts = list(ex.map(_tile, jobs, chunksize=2))
    log(f"trees: {sum(counts)} from the canopy height map")


def merge_trees(spec, out: Path, cache: Path, log=print) -> None:
    """The cached trees into every features file ("trees")."""
    lo, hi = spec.ix_range
    n = 0
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            path = out / features_name(ix, iz)
            if not path.exists():
                continue
            f = json.loads(path.read_text())
            f["trees"] = json.loads((cache / cache_name(ix, iz)).read_text())
            n += len(f["trees"])
            path.write_text(json.dumps(f, separators=(",", ":"), sort_keys=True))
    log(f"trees: {n} into the features")
