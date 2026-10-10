"""Building heights per building (the scenery build, visual only).

1. GlobalBuildingAtlas (the region file's `gba_heights`; Zhu et al. 2025): heights per
   footprint, machine-learned from 2019 satellite imagery. Buildings without an OSM height
   or levels tag (a type estimate so far; GHS-BUILT-H's 100 m cell averages then fill
   what GBA does not cover) take the height of the GBA footprint under their centre, plus `bias_m` (the
   dataset's median shortfall against OSM's levels here), at least `min_m`; houses and
   the like at most the region's `small_kinds_max_m`. Source "gba".
2. Shadows (`shadow_heights`, after the 1 m imagery): where the 1 m imagery covers, a
   building's height from the length of its shadow, measured from its footprint's edge
   on the shadow side to where the shadow ends, times the tangent of the sun's elevation
   of the satellite pass that took the picture there; used where the measurement is
   consistent along the building's edge (source "shadow").
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from flightsim.world.scenery import TILE_SIZE_M

REPLACEABLE = ("estimate", "estimate_small")  # (before GHS-BUILT-H fills what is left)
WEB_MERCATOR_R = 6378137.0


def _gba_footprints(spec, geodesy, src: Path):
    """[(ring in map metres (n, 2), height m)] of the GBA footprints with a height in the
    region."""
    from flightsim.world.scenery_build import region_bounds_deg, to_map_arrays

    gba = spec.sources["gba_heights"]
    names = [f["name"] for f in gba["files"]]
    heights = json.loads((src / next(n for n in names if n.endswith(".json"))).read_text())
    s, w, n, e = region_bounds_deg(spec, 0.0)
    rings, hs = [], []
    for name in names:
        if not name.endswith(".geojson"):
            continue
        for f in json.loads((src / name).read_text())["features"]:
            p = f["properties"]
            h = heights.get(f"{p.get('source', '')}{p.get('id', '')}{p.get('region', '')}")
            if not h or h.get("height") is None or h["height"] <= 0:  # -999: no data
                continue
            geom = f["geometry"]
            ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
            a = np.asarray(ring, float)  # EPSG:3857 (some files say 4326: they are 3857 too, the dataset's notes)
            lon = np.degrees(a[:, 0] / WEB_MERCATOR_R)
            lat = np.degrees(2.0 * np.arctan(np.exp(a[:, 1] / WEB_MERCATOR_R)) - math.pi / 2)
            if lon.max() < w or lon.min() > e or lat.max() < s or lat.min() > n:
                continue
            rings.append((lat, lon))
            hs.append(float(h["height"]))
    lat = np.concatenate([r[0] for r in rings])
    lon = np.concatenate([r[1] for r in rings])
    north, east = to_map_arrays(geodesy, lat, lon)
    out, k = [], 0
    for (la, _), h in zip(rings, hs):
        m = len(la)
        out.append((np.column_stack([east[k : k + m], -north[k : k + m]]), h))
        k += m
    return out


def gba_heights(spec, geodesy, tiles: dict, src: Path, log=print) -> None:
    """Heights from GlobalBuildingAtlas for the buildings without an OSM height (in place)."""
    from rasterio.features import rasterize
    from rasterio.transform import Affine

    gba = spec.sources["gba_heights"]
    bias, low = float(gba["bias_m"]), float(gba["min_m"])
    small_max = float((spec.sources.get("building_height") or {}).get("small_kinds_max_m", 1e9))
    prints = _gba_footprints(spec, geodesy, src)
    boxes = np.array([[r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max()] for r, _ in prints])
    hts = np.array([h for _, h in prints])
    n = int(TILE_SIZE_M)
    done = kept = 0
    for (ix, iz), t in tiles.items():
        if not isinstance(ix, int):
            continue
        todo = [k for k, b in enumerate(t["buildings"]) if b[1] in REPLACEABLE]
        if not todo:
            continue
        x0, z0 = ix * TILE_SIZE_M, iz * TILE_SIZE_M
        sel = np.nonzero((boxes[:, 2] > x0) & (boxes[:, 0] < x0 + n) & (boxes[:, 3] > z0) & (boxes[:, 1] < z0 + n))[0]
        if not len(sel):
            continue
        shapes = [({"type": "Polygon", "coordinates": [(prints[j][0] - (x0, z0)).tolist()]}, int(j) + 1) for j in sel]
        label = rasterize(shapes, out_shape=(n, n), transform=Affine.identity(), fill=0, dtype="int32")
        for k in todo:
            b = t["buildings"][k]
            ring = np.asarray(b[2], float).reshape(-1, 2)
            c, r = int(ring[:, 0].mean() - x0), int(ring[:, 1].mean() - z0)
            j = label[min(max(r, 0), n - 1), min(max(c, 0), n - 1)] - 1
            if j < 0:
                kept += 1
                continue
            h = max(low, hts[j] + bias)
            if b[1] == "estimate_small":
                h = min(h, small_max)
            t["buildings"][k] = [round(h, 1), "gba", *b[2:]]
            done += 1
    log(f"building heights: {done} from GlobalBuildingAtlas, {kept} without a GBA footprint keep theirs")
