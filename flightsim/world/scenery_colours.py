"""Building colours from a region's imagery (the scenery build, visual only): each OSM
building's roof colour is the median of the imagery inside its footprint: the 1 m
imagery's chunks (scenery_hires.py) where built, else the tiles' 10 m Sentinel imagery
upsampled. Stored as a fourth value of the building in its features file (0xRRGGBB, the
imagery's sRGB); the viewer (featureGeometry.js) colours the roof with it and the walls
with a lighter blend of it.

Towers (at least TOWER_M) are left out: the imagery sees them from up to ~23 degrees off
nadir, so their roofs lie tens of metres beside the footprint. Lower buildings lean a few
metres; the median keeps the roof's colour where most of the footprint still sees it.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import multiprocessing
import warnings
from pathlib import Path

import numpy as np

from flightsim.world.scenery import TILE_SIZE_M, features_name, imagery_name

TOWER_M = 40.0
MIN_PIXELS = 4  # footprints smaller than this (at 1 m) take the pixel under their centre


def _read(path: Path) -> np.ndarray:
    import rasterio
    import rasterio.errors

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with rasterio.open(path) as ds:
            return ds.read()


def tile_picture(out: Path, ix: int, iz: int, hires: dict | None) -> np.ndarray:
    """The 4 km tile's imagery at 1 m (3, 4000, 4000) uint8: the 1 m chunks where there
    are, the Sentinel tile (nearest, upsampled) elsewhere."""
    n = int(TILE_SIZE_M)
    base = _read(out / imagery_name(ix, iz))
    k = n // base.shape[1]
    img = np.repeat(np.repeat(base, k, axis=1), k, axis=2)
    if hires:
        lv = hires["levels"][0]
        cm = float(lv["chunk_m"])
        x0, z0 = ix * TILE_SIZE_M, iz * TILE_SIZE_M
        for cz in range(math.floor(z0 / cm), math.ceil((z0 + n) / cm)):
            for cx in range(math.floor(x0 / cm), math.ceil((x0 + n) / cm)):
                if f"{cx},{cz}" not in lv["chunks"]:
                    continue
                chunk = _read(out / "hires" / "l0" / f"c_{cx}_{cz}.jpg")
                # Overlap of the chunk and the tile, in metres (1 m pixels).
                ax0, ax1 = max(cx * cm, x0), min((cx + 1) * cm, x0 + n)
                az0, az1 = max(cz * cm, z0), min((cz + 1) * cm, z0 + n)
                img[:, int(az0 - z0) : int(az1 - z0), int(ax0 - x0) : int(ax1 - x0)] = chunk[
                    :, int(az0 - cz * cm) : int(az1 - cz * cm), int(ax0 - cx * cm) : int(ax1 - cx * cm)
                ]
    return img


def roof_colours(buildings: list, img: np.ndarray, x0: float, z0: float) -> list[int | None]:
    """Per building, the median imagery colour inside its footprint (0xRRGGBB), or None
    (towers)."""
    from rasterio.features import rasterize
    from rasterio.transform import Affine
    from scipy.ndimage import median

    n = img.shape[1]
    shapes, ids = [], []
    for i, (h, _src, ring, *_rest) in enumerate(buildings):
        if h >= TOWER_M or len(ring) < 6:
            continue
        pts = [(ring[k] - x0, ring[k + 1] - z0) for k in range(0, len(ring), 2)]
        shapes.append(({"type": "Polygon", "coordinates": [pts + [pts[0]]]}, len(ids) + 1))
        ids.append(i)
    out: list[int | None] = [None] * len(buildings)
    if not shapes:
        return out
    labels = rasterize(shapes, out_shape=(n, n), transform=Affine.identity(), fill=0, dtype="int32")
    index = np.arange(1, len(ids) + 1)
    counts = np.bincount(labels.ravel(), minlength=len(ids) + 1)[1:]
    med = np.stack([np.asarray(median(img[b], labels, index)) for b in range(3)], axis=1)
    for j, i in enumerate(ids):
        if counts[j] >= MIN_PIXELS:
            r, g, b = (int(round(v)) for v in med[j])
        else:  # small or thin: the pixel under its centre
            ring = buildings[i][2]
            c = min(max(int(np.mean(ring[0::2]) - x0), 0), n - 1)
            z = min(max(int(np.mean(ring[1::2]) - z0), 0), n - 1)
            r, g, b = (int(v) for v in img[:, z, c])
        out[i] = (r << 16) | (g << 8) | b
    return out


def _tile(args) -> int:
    out, ix, iz, hires = args
    path = out / features_name(ix, iz)
    f = json.loads(path.read_text())
    if not f["buildings"]:
        return 0
    img = tile_picture(out, ix, iz, hires)
    colours = roof_colours(f["buildings"], img, ix * TILE_SIZE_M, iz * TILE_SIZE_M)
    f["buildings"] = [b[:3] + ([c] if c is not None else []) for b, c in zip(f["buildings"], colours)]
    path.write_text(json.dumps(f, separators=(",", ":"), sort_keys=True))
    return sum(c is not None for c in colours)


def colour_buildings(spec, out: Path, log=print, workers: int | None = None) -> None:
    """Every features tile's buildings get their roof colour (after the imagery)."""
    hires_path = out / "hires.json"
    hires = json.loads(hires_path.read_text()) if spec.sources.get("hires_imagery") and hires_path.exists() else None
    lo, hi = spec.ix_range
    jobs = [(out, ix, iz, hires) for iz in range(lo, hi + 1) for ix in range(lo, hi + 1) if (out / imagery_name(ix, iz)).exists()]
    with concurrent.futures.ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as ex:
        n = sum(ex.map(_tile, jobs, chunksize=4))
    log(f"building colours: {n} buildings coloured from the imagery")
