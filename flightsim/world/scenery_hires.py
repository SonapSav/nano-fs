"""High-resolution imagery of a real-world region (the scenery build, visual only): 1 m
true colour from Satellogic EarthView (CC BY 4.0) around the origin, over the 10 m
Sentinel-2 imagery of the tiles.

EarthView is a set of 384 x 384 m tiles at 1 m in UTM, one per satellite pass that
imaged them (2022), named by their UTM corner; passes overlap, and a strip's edges have
no data (black). The build:

1. lists the bucket's tiles over the area (`sources/satellogic/listing.json`, pinned in
   the region file like the other sources) and downloads them (each checked against
   the listing's MD5 ETag, so the pinned listing pins every file);
2. composites them pixel by pixel in the region file's pass order (first with data wins);
3. matches colours to the Sentinel imagery (`tiles/i_*.jpg`): per tile a brightness
   curve and a colour balance (quantile matching of the band mean, then a gain per band,
   over the tile's footprint on land: EarthView stretches each tile's contrast on its
   own; with water in, asphalt was matched to the deep sea's black, and curves per band
   turned dark greys maroon), then a smooth gain per band (Sentinel / EarthView, both blurred
   over `match_blur_m`), so tiles and passes taken on different days, through different
   haze, meet without seams and fade into Sentinel;
4. feathers the edges of the covered area into the Sentinel imagery (`feather_m`) and
   writes square chunks: level 0 `CHUNK_PX` pixels at 1 m (`CHUNK_M` metres), level 1 at
   4 m (four times larger chunks), JPEG; rows going south like the tiles' imagery, on the
   map grid (chunk (cx, cz) covers east x in [cx, cx + 1) x chunk and south z alike).
   Pixels without EarthView data hold the Sentinel colour, so a chunk is a complete
   picture. `hires.json` lists the chunks and `hires/cover.png` (the covered area's
   weight every 16 m); the viewer (viewer/imageryClip.js) streams the chunks around the
   camera and hides OSM's drawn roads where the imagery covers.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import re
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree

import numpy as np

CHUNK_PX = 512  # pixels per chunk side, every level
LEVELS = (1.0, 4.0)  # metres per pixel of level 0 and level 1
TILE_PX = 384  # EarthView tile side (pixels, 1 m)
HIRES_FORMAT = 1
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_NAME = re.compile(r"region=(\d+)_(\d+)/date=[\d-]+/((\d{8})_(\d{6})_(SN\d+)_\w+?)_VISUAL\.tif$")


def chunk_m(level: int) -> float:
    return CHUNK_PX * LEVELS[level]


def pass_id(name: str) -> str:
    """A tile file's pass: its date and satellite ("20221025_SN28")."""
    m = _NAME.search(name)
    return f"{m[4]}_{m[6]}"


def chunk_range(radius_m: float) -> range:
    """Level-0 chunk indices covering [-radius, radius) on both axes (level-1 chunks are
    whole multiples of 4 level-0 chunks, so the range is rounded out to them)."""
    k = round(LEVELS[1] / LEVELS[0])
    lo = math.floor(-radius_m / chunk_m(0) / k) * k
    hi = math.ceil(radius_m / chunk_m(0) / k) * k
    return range(lo, hi)


# --- Listing and download ----------------------------------------------------------------


def _list(bucket: str, prefix: str) -> list[dict]:
    from flightsim.world.scenery_build import _get

    out, token = [], None
    while True:
        q = {"list-type": "2", "prefix": prefix} | ({"continuation-token": token} if token else {})
        x = ElementTree.fromstring(_get(f"{bucket}/?{urllib.parse.urlencode(q)}"))
        for c in x.iter(_S3_NS + "Contents"):
            out.append({"key": c.find(_S3_NS + "Key").text, "size": int(c.find(_S3_NS + "Size").text),
                        "etag": c.find(_S3_NS + "ETag").text.strip('"')})  # fmt: skip
        nxt = x.find(_S3_NS + "NextContinuationToken")
        if nxt is None:
            return out
        token = nxt.text


def utm_box(spec, hi: dict) -> tuple[float, float, float, float]:
    """(east_min, east_max, north_min, north_max) in the tiles' UTM zone covering the
    chunks, plus a tile's margin."""
    from rasterio.warp import transform

    from flightsim.world import geo
    from flightsim.world.scenery_build import to_geodetic_arrays

    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    r = chunk_range(float(hi["radius_m"]))
    edge = np.linspace(r.start * chunk_m(0), r.stop * chunk_m(0), 100)
    x = np.concatenate([edge, edge, np.full_like(edge, edge[0]), np.full_like(edge, edge[-1])])
    z = np.concatenate([np.full_like(edge, edge[0]), np.full_like(edge, edge[-1]), edge, edge])
    lat, lon = to_geodetic_arrays(g, -z, x)
    e, n = transform("EPSG:4326", f"EPSG:{hi['epsg']}", lon.tolist(), lat.tolist())
    return min(e) - TILE_PX, max(e) + TILE_PX, min(n) - TILE_PX, max(n) + TILE_PX


def listing(spec, hi: dict, dest: Path) -> list[dict]:
    """The VISUAL tiles over the area, of the listed passes (from the bucket the first
    time, then `dest`)."""
    if not dest.exists():
        e0, e1, n0, n1 = utm_box(spec, hi)
        prefixes = [f"data/tif/zone={hi['zone']}/region={k}" for k in range(int(e0) // 1000, int(e1) // 1000 + 1)]
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            found = [f for part in ex.map(lambda p: _list(hi["bucket"], p), prefixes) for f in part]
        keep = []
        for f in found:
            m = _NAME.search(f["key"])
            if m and e0 <= int(m[1]) <= e1 and n0 <= int(m[2]) <= n1:
                keep.append(f)
        keep.sort(key=lambda f: f["key"])
        part = dest.with_suffix(".part")
        part.write_text(json.dumps(keep, indent=0) + "\n")
        part.rename(dest)
    passes = set(hi["passes"])
    return [f for f in json.loads(dest.read_text()) if pass_id(f["key"]) in passes]


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()


def download(spec, hi: dict, src: Path, log=print) -> list[dict]:
    """Every listed tile into `src` (checked against its ETag, the MD5 of a single-part
    upload). Returns the listing."""
    from flightsim.world.scenery_build import _download

    src.mkdir(parents=True, exist_ok=True)
    files = listing(spec, hi, src / "listing.json")
    todo = [f for f in files if not (src / Path(f["key"]).name).exists()]
    log(f"hires imagery: {len(files)} tiles ({sum(f['size'] for f in files) / 1e9:.1f} GB), {len(todo)} to download")

    def fetch(f):
        dest = src / Path(f["key"]).name
        _download(f"{hi['bucket']}/{f['key']}", dest)
        if _md5(dest) != f["etag"]:
            dest.unlink()
            raise ValueError(f"{dest.name}: MD5 differs from the listing's ETag")

    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        for i, _ in enumerate(ex.map(fetch, todo), 1):
            if i % 500 == 0:
                log(f"hires imagery: downloaded {i} of {len(todo)}")
    return files


# --- Compositing and colour matching -------------------------------------------------------

COARSE_M = 16.0  # the colour matching's grid (one EarthView tile: 24 cells)
EDGE_PX = 2  # strip edges: pixels next to no data are dropped (resampling fringes)


class _Tiles:
    """The downloaded tiles: UTM corners, pass priority and files."""

    def __init__(self, files: list[dict], passes: list[str], src: Path):
        rank = {p: i for i, p in enumerate(passes)}
        rows = []
        for f in files:
            m = _NAME.search(f["key"])
            rows.append((rank[pass_id(f["key"])], int(m[1]), int(m[2]), src / Path(f["key"]).name))
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        self.rank = np.array([r[0] for r in rows])
        self.e0 = np.array([r[1] for r in rows], float)  # west edge
        self.n0 = np.array([r[2] for r in rows], float)  # north edge
        self.path = [r[3] for r in rows]
        self.passes = passes

    def overlapping(self, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        """Indices (priority order) of the tiles overlapping the points' bounding box."""
        return np.nonzero((self.e0 < e.max()) & (self.e0 + TILE_PX > e.min()) & (self.n0 > n.min()) & (self.n0 - TILE_PX < n.max()))[0]


def _read_tile(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """(rgb float32 (3, 384, 384), valid bool (384, 384)); no data where any band is 0,
    eroded by EDGE_PX."""
    import rasterio
    from scipy.ndimage import binary_erosion

    with rasterio.open(path) as ds:
        rgb = ds.read([1, 2, 3]).astype(np.float32)
    valid = binary_erosion(rgb.min(axis=0) > 0, iterations=EDGE_PX, border_value=1)
    return rgb, valid


def _thumb(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """A tile's COARSE_M means (rgb (3, 24, 24), valid fraction (24, 24))."""
    rgb, valid = _read_tile(path)
    k = int(COARSE_M)
    w = valid.reshape(TILE_PX // k, k, TILE_PX // k, k).sum(axis=(1, 3)).astype(np.float32)
    s = (rgb * valid).reshape(3, TILE_PX // k, k, TILE_PX // k, k).sum(axis=(2, 4))
    return s / np.maximum(w, 1), w / k**2


def _utm(geodesy, epsg: int, x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from rasterio.warp import transform

    from flightsim.world.scenery_build import to_geodetic_arrays

    lat, lon = to_geodetic_arrays(geodesy, -z.ravel(), x.ravel())
    e, n = transform("EPSG:4326", f"EPSG:{epsg}", lon.tolist(), lat.tolist())
    return np.asarray(e).reshape(x.shape), np.asarray(n).reshape(x.shape)


def _utm_grid(geodesy, epsg: int, x0: float, z0: float, step_m: float, size: int) -> tuple[np.ndarray, np.ndarray]:
    """UTM coordinates of the centres of a size x size map grid (rows south, columns east):
    exact every 16 m, bilinear between (the projections are smooth: well under a millimetre)."""
    from scipy.ndimage import map_coordinates

    span = step_m * size
    m = int(math.ceil(span / COARSE_M)) + 1
    k = np.arange(m) * COARSE_M
    e, n = _utm(geodesy, epsg, *np.meshgrid(x0 + k, z0 + k))
    c = ((np.arange(size) + 0.5) * step_m) / COARSE_M
    rr, cc = np.meshgrid(c, c, indexing="ij")
    return map_coordinates(e, [rr, cc], order=1), map_coordinates(n, [rr, cc], order=1)


def _window(tiles: _Tiles, i: int, e: np.ndarray, n: np.ndarray) -> tuple[slice, slice]:
    """The rows and columns of a map grid (UTM e, n per point; rows south, columns east,
    nearly aligned with UTM) that can hold tile i: from the grid's affine fit, padded."""
    h, w = e.shape
    rr, cc = np.meshgrid([0, h - 1], [0, w - 1], indexing="ij")
    a = np.column_stack([e[rr, cc].ravel(), n[rr, cc].ravel(), np.ones(4)])
    coef, *_ = np.linalg.lstsq(a, np.column_stack([rr.ravel(), cc.ravel()]).astype(float), rcond=None)
    ce = tiles.e0[i] + np.array([0, TILE_PX, 0, TILE_PX])
    cn = tiles.n0[i] - np.array([0, 0, TILE_PX, TILE_PX])
    rc = np.column_stack([ce, cn, np.ones(4)]) @ coef
    pad = 2 + 0.01 * max(h, w)  # the fit's error over the grid (projections differ slightly)
    r0, r1 = int(max(rc[:, 0].min() - pad, 0)), int(min(rc[:, 0].max() + pad + 1, h))
    c0, c1 = int(max(rc[:, 1].min() - pad, 0)), int(min(rc[:, 1].max() + pad + 1, w))
    return slice(r0, max(r0, r1)), slice(c0, max(c0, c1))


def _sample(tiles: _Tiles, i: int, e: np.ndarray, n: np.ndarray, data: np.ndarray, valid: np.ndarray, todo=None):
    """Tile i's colour at the points (UTM e, n) inside it with data (bilinear; on the tile's
    grid at its own pixel size: the 1 m tile or its coarse thumb): (index tuple, rgb (3, k)).
    Inside reaches the tile's outer edge (the half pixel beyond the outer pixel centres
    takes the edge pixels), so neighbouring tiles meet without a gap. `todo`: only there."""
    size = data.shape[-1]
    px = TILE_PX / size
    win = _window(tiles, i, e, n)
    e, n = e[win], n[win]
    col = (e - tiles.e0[i]) / px - 0.5
    row = (tiles.n0[i] - n) / px - 0.5
    sel = (col >= -0.5) & (row >= -0.5) & (col < size - 0.5) & (row < size - 0.5)
    if todo is not None:
        sel &= todo[win]
    if not sel.any():
        return None, None
    cs, rs = np.clip(col[sel], 0, size - 1), np.clip(row[sel], 0, size - 1)
    c, r = np.minimum(cs.astype(np.int64), size - 2), np.minimum(rs.astype(np.int64), size - 2)
    # Bilinear over the neighbours with data (renormalized): a point next to no data
    # takes its valid neighbours, so it does not fall through to a gap.
    fc, fr = cs - c, rs - r
    corners = [(r, c, (1 - fc) * (1 - fr)), (r, c + 1, fc * (1 - fr)), (r + 1, c, (1 - fc) * fr), (r + 1, c + 1, fc * fr)]
    wsum = sum(w * valid[rr, cc] for rr, cc, w in corners)
    ok = wsum > 1e-6
    v = sum(data[:, rr[ok], cc[ok]] * (w * valid[rr, cc])[ok] for rr, cc, w in corners) / wsum[ok]
    r, c = np.nonzero(sel)
    return (r[ok] + win[0].start, c[ok] + win[1].start), v


def _composite(tiles: _Tiles, which: np.ndarray, e: np.ndarray, n: np.ndarray, read, luts=None):
    """Pixel by pixel, the first tile (priority order) with data: (rgb (3, ...) float32,
    the tile per pixel (-1: none)), through each tile's colour curve (`luts`) if given.
    `read(i)` -> (rgb, valid) on the tile's grid."""
    rgb = np.zeros((3, *e.shape), np.float32)
    won = np.full(e.shape, -1, np.int32)
    for i in which:
        idx, v = _sample(tiles, i, e, n, *read(i), todo=won < 0)
        if idx is None:
            continue
        if luts is not None:
            v = _apply_match(v, luts[i])
        rgb[(slice(None), *idx)] = v
        won[idx] = i
    return rgb, won


class _Sentinel:
    """The built Sentinel imagery (tiles/i_*.jpg) as one picture, sampled at map positions."""

    def __init__(self, out: Path, x0: float, x1: float):
        import rasterio

        from flightsim.world.scenery import IMAGERY_PX, TILE_SIZE_M, imagery_name, landcover_name, LANDCOVER_CELLS

        self.t0 = math.floor(x0 / TILE_SIZE_M)
        t1 = math.ceil(x1 / TILE_SIZE_M)
        nt = t1 - self.t0
        self.px_m = TILE_SIZE_M / IMAGERY_PX
        self.lc_m = TILE_SIZE_M / LANDCOVER_CELLS
        self.rgb = np.zeros((3, nt * IMAGERY_PX, nt * IMAGERY_PX), np.uint8)
        self.lc = np.zeros((nt * LANDCOVER_CELLS, nt * LANDCOVER_CELLS), np.uint8)
        for j in range(nt):
            for i in range(nt):
                ix, iz = self.t0 + i, self.t0 + j
                with rasterio.open(out / imagery_name(ix, iz)) as ds:
                    self.rgb[:, j * IMAGERY_PX : (j + 1) * IMAGERY_PX, i * IMAGERY_PX : (i + 1) * IMAGERY_PX] = ds.read()
                lc = np.frombuffer((out / landcover_name(ix, iz)).read_bytes(), np.uint8).reshape(LANDCOVER_CELLS, LANDCOVER_CELLS)
                self.lc[j * LANDCOVER_CELLS : (j + 1) * LANDCOVER_CELLS, i * LANDCOVER_CELLS : (i + 1) * LANDCOVER_CELLS] = lc
        self.origin = self.t0 * TILE_SIZE_M

    def sample(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        """Bilinear colour (3, ...) float32."""
        from scipy.ndimage import map_coordinates

        rc = [(z - self.origin) / self.px_m - 0.5, (x - self.origin) / self.px_m - 0.5]
        return np.stack([map_coordinates(self.rgb[b], rc, order=1, mode="nearest").astype(np.float32) for b in range(3)])

    def land(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        from flightsim.world.scenery_build import WATER

        r = np.clip(((z - self.origin) / self.lc_m).astype(np.int64), 0, self.lc.shape[0] - 1)
        c = np.clip(((x - self.origin) / self.lc_m).astype(np.int64), 0, self.lc.shape[1] - 1)
        return self.lc[r, c] != WATER


def _quantile_lut(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """A monotone curve (256 entries) taking src's distribution to dst's (one band)."""
    q = np.linspace(0.5, 99.5, 100)
    a, b = np.percentile(src, q), np.percentile(dst, q)
    a = np.maximum.accumulate(a + np.arange(a.size) * 1e-6)  # strictly increasing for interp
    lut = np.interp(np.arange(256), a, b)
    # Linear beyond the matched range, with the end segments' slopes.
    lo, hi = a[0], a[-1]
    s_lo = (b[5] - b[0]) / max(a[5] - a[0], 1e-6)
    s_hi = (b[-1] - b[-6]) / max(a[-1] - a[-6], 1e-6)
    v = np.arange(256)
    lut = np.where(v < lo, b[0] + (v - lo) * s_lo, np.where(v > hi, b[-1] + (v - hi) * s_hi, lut))
    return np.clip(lut, 0, 255)


def _fit_match(v: np.ndarray, t: np.ndarray) -> tuple:
    """A tile's colour match (rgb rows (3, n) of EarthView against Sentinel): one curve
    for the brightness (quantile matching of the band mean) and one colour balance (a gain
    per band, medians after the curve). Per band curves turned dark greys maroon (the
    bands' dark tails differ); this keeps a pixel's hue and fixes the overall cast."""
    lv, lt = v.mean(0), t.mean(0)
    lut = _quantile_lut(lv, lt)
    # Below the median no darker than the mid-tones' gain: the 10 m imagery's darkest land
    # is deep shadow, and matching to it crushed 1 m asphalt to black.
    m = max(float(np.median(lv)), 1.0)
    x = np.arange(256)
    lut = np.where(x < m, np.maximum(lut, x * np.interp(m, x, lut) / m), lut)
    scaled = v * (np.interp(lv, np.arange(256), lut) / np.maximum(lv, 1.0))
    balance = np.median(t, axis=1) / np.maximum(np.median(scaled, axis=1), 1.0)
    return lut, np.clip(balance / balance.mean(), 0.7, 1.4)


def _apply_match(v: np.ndarray, match: tuple) -> np.ndarray:
    lut, balance = match
    lv = v.mean(0)
    return v * (np.interp(lv, np.arange(256), lut) / np.maximum(lv, 1.0)) * np.asarray(balance)[:, None]


class _Match:
    """The colour matching on the coarse grid, shared by every chunk: a curve per tile
    (EarthView stretches each tile's contrast on its own), a smooth gain per band over
    the whole area, and the feathering weight."""

    MIN_CELLS = 100  # a tile's own curve needs this many coarse land cells with data (of 576)

    def __init__(self, tiles: _Tiles, thumbs: list, sentinel: _Sentinel, e, n, x0: float, hi: dict, log):
        from scipy.ndimage import distance_transform_edt, find_objects

        size = e.shape[0]
        self.x0, self.size = x0, size
        k = (np.arange(size) + 0.5) * COARSE_M + x0
        x, z = np.meshgrid(k, k)
        target = sentinel.sample(x, z)
        land = sentinel.land(x, z)
        read = lambda i: (thumbs[i][0], thumbs[i][1] > 0.9)  # noqa: E731
        which = tiles.overlapping(e, n)
        # Per tile: its footprint's land with data against Sentinel's colour there. Land
        # only: with the sea in, the darkest land (asphalt) was matched to deep water and
        # came out black.
        self.luts, pooled = {}, {}
        for i in which:
            idx, v = _sample(tiles, i, e, n, *read(i))
            if idx is None:
                continue
            on = land[idx]
            idx, v = tuple(a[on] for a in idx), v[:, on]
            if not on.any():
                continue
            t = target[(slice(None), *idx)]
            if v.shape[1] >= self.MIN_CELLS:
                self.luts[i] = _fit_match(v, t)
            p = pooled.setdefault(tiles.rank[i], ([], []))
            p[0].append(v)
            p[1].append(t)
        # Slivers (too little data of their own): their pass's curve.
        by_pass = {p: _fit_match(np.concatenate(v, 1), np.concatenate(t, 1)) for p, (v, t) in pooled.items()}
        for i in which:
            if i not in self.luts and tiles.rank[i] in by_pass:
                self.luts[i] = by_pass[tiles.rank[i]]
        cur, won = _composite(tiles, which, e, n, read, self.luts)
        covered = won >= 0
        # Smooth gains (Sentinel / EarthView, each blurred), per tile from its own pixels
        # only (normalized convolution), so tiles meet without a step; and one over all
        # tiles for slivers that only show at 1 m.
        sigma = float(hi["match_blur_m"]) / COARSE_M
        self.gain = self._gain(target, cur, covered.astype(np.float32), sigma)
        self.tile_gain = {}
        pad = int(math.ceil(3 * sigma)) + 2
        for i, box in enumerate(find_objects(won + 1)):  # each tile's bounding box (label i + 1)
            if box is None:
                continue
            r0, c0 = max(box[0].start - pad, 0), max(box[1].start - pad, 0)
            r1, c1 = min(box[0].stop + pad, size), min(box[1].stop + pad, size)
            sl = (slice(None), slice(r0, r1), slice(c0, c1))
            w = (won[r0:r1, c0:c1] == i).astype(np.float32)
            self.tile_gain[i] = (r0, c0, self._gain(target[sl], cur[sl], w, sigma).astype(np.float16))
        d = distance_transform_edt(covered) * COARSE_M
        t = np.clip(d / float(hi["feather_m"]), 0.0, 1.0)
        self.alpha = (t * t * (3 - 2 * t)).astype(np.float32)  # smoothstep
        self.covered = covered
        ranks = tiles.rank[won[covered]]
        log(f"hires imagery: {covered.mean():.0%} of the area covered; passes used: "
            + ", ".join(f"{tiles.passes[p]} {np.mean(ranks == p):.0%}" for p in np.unique(ranks)))  # fmt: skip

    @staticmethod
    def _gain(target, cur, w, sigma):
        from scipy.ndimage import gaussian_filter

        g = np.ones_like(target)
        for b in range(3):
            num, den = gaussian_filter(target[b] * w, sigma, mode="nearest"), gaussian_filter(cur[b] * w, sigma, mode="nearest")
            g[b] = np.where(den > 1e-3, num / np.maximum(den, 1e-3), 1.0)
        return np.clip(g, 0.5, 2.0)

    def at(self, field: np.ndarray, x: np.ndarray, z: np.ndarray, r0: int = 0, c0: int = 0) -> np.ndarray:
        from scipy.ndimage import map_coordinates

        rc = [(z - self.x0) / COARSE_M - 0.5 - r0, (x - self.x0) / COARSE_M - 0.5 - c0]
        return map_coordinates(field.astype(np.float32), rc, order=1, mode="nearest")

    def apply_gain(self, rgb: np.ndarray, won: np.ndarray, x: np.ndarray, z: np.ndarray) -> None:
        """Each pixel's gain: its tile's own, else the overall one."""
        for i in np.unique(won[won >= 0]):
            sel = won == i
            r0, c0, g = self.tile_gain.get(i, (0, 0, self.gain))
            for b in range(3):
                rgb[b][sel] *= self.at(g[b], x[sel], z[sel], r0, c0)


# Shared with the chunk workers (fork): set by build_hires before the pool starts.
_STATE: dict = {}


def _write_jpeg(img: np.ndarray, path: Path, quality: int) -> None:
    import warnings

    import rasterio
    import rasterio.errors
    import rasterio.shutil
    from rasterio.io import MemoryFile

    with MemoryFile() as mem, warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with mem.open(driver="GTiff", width=img.shape[2], height=img.shape[1], count=3, dtype="uint8") as m:
            m.write(img)
            with MemoryFile() as jpg:
                rasterio.shutil.copy(m, jpg.name, driver="JPEG", QUALITY=quality)
                part = path.with_suffix(".part")
                part.write_bytes(jpg.read())
                part.rename(path)


def _write_png(img: np.ndarray, path: Path) -> None:
    import warnings

    import rasterio
    import rasterio.errors

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with rasterio.open(path, "w", driver="PNG", width=img.shape[2], height=img.shape[1], count=img.shape[0], dtype="uint8") as o:
            o.write(img)
    path.with_name(path.name + ".aux.xml").unlink(missing_ok=True)


def _block(cx1: int, cz1: int) -> list[tuple[int, int, int]]:
    """One level-1 chunk: its level-0 chunks with imagery and itself, written; returns
    the written chunks as (level, cx, cz)."""
    s = _STATE
    tiles, match, sentinel, g, hi, out = s["tiles"], s["match"], s["sentinel"], s["geodesy"], s["hi"], s["out"]
    k = round(LEVELS[1] / LEVELS[0])
    size = CHUNK_PX * k
    x0, z0 = cx1 * chunk_m(1), cz1 * chunk_m(1)
    # Anything to do: the coarse grid's coverage over this block.
    i0, j0 = int((x0 - match.x0) / COARSE_M), int((z0 - match.x0) / COARSE_M)
    n = int(chunk_m(1) / COARSE_M)
    if not match.covered[j0 : j0 + n, i0 : i0 + n].any():
        return []
    kk = x0 + (np.arange(size) + 0.5) * LEVELS[0]
    x, z = np.meshgrid(kk, z0 + (np.arange(size) + 0.5) * LEVELS[0])
    e, nn = _utm_grid(g, int(hi["epsg"]), x0, z0, LEVELS[0], size)
    rgb, won = _composite(tiles, tiles.overlapping(e, nn), e, nn, lambda i: _read_tile(tiles.path[i]), match.luts)
    match.apply_gain(rgb, won, x, z)
    alpha = np.where(won >= 0, match.at(match.alpha, x, z), 0.0).astype(np.float32)
    base = sentinel.sample(x, z)
    img = rgb * alpha + base * (1 - alpha)
    img = np.round(img).clip(0, 255).astype(np.uint8)
    written = []
    l0 = out / "hires" / "l0"
    for j in range(k):
        for i in range(k):
            sl = (slice(j * CHUNK_PX, (j + 1) * CHUNK_PX), slice(i * CHUNK_PX, (i + 1) * CHUNK_PX))
            if alpha[sl].max() > 0:
                _write_jpeg(img[(slice(None), *sl)], l0 / f"c_{cx1 * k + i}_{cz1 * k + j}.jpg", int(hi.get("jpeg_quality", 85)))
                written.append((0, cx1 * k + i, cz1 * k + j))
    if written:
        small = img.astype(np.float32).reshape(3, CHUNK_PX, k, CHUNK_PX, k).mean(axis=(2, 4))
        _write_jpeg(np.round(small).astype(np.uint8), out / "hires" / "l1" / f"c_{cx1}_{cz1}.jpg", int(hi.get("jpeg_quality", 85)))
        written.append((1, cx1, cz1))
    return written


def build_hires(spec, out: Path, log=print, workers: int | None = None) -> None:
    """hires/l0, hires/l1 and hires.json from the region's `hires_imagery` (after the
    Sentinel imagery and land cover tiles are built)."""
    import multiprocessing
    import shutil

    from flightsim.world import geo
    from flightsim.world.scenery_build import sha256_file

    hi = spec.sources["hires_imagery"]
    files = download(spec, hi, out / "sources" / "satellogic", log)
    tiles = _Tiles(files, list(hi["passes"]), out / "sources" / "satellogic")
    r = chunk_range(float(hi["radius_m"]))
    x0, x1 = r.start * chunk_m(0), r.stop * chunk_m(0)
    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    size = int((x1 - x0) / COARSE_M)
    e, n = _utm_grid(g, int(hi["epsg"]), x0, x0, COARSE_M, size)
    with concurrent.futures.ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as ex:
        thumbs = list(ex.map(_thumb, tiles.path, chunksize=64))
    log(f"hires imagery: {len(thumbs)} tiles read")
    sentinel = _Sentinel(out, x0, x1)
    match = _Match(tiles, thumbs, sentinel, e, n, x0, hi, log)
    for d in ("hires/l0", "hires/l1"):
        shutil.rmtree(out / d, ignore_errors=True)
        (out / d).mkdir(parents=True)
    _STATE.update(tiles=tiles, match=match, sentinel=sentinel, geodesy=g, hi=hi, out=out)
    k = round(LEVELS[1] / LEVELS[0])
    blocks = [(cx, cz) for cz in range(r.start // k, r.stop // k) for cx in range(r.start // k, r.stop // k)]
    written = []
    with concurrent.futures.ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as ex:
        futures = [ex.submit(_block, *b) for b in blocks]
        for i, f in enumerate(futures, 1):
            written += f.result()
            if i % 50 == 0:
                log(f"hires imagery: block {i} of {len(blocks)}")
    _STATE.clear()
    levels = []
    for lv, m in enumerate(LEVELS):
        chunks = sorted((cx, cz) for L, cx, cz in written if L == lv)
        levels.append({"m_per_px": m, "chunk_m": chunk_m(lv),
                       "chunks": {f"{cx},{cz}": sha256_file(out / "hires" / f"l{lv}" / f"c_{cx}_{cz}.jpg")[:16] for cx, cz in chunks}})  # fmt: skip
    # Where the imagery covers (its feathered weight on the coarse grid; rows going south):
    # the viewer hides OSM's drawn roads there, as the imagery shows the real ones.
    _write_png((np.round(match.alpha * 255).astype(np.uint8))[None], out / "hires" / "cover.png")
    cover = {"file": "hires/cover.png", "x0_m": x0, "z0_m": x0, "size_m": size * COARSE_M, "sha": sha256_file(out / "hires" / "cover.png")[:16]}
    index = {"format": HIRES_FORMAT, "chunk_px": CHUNK_PX, "levels": levels, "cover": cover}
    (out / "hires.json").write_text(json.dumps(index, indent=0, sort_keys=True) + "\n")
    size_mb = sum(p.stat().st_size for p in (out / "hires").rglob("*.jpg")) / 1e6
    log(f"hires imagery: {len(levels[0]['chunks'])} chunks at 1 m, {len(levels[1]['chunks'])} at 4 m, {size_mb:.0f} MB")
