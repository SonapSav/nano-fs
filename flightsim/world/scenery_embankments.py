"""Approach embankments at landmark bridges' ends (the scenery build, before the bridge
profiles): FABDEM is bare earth, and its removal of buildings and trees also took most of
the man-made banks the approach roads run on (Sheikh Zayed Bridge's east end: 11.6 m
where the road runs at about 17 m, the deck ~20 m; the owner's photo, 2026-10-11).
Copernicus GLO-30, FABDEM's source before that removal (a surface model: roads,
buildings, vehicles), still has them.

For a landmark bridge with `embankment: true` (the region file's `bridges`), at each end
where a road on the ground continues (OSM), the road is followed for up to `reach_m`,
the surface model sampled along it (smoothed: a median over `smooth_m`); the bank runs
from the end while it stands at least `raise_min_m` above the bare earth, its top the
surface model, then comes down at `grade` until it meets the ground. Height posts within
`half_width_m` (plus one post spacing, so the drawn surface reaches the top on the road)
of the road take the top; beyond, the side falls at `side_slope` (rise over run); the
bank is square at the bridge end (reaching one post spacing under the deck's end, as an
abutment's fill, so the ground at the end is the top); a post is only ever raised. Physics and viewer read the same edited tiles. Project choices:
the region file's numbers.
"""

from __future__ import annotations

import numpy as np

STEP_M = 10.0  # the road sampled every STEP_M
TWIN_M = 60.0  # bridge ends this close share their bank (project choice)


def bank_profile(s: np.ndarray, dsm: np.ndarray, bare: np.ndarray, raise_min_m: float, smooth_m: float, grade: float) -> np.ndarray | None:
    """The bank's top along a road from a bridge end (arc lengths `s`, every STEP_M;
    `dsm` the surface model, `bare` the ground there): NaN where no bank, or None when
    there is no bank at the end."""
    k = max(1, int(round(smooth_m / STEP_M)) | 1)
    pad = np.pad(dsm, k // 2, mode="edge")
    smooth = np.array([np.median(pad[i : i + k]) for i in range(len(dsm))])
    above = smooth - bare >= raise_min_m
    if not above[0]:
        return None
    n = int(np.argmin(above)) if not above.all() else len(s)  # the bank's run from the end
    top = np.full(len(s), np.nan)
    top[:n] = smooth[:n]
    if n < len(s):
        down = smooth[n - 1] - grade * (s[n:] - s[n - 1])
        top[n:] = np.where(down > bare[n:], down, np.nan)
        gone = np.isnan(top[n:])
        if gone.any():
            top[n + int(np.argmax(gone)) :] = np.nan  # once down to the ground it stays there
    return top


def raise_posts(h: np.ndarray, x0: float, post_m: float, line: np.ndarray, top: np.ndarray, half_width_m: float, side_slope: float) -> int:
    """Raises the posts of the height grid `h` (row = z, col = x; post (r, c) at
    x0 + c * post_m, x0 + r * post_m) to a bank along `line` ([[x, z], ...], one point per
    `top` value; NaN tops end it). Returns the number of posts raised."""
    keep = ~np.isnan(top)
    if keep.sum() < 2:
        return 0
    line, top = line[keep], top[keep]
    crest = half_width_m + post_m
    reach = crest + (np.nanmax(top) - np.nanmin(h)) / side_slope if side_slope > 0 else crest
    reach = min(reach, crest + 200.0)
    lo = np.floor((line.min(axis=0) - reach - x0) / post_m).astype(int)
    hi = np.ceil((line.max(axis=0) + reach - x0) / post_m).astype(int)
    c0, r0 = max(lo[0], 0), max(lo[1], 0)
    c1, r1 = min(hi[0], h.shape[1] - 1), min(hi[1], h.shape[0] - 1)
    if c0 > c1 or r0 > r1:
        return 0
    px = x0 + np.arange(c0, c1 + 1)[None, :] * post_m
    pz = x0 + np.arange(r0, r1 + 1)[:, None] * post_m
    best = np.full((pz.shape[0], px.shape[1]), -np.inf)  # (rows, cols)
    for (ax, az), (bx, bz), ta, tb in zip(line[:-1], line[1:], top[:-1], top[1:]):
        dx, dz = bx - ax, bz - az
        l2 = dx * dx + dz * dz or 1.0
        t = np.clip(((px - ax) * dx + (pz - az) * dz) / l2, 0.0, 1.0)
        d = np.hypot(px - (ax + dx * t), pz - (az + dz * t))
        best = np.maximum(best, ta + (tb - ta) * t - np.maximum(0.0, d - crest) * side_slope)
    # Square at the bridge end (an abutment; the bridge is behind it): nothing more than one
    # post spacing behind the line square to the road's first piece there (that one: so the
    # surface between the posts reaches the top at the end, under the deck's end).
    u = (line[1] - line[0]) / (np.hypot(*(line[1] - line[0])) or 1.0)
    best[(px - line[0][0]) * u[0] + (pz - line[0][1]) * u[1] < -post_m] = -np.inf
    block = h[r0 : r1 + 1, c0 : c1 + 1]
    raised = best > block
    block[raised] = best[raised]
    return int(raised.sum())


def _follow(lines: list[np.ndarray], start: np.ndarray, reach_m: float) -> np.ndarray | None:
    """The road on the ground leaving a bridge end at `start`: OSM pieces joined end to
    end (the straightest at a junction), up to `reach_m` long; None if none starts there."""
    def ends_at(p, exclude):
        out = []
        for i, l in enumerate(lines):
            if i in exclude:
                continue
            if np.hypot(*(l[0] - p)) < 3.0:
                out.append((i, l))
            elif np.hypot(*(l[-1] - p)) < 3.0:
                out.append((i, l[::-1]))
        return out

    first = ends_at(start, set())
    if not first:
        return None
    used, (i, path) = {first[0][0]}, first[0]
    path = path.copy()
    while True:
        length = np.hypot(*np.diff(path, axis=0).T).sum()
        if length >= reach_m:
            break
        nxt = ends_at(path[-1], used)
        if not nxt:
            break
        d = path[-1] - path[-2]
        j, l = max(nxt, key=lambda c: float((c[1][1] - c[1][0]) @ d) / (np.hypot(*(c[1][1] - c[1][0])) * np.hypot(*d) + 1e-9))
        used.add(j)
        path = np.vstack([path, l[1:]])
    return path


def _resample(path: np.ndarray, reach_m: float) -> tuple[np.ndarray, np.ndarray]:
    seg = np.hypot(*np.diff(path, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    s = np.arange(0.0, min(cum[-1], reach_m) + 1e-9, STEP_M)
    return s, np.column_stack([np.interp(s, cum, path[:, 0]), np.interp(s, cum, path[:, 1])])


class _Dsm:
    """The surface model (GLO-30 tiles, EPSG:4326, one array), bilinear at map points."""

    def __init__(self, paths, geodesy):
        import rasterio

        from flightsim.world.scenery_build import _mosaic, to_geodetic_arrays

        infos, self.lon0, self.dlon, self.lat0, self.dlat, rows, cols = _mosaic(paths)
        self.a = np.full((rows, cols), np.nan, np.float32)
        for p, w, hgt, _, _, lc, la, _ in infos:
            r, c = round((la - self.lat0) / self.dlat), round((lc - self.lon0) / self.dlon)
            with rasterio.open(p) as ds:
                self.a[r : r + hgt, c : c + w] = ds.read(1)
        self.g, self.to_geodetic = geodesy, to_geodetic_arrays

    def at(self, xz: np.ndarray) -> np.ndarray:
        lat, lon = self.to_geodetic(self.g, -xz[:, 1], xz[:, 0])
        u, v = (lon - self.lon0) / self.dlon, (lat - self.lat0) / self.dlat
        i, j = np.floor(u).astype(int), np.floor(v).astype(int)
        fx, fz = u - i, v - j
        a = self.a
        top = a[j, i] + (a[j, i + 1] - a[j, i]) * fx
        bot = a[j + 1, i] + (a[j + 1, i + 1] - a[j + 1, i]) * fx
        return top + (bot - top) * fz


def build_embankments(spec, g, ways: list[dict], roads: list[np.ndarray], h: np.ndarray, x0: float, post_m: float, dsm_paths, log=print) -> list[dict]:
    """Raises the height grid `h` (the region's posts, see raise_posts) under the approach
    banks of the landmark bridges with `embankment: true`; `ways` the OSM bridge ways,
    `roads` every road on the ground ([[x, z], ...]). Returns what was raised, per end."""
    from flightsim.world.scenery_bridges import _polyline, join

    cfg = spec.sources["dsm"]
    marks = [m for m in spec.sources.get("bridges") or [] if m.get("embankment")]
    if not marks:
        return []
    dsm = _Dsm(dsm_paths, g)
    reach = float(cfg["reach_m"])
    boxes = [(r, r.min(axis=0), r.max(axis=0)) for r in roads]

    def bare(xz):
        u, v = (xz[:, 0] - x0) / post_m, (xz[:, 1] - x0) / post_m
        i, j = np.floor(u).astype(int), np.floor(v).astype(int)
        fx, fz = u - i, v - j
        top = h[j, i] + (h[j, i + 1] - h[j, i]) * fx
        bot = h[j + 1, i] + (h[j + 1, i + 1] - h[j + 1, i]) * fx
        return top + (bot - top) * fz

    banks = []
    for chain in join(ways):
        ids = {w["id"] for w in chain}
        names = {w["name"] for w in chain if w["name"]}
        mark = next((m for m in marks if set(m.get("ways", [])) & ids or m.get("osm_name") in names), None)
        if not mark:
            continue
        line = _polyline(chain)
        for end in (line[0], line[-1]):
            near = [r for r, lo, hi in boxes if (lo <= end + reach + 100).all() and (hi >= end - reach - 100).all()]
            path = _follow(near, end, reach)
            if path is None or len(path) < 2:
                continue
            s, xz = _resample(path, reach)
            if len(s) < 3:
                continue
            top = bank_profile(s, dsm.at(xz), bare(xz), float(cfg["raise_min_m"]), float(cfg["smooth_m"]), float(cfg["grade"]))
            if top is None:
                continue
            banks.append((mark["name"], end, xz, top, float(bare(xz[:1])[0]), float(s[~np.isnan(top)][-1])))
    # All measured on the bare earth first, then raised (neighbouring ends, e.g. twin decks,
    # do not hide each other's banks). Ends within TWIN_M of each other (twin decks: the
    # surface model's 30 m cannot tell their roads apart) take the higher bank of the two,
    # along the length they share.
    tops = [b[3].copy() for b in banks]
    for i, a in enumerate(banks):
        for j, b in enumerate(banks):
            if i != j and np.hypot(*(a[1] - b[1])) < TWIN_M:
                n = min(len(tops[i]), len(b[3]))
                tops[i][:n] = np.fmax(tops[i][:n], b[3][:n])
    banks = [(name, end, xz, top, before, float(STEP_M * (np.flatnonzero(~np.isnan(top))[-1]))) for (name, end, xz, _, before, _), top in zip(banks, tops)]
    done = []
    for name, end, xz, top, before, run in banks:
        n = raise_posts(h, x0, post_m, xz, top, float(cfg["half_width_m"]), float(cfg["side_slope"]))
        done.append({"bridge": name, "end": [round(float(end[0]), 1), round(float(end[1]), 1)], "ground_m": round(before, 1),
                     "top_m": round(float(top[0]), 1), "length_m": round(run, 0), "posts": n})  # fmt: skip
        log(f"embankment {name} end ({end[0]:.0f}, {end[1]:.0f}): ground {before:.1f} m -> bank {top[0]:.1f} m, "
            f"{run:.0f} m along the road, {n} posts raised")  # fmt: skip
    return done


def dsm_files(spec) -> list[dict]:
    """The region's surface model tiles (source_files)."""
    cfg = spec.sources.get("dsm")
    if not cfg:
        return []
    return [{"kind": "dsm", "name": f"glo30_{t}.tif", "url": cfg["url"].format(tile=t)} for t in cfg["tiles"]]

