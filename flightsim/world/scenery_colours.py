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


def tile_picture(out: Path, ix: int, iz: int, hires: dict | None, with_mask: bool = False):
    """The 4 km tile's imagery at 1 m (3, 4000, 4000) uint8: the 1 m chunks where there
    are, the Sentinel tile (nearest, upsampled) elsewhere; with `with_mask` also where it
    is the 1 m imagery (bool, 4000 x 4000)."""
    n = int(TILE_SIZE_M)
    base = _read(out / imagery_name(ix, iz))
    k = n // base.shape[1]
    img = np.repeat(np.repeat(base, k, axis=1), k, axis=2)
    mask = np.zeros((n, n), bool)
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
                mask[int(az0 - z0) : int(az1 - z0), int(ax0 - x0) : int(ax1 - x0)] = True
    return (img, mask) if with_mask else img


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


# --- Road, bridge and runway colours -----------------------------------------------------------
#
# Drawn roads, taxiways, aprons, bridge decks and runways take the colour the 1 m imagery
# shows for them, so they match the imagery's own roads where those show (and where the
# viewer hides the drawn ones): the median under their centrelines, per tile and road
# class; per runway along lines at a quarter of its width from the centreline (beside the
# markings). Where the 1 m imagery has too few samples (outside its area, where 10 m
# pixels mix the road with its surroundings) the region's median for the class. A bridge
# takes its class's colour in the tile at its middle: the colour of the roads it joins
# (measured along the deck it picked up the shade and planting beside it).

STEP_M = 2.0  # sampling along lines
MIN_SAMPLES = 40  # 1 m samples for a measured colour


def _along(pts: list, step: float = STEP_M) -> np.ndarray:
    """Points every `step` metres along a polyline [x0, z0, x1, z1, ...] or [[x, z], ...]."""
    p = np.asarray(pts, float).reshape(-1, 2)
    out = [p[:1]]
    for a, b in zip(p[:-1], p[1:]):
        n = max(1, int(np.hypot(*(b - a)) / step))
        out.append(a + (b - a) * (np.arange(1, n + 1) / n)[:, None])
    return np.concatenate(out)


def _samples(img: np.ndarray, mask: np.ndarray, pts: np.ndarray, x0: float, z0: float) -> np.ndarray:
    """RGB rows of the 1 m imagery at the points inside the tile (where it is 1 m)."""
    n = img.shape[1]
    c = np.floor(pts[:, 0] - x0).astype(int)
    r = np.floor(pts[:, 1] - z0).astype(int)
    ok = (c >= 0) & (c < n) & (r >= 0) & (r < n)
    c, r = c[ok], r[ok]
    hi = mask[r, c]
    return img[:, r[hi], c[hi]].T


def _apron_points(ring: list, step: float = 4.0) -> np.ndarray:
    """Points on a grid inside an apron (ring [x0, z0, ...]): its concrete, not its edges."""
    r = np.asarray(ring, float).reshape(-1, 2)
    xs = np.arange(r[:, 0].min() + step / 2, r[:, 0].max(), step)
    zs = np.arange(r[:, 1].min() + step / 2, r[:, 1].max(), step)
    if not len(xs) or not len(zs):
        return np.zeros((0, 2))
    X, Z = np.meshgrid(xs, zs)
    x, z = X.ravel(), Z.ravel()
    ins = np.zeros(x.shape, bool)
    for (x0, z0), (x1, z1) in zip(r, np.roll(r, -1, axis=0)):  # even-odd rule
        cross = (z0 > z) != (z1 > z)
        ins ^= cross & (x < x0 + (z - z0) * (x1 - x0) / np.where(z1 == z0, 1e-9, z1 - z0))
    return np.column_stack([x[ins], z[ins]])


def _hex(rgb) -> int:
    r, g, b = (int(round(v)) for v in rgb)
    return (r << 16) | (g << 8) | b


def _median(rows: list) -> tuple | None:
    if not rows:
        return None
    a = np.concatenate(rows)
    return (np.median(a, axis=0), len(a)) if len(a) else None


def _tile(args):
    """Building roof colours (written), and the tile's road and runway samples (returned
    for colour_features)."""
    out, ix, iz, hires, runway_pts = args
    path = out / features_name(ix, iz)
    f = json.loads(path.read_text())
    x0, z0 = ix * TILE_SIZE_M, iz * TILE_SIZE_M
    img, mask = tile_picture(out, ix, iz, hires, with_mask=True)
    n = 0
    if f["buildings"]:
        colours = roof_colours(f["buildings"], img, x0, z0)
        f["buildings"] = [b[:3] + ([c] if c is not None else []) for b, c in zip(f["buildings"], colours)]
        path.write_text(json.dumps(f, separators=(",", ":"), sort_keys=True))
        n = sum(c is not None for c in colours)
    roads = {cls: [_samples(img, mask, _along(line), x0, z0) for line in lines] for cls, lines in f["roads"].items()}
    taxi = [_samples(img, mask, _along(line), x0, z0) for line in f["taxiway"]]
    apron = [_samples(img, mask, _apron_points(r), x0, z0) for r in f["apron"]]
    runways = {i: _samples(img, mask, p, x0, z0) for i, p in runway_pts.items()}
    cat = lambda v: np.concatenate(v) if v else np.zeros((0, 3))  # noqa: E731
    return n, {**{cls: cat(v) for cls, v in roads.items()}, "_taxiway": cat(taxi), "_apron": cat(apron)}, runways


def colour_features(spec, out: Path, log=print, workers: int | None = None) -> None:
    """Every features tile's building roof colours and road colours, bridges.json's deck
    colours, airfields.json's runway colours (after the imagery)."""
    hires_path = out / "hires.json"
    hires = json.loads(hires_path.read_text()) if spec.sources.get("hires_imagery") and hires_path.exists() else None
    lo, hi = spec.ix_range
    bridges = json.loads((out / "bridges.json").read_text()) if (out / "bridges.json").exists() else []
    fields = json.loads((out / "airfields.json").read_text())
    # Sample points per tile: runways at a quarter
    # of their width either side of theirs.
    by_tile: dict = {}

    def assign(key, pts):
        t = np.floor(pts / TILE_SIZE_M).astype(int)
        for k in {tuple(v) for v in t}:
            sel = (t[:, 0] == k[0]) & (t[:, 1] == k[1])
            by_tile.setdefault(k, {})[key] = pts[sel]

    for i, rw in enumerate(fields.get("runways", [])):
        (ax, az), (bx, bz) = [(e["pavement"][1], -e["pavement"][0]) for e in rw["ends"]]  # ends are [north, east]
        L = math.hypot(bx - ax, bz - az)
        nx, nz = -(bz - az) / L, (bx - ax) / L
        pts = np.concatenate([_along([ax + nx * o, az + nz * o, bx + nx * o, bz + nz * o]) for o in (-rw["width_m"] / 4, rw["width_m"] / 4)])
        assign(i, pts)
    jobs = [(out, ix, iz, hires, by_tile.get((ix, iz), {})) for iz in range(lo, hi + 1) for ix in range(lo, hi + 1) if (out / imagery_name(ix, iz)).exists()]
    with concurrent.futures.ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as ex:
        results = list(ex.map(_tile, jobs, chunksize=4))
    log(f"building colours: {sum(r[0] for r in results)} buildings coloured from the imagery")
    # Region medians per road class (and paved), from every tile's 1 m samples.
    pooled: dict = {}
    for _, roads, _ in results:
        for cls, a in roads.items():
            pooled.setdefault(cls, []).append(a)
    region = {cls: np.median(np.concatenate(v), axis=0) for cls, v in pooled.items() if sum(len(a) for a in v) >= MIN_SAMPLES}
    fallback = np.median(np.concatenate([np.concatenate(v) for k, v in pooled.items() if not k.startswith("_")]), axis=0)
    log("road colours, region medians: " + ", ".join(f"{k} #{_hex(v):06x}" for k, v in sorted(region.items())))
    runway_rows: dict = {}
    measured = 0
    for (job, (_, roads, rws)) in zip(jobs, results):
        _, ix, iz = job[:3]
        path = out / features_name(ix, iz)
        f = json.loads(path.read_text())
        cols = {}
        def pick(key):
            a = roads.get(key, np.zeros((0, 3)))
            return _hex(np.median(a, axis=0) if len(a) >= MIN_SAMPLES else region.get(key, fallback)), len(a) >= MIN_SAMPLES

        for cls in f["roads"]:
            cols[cls], ok = pick(cls)
            measured += ok
        f["colours"] = {"roads": cols, "taxiway": pick("_taxiway")[0], "apron": pick("_apron")[0]}
        path.write_text(json.dumps(f, separators=(",", ":"), sort_keys=True))
        for i, a in rws.items():
            runway_rows.setdefault(i, []).append(a)
    # A bridge's road: the colour its class has in the tile around its middle (the colour
    # the roads it joins are drawn in). Measured along the bridge it picked up the shade
    # and planting beside it (brownish decks between grey roads).
    tile_cols = {}
    for job in jobs:
        _, ix, iz = job[:3]
        tile_cols[(ix, iz)] = json.loads((out / features_name(ix, iz)).read_text()).get("colours", {}).get("roads", {})
    for b in bridges:
        mid = b["pts"][len(b["pts"]) // 2]
        cols = tile_cols.get((math.floor(mid[0] / TILE_SIZE_M), math.floor(mid[1] / TILE_SIZE_M)), {})
        b["colour"] = cols.get(b["cls"]) if b["cls"] in cols else _hex(region.get(b["cls"], fallback))
    (out / "bridges.json").write_text(json.dumps(bridges, separators=(",", ":"), sort_keys=True) + "\n")
    for i, rw in enumerate(fields.get("runways", [])):
        m = _median(runway_rows.get(i, []))
        rw.pop("colour", None)  # (a previous build's)
        if m and m[1] >= MIN_SAMPLES:
            rw["colour"] = _hex(m[0])
    (out / "airfields.json").write_text(json.dumps(fields, indent=1, sort_keys=True) + "\n")
    log(f"road colours: {measured} tile road classes measured on the 1 m imagery, the rest the region's median; "
        f"{sum('colour' in rw for rw in fields.get('runways', []))} runways")  # fmt: skip
