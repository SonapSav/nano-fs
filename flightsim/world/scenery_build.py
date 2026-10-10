"""Build a real-world scenery region from open data, offline (scripts/build_scenery.py).

Needs the `scenery` dependency group (rasterio, osmium), imported only here: the physics
and the viewer read the built files through scenery.py. Steps, each repeatable:

1. download: every source once into data/scenery/<region>/sources/ (FABDEM 1-degree
   tiles out of their 10-degree zip by HTTP range requests, ESA WorldCover 3-degree
   tiles, the dated Geofabrik OSM extract); checked against the region file's sha256
   when pinned there, and recorded in the manifest.
2. heights: FABDEM, bilinear at every height post of the map grid (positions through
   the same transverse Mercator as world/geo.py, here on numpy arrays).
3. land cover: ESA WorldCover, the class at each cell's centre; cells without data (the
   open sea, outside WorldCover) become water.
4. airfields (OpenStreetMap, scenery_osm.py, with the region file's published data):
   every runway's ends, width and elevation; the ground along each runway is flattened
   onto a straight slope fitted to the terrain under its centreline (`airfields.json`).
4b. shore distance: from each water cell to the nearest land (the viewer's shallow water).
5. features (OpenStreetMap): roads, railways, taxiways, aprons and buildings per tile.
6. manifest.json: the region, its sources, every file's sha256.

The same sources and code give byte-identical files.
"""

import hashlib
import json
import math
import struct
import urllib.request
import warnings
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from flightsim.world import geo
from flightsim.world.dem import DemTerrain
from flightsim.world.scenery import (
    FORMAT, HEIGHT_CELLS, IMAGERY_PX, LANDCOVER_CELLS, POST_M, SCENERY_DIR, SHORE_STEP_M, TILE_SIZE_M, features_name,
    heights_name, imagery_name, landcover_name, shore_name,
)  # fmt: skip

USER_AGENT = "nano-fs-scenery-build/1"
WATER = 80  # WorldCover "permanent water bodies"


# --- Region definition ------------------------------------------------------------------


@dataclass(frozen=True)
class RegionSpec:
    """A region file (configs/scenery/<name>.yaml)."""

    name: str
    origin_lat_deg: float
    origin_lon_deg: float
    tiles_radius: int  # tiles from -radius to radius - 1 east and south
    sources: dict
    pinned: dict = field(default_factory=dict)  # file name -> sha256
    airports: dict = field(default_factory=dict)  # ICAO -> published data (elevation_ft, runways)
    origin_airport: str | None = None  # the airport at the origin (ICAO), for displays
    default_runway_width_m: float = 30.0

    @property
    def ix_range(self) -> tuple[int, int]:
        return -self.tiles_radius, self.tiles_radius - 1


def load_spec(path: str | Path) -> RegionSpec:
    raw = yaml.safe_load(Path(path).read_text())
    return RegionSpec(
        name=raw["name"], origin_lat_deg=float(raw["origin_lat_deg"]), origin_lon_deg=float(raw["origin_lon_deg"]),
        tiles_radius=int(raw["tiles_radius"]), sources={**raw["sources"], "landmarks": raw.get("landmarks") or [], "bridges": raw.get("bridges") or []},
        pinned=raw.get("pinned", {}) or {},
        airports=raw.get("airports", {}) or {}, default_runway_width_m=float(raw.get("default_runway_width_m", 30.0)),
        origin_airport=raw.get("origin_airport"),
    )  # fmt: skip


# --- Map positions on arrays (world/geo.py's transverse Mercator) -------------------------


def to_geodetic_arrays(geodesy: geo.Geodesy, north_m: np.ndarray, east_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(lat, lon) in degrees of map positions: geo.Geodesy.to_geodetic on numpy arrays."""
    if geodesy.model != "wgs84":
        raise ValueError("real scenery uses the wgs84 map")
    xi = (north_m + geodesy._north0) / geo._A
    eta = east_m / geo._A
    xi_p, eta_p = xi.copy(), eta.copy()
    for j, b in enumerate(geo._BETA, start=1):
        xi_p -= b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        eta_p -= b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
    chi = np.arcsin(np.sin(xi_p) / np.cosh(eta_p))
    lat = chi + sum(d * np.sin(2 * j * chi) for j, d in enumerate(geo._DELTA, start=1))
    dlon = np.arctan2(np.sinh(eta_p), np.cos(xi_p))
    return np.degrees(lat), np.degrees(dlon + geodesy._lon0)


def region_bounds_deg(spec: RegionSpec, margin_m: float = 2000.0) -> tuple[float, float, float, float]:
    """(south, west, north, east) in degrees covering the region's tiles plus a margin."""
    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    lo, hi = spec.ix_range
    edge = np.linspace(lo * TILE_SIZE_M - margin_m, (hi + 1) * TILE_SIZE_M + margin_m, 200)
    n = np.concatenate([edge, edge, np.full_like(edge, edge[0]), np.full_like(edge, edge[-1])])
    e = np.concatenate([np.full_like(edge, edge[0]), np.full_like(edge, edge[-1]), edge, edge])
    lat, lon = to_geodetic_arrays(g, n, e)
    return float(lat.min()), float(lon.min()), float(lat.max()), float(lon.max())


# --- Downloads ------------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def _download(url: str, dest: Path) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    part.rename(dest)


def _zip_member(url: str, name: str, dest: Path) -> None:
    """One stored or deflated member of a remote zip, by HTTP range requests (the zip's
    central directory, the member's local header, its data); checked against its CRC."""
    head = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(head, timeout=120) as r:
        size = int(r.headers["Content-Length"])
    tail = _get(url, {"Range": f"bytes={max(0, size - 65536 - 22)}-{size - 1}"})
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError(f"{url}: no end of central directory (zip64?)")
    cd_size, cd_offset = struct.unpack("<II", tail[eocd + 12 : eocd + 20])
    if cd_offset == 0xFFFFFFFF:  # zip64: the real values are in the zip64 end record
        loc = tail.rfind(b"PK\x06\x07")
        (z64,) = struct.unpack("<Q", tail[loc + 8 : loc + 16])
        rec = _get(url, {"Range": f"bytes={z64}-{z64 + 55}"})
        cd_size, cd_offset = struct.unpack("<QQ", rec[40:56])
    cd = _get(url, {"Range": f"bytes={cd_offset}-{cd_offset + cd_size - 1}"})
    # Find the member in the central directory.
    pos = 0
    while pos < len(cd):
        if cd[pos : pos + 4] != b"PK\x01\x02":
            raise ValueError("bad central directory")
        (method,) = struct.unpack("<H", cd[pos + 10 : pos + 12])
        crc, csize, usize = struct.unpack("<III", cd[pos + 16 : pos + 28])
        nlen, xlen, clen = struct.unpack("<HHH", cd[pos + 28 : pos + 34])
        (offset,) = struct.unpack("<I", cd[pos + 42 : pos + 46])
        fname = cd[pos + 46 : pos + 46 + nlen].decode()
        extra = cd[pos + 46 + nlen : pos + 46 + nlen + xlen]
        if fname == name:
            # zip64 extra field: sizes and offset that did not fit in 32 bits.
            vals = [usize, csize, offset]
            e = 0
            while e < len(extra):
                tag, ln = struct.unpack("<HH", extra[e : e + 4])
                if tag == 0x0001:
                    data, k = extra[e + 4 : e + 4 + ln], 0
                    for idx, v in enumerate(vals):
                        if v == 0xFFFFFFFF:
                            vals[idx] = struct.unpack("<Q", data[k : k + 8])[0]
                            k += 8
                e += 4 + ln
            usize, csize, offset = vals
            local = _get(url, {"Range": f"bytes={offset}-{offset + 29}"})
            lnlen, lxlen = struct.unpack("<HH", local[26:30])
            start = offset + 30 + lnlen + lxlen
            data = _get(url, {"Range": f"bytes={start}-{start + csize - 1}"})
            if method == 8:
                data = zlib.decompress(data, -15)
            elif method != 0:
                raise ValueError(f"{name}: compression method {method}")
            if len(data) != usize or zlib.crc32(data) != crc:
                raise ValueError(f"{name}: size or CRC mismatch")
            part = dest.with_suffix(dest.suffix + ".part")
            part.write_bytes(data)
            part.rename(dest)
            return
        pos += 46 + nlen + xlen + clen
    raise FileNotFoundError(f"{name} not in {url}")


def _imagery_window(urls: list[str], bounds_deg, dest: Path) -> None:
    """The part of remote single-band Cloud-Optimized GeoTIFFs (one scene's bands, same
    grid) covering a lat/lon box (GDAL range requests), saved as one local multi-band
    GeoTIFF with its georeferencing."""
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import from_bounds

    s, w, n, e = bounds_deg
    bands = []
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_USERAGENT=USER_AGENT):
        for url in urls:
            with rasterio.open(f"/vsicurl/{url}") as ds:
                xs, ys = transform("EPSG:4326", ds.crs, [w, e, w, e], [s, s, n, n])
                win = from_bounds(min(xs), min(ys), max(xs), max(ys), ds.transform).round_offsets().round_lengths()
                win = win.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
                bands.append(ds.read(1, window=win))
                profile = {"driver": "GTiff", "width": win.width, "height": win.height, "count": len(urls), "dtype": bands[0].dtype,
                           "crs": ds.crs, "transform": ds.window_transform(win), "nodata": ds.nodata, "compress": "deflate",
                           "predictor": 2, "tiled": True}  # fmt: skip
    data = np.stack(bands)
    part = dest.with_suffix(dest.suffix + ".part")
    with rasterio.open(part, "w", **profile) as out:
        out.write(data)
    part.rename(dest)


def _deg_tiles(south: float, west: float, north: float, east: float, step: int) -> list[tuple[int, int]]:
    """South-west corners (lat, lon) of the step-degree tiles covering a box."""
    lats = range(math.floor(south / step) * step, math.floor(north / step) * step + 1, step)
    lons = range(math.floor(west / step) * step, math.floor(east / step) * step + 1, step)
    return [(la, lo) for la in lats for lo in lons]


def _ns_ew(lat: int, lon: int, lon_digits: int = 3) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):0{lon_digits}d}"


def source_files(spec: RegionSpec) -> list[dict]:
    """Every file the region needs: {kind, name, url, member?, lat, lon}."""
    s, w, n, e = region_bounds_deg(spec)
    out = []
    dem = spec.sources["dem"]
    for la, lo in _deg_tiles(s, w, n, e, 1):
        name = f"{_ns_ew(la, lo)}_FABDEM_V1-2.tif"
        out.append({"kind": "dem", "name": name, "url": dem["zip_url"], "member": name, "lat": la, "lon": lo})
    lc = spec.sources["landcover"]
    for la, lo in _deg_tiles(s, w, n, e, 3):
        tile = _ns_ew(la, lo)
        out.append({"kind": "landcover", "name": Path(lc["url"].format(tile=tile)).name, "url": lc["url"].format(tile=tile), "lat": la, "lon": lo})
    osm = spec.sources["osm"]
    out.append({"kind": "osm", "name": Path(osm["url"]).name, "url": osm["url"]})
    bh = spec.sources.get("building_height")
    if bh:
        out.append({"kind": "building_height", "name": "building_height_window.tif", "url": bh["url"]})
    # Imagery: the window of each scene that covers the region (in priority order).
    for item in (spec.sources.get("imagery") or {}).get("items", []):
        out.append({"kind": "imagery", "name": f"{item['id']}_window.tif", "url": item["url"]})
    return out


def download(spec: RegionSpec, root: Path = SCENERY_DIR, log=print) -> list[dict]:
    """Fetch what is missing; check pinned hashes. Returns the source files with sha256."""
    src = root / spec.name / "sources"
    src.mkdir(parents=True, exist_ok=True)
    files = source_files(spec)
    for f in files:
        dest = src / f["name"]
        if not dest.exists():
            log(f"downloading {f['name']}")
            if "member" in f:
                _zip_member(f["url"], f["member"], dest)
            elif f["kind"] == "building_height":
                _imagery_window([f["url"]], region_bounds_deg(spec), dest)
            elif f["kind"] == "imagery":
                bands = spec.sources["imagery"]["bands"]
                _imagery_window([f["url"] + f"{b}.tif" for b in bands], region_bounds_deg(spec), dest)
            else:
                _download(f["url"], dest)
        f["sha256"] = sha256_file(dest)
        pin = spec.pinned.get(f["name"])
        if pin and pin != f["sha256"]:
            raise ValueError(f"{f['name']}: sha256 {f['sha256']} differs from the pinned {pin}; delete it to download again")
    return files


# --- Heights and land cover -----------------------------------------------------------------


def _mosaic(paths: list[Path]):
    """The rasters (same grid spacing) as one array with its (lon0, dlon, lat0, dlat):
    pixel centre (row r, col c) at lon0 + c * dlon, lat0 + r * dlat."""
    import rasterio

    infos = []
    for p in paths:
        with rasterio.open(p) as ds:
            t = ds.transform
            infos.append((p, ds.width, ds.height, t.a, t.e, t.c + t.a / 2, t.f + t.e / 2, ds.nodata))
    dlon, dlat = infos[0][3], infos[0][4]
    for i in infos:
        if abs(i[3] - dlon) > 1e-12 or abs(i[4] - dlat) > 1e-12:
            raise ValueError("rasters with different grid spacing")
    lon0 = min(i[5] for i in infos)
    lat0 = max(i[6] for i in infos)
    cols = max(round((i[5] - lon0) / dlon) + i[1] for i in infos)
    rows = max(round((i[6] - lat0) / dlat) + i[2] for i in infos)
    return infos, lon0, dlon, lat0, dlat, rows, cols


def build_heights(spec: RegionSpec, dem_paths: list[Path], out: Path, log=print) -> None:
    """FABDEM sampled bilinearly at every post; no data (none expected) is an error."""
    import rasterio

    infos, lon0, dlon, lat0, dlat, rows, cols = _mosaic(dem_paths)
    grid = np.full((rows, cols), np.nan, dtype=np.float32)
    for p, w, h, _, _, clon, clat, nodata in infos:
        r0, c0 = round((clat - lat0) / dlat), round((clon - lon0) / dlon)
        with rasterio.open(p) as ds:
            a = ds.read(1).astype(np.float32)
        if nodata is not None:
            a[a == nodata] = np.nan
        grid[r0 : r0 + h, c0 : c0 + w] = a
    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    lo, hi = spec.ix_range
    k = np.arange(HEIGHT_CELLS + 1, dtype=np.float64) * POST_M
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            x = ix * TILE_SIZE_M + k[None, :]  # east
            z = iz * TILE_SIZE_M + k[:, None]  # south
            lat, lon = to_geodetic_arrays(g, np.broadcast_to(-z, (k.size, k.size)), np.broadcast_to(x, (k.size, k.size)))
            fr, fc = (lat - lat0) / dlat, (lon - lon0) / dlon
            r, c = np.floor(fr).astype(int), np.floor(fc).astype(int)
            tr, tc = fr - r, fc - c
            v00, v01, v10, v11 = grid[r, c], grid[r, c + 1], grid[r + 1, c], grid[r + 1, c + 1]
            hgt = (v00 * (1 - tr) * (1 - tc) + v01 * (1 - tr) * tc + v10 * tr * (1 - tc) + v11 * tr * tc).astype(np.float32)
            if np.isnan(hgt).any():
                raise ValueError(f"tile {ix},{iz}: no elevation data at some posts")
            (out / heights_name(ix, iz)).write_bytes(hgt.astype("<f4").tobytes())
        log(f"heights: row {iz - lo + 1} of {hi - lo + 1}")


def build_landcover(spec: RegionSpec, lc_paths: list[Path], out: Path, log=print) -> None:
    """ESA WorldCover, the class at each cell centre (read in windows: each source tile is
    36000 pixels square)."""
    import rasterio
    from rasterio.windows import Window

    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    s, w, n, e = region_bounds_deg(spec)
    sources = []
    for p in lc_paths:
        with rasterio.open(p) as ds:
            t = ds.transform
            c0 = max(0, math.floor((w - t.c) / t.a) - 2)
            c1 = min(ds.width, math.ceil((e - t.c) / t.a) + 2)
            r0 = max(0, math.floor((n - t.f) / t.e) - 2)
            r1 = min(ds.height, math.ceil((s - t.f) / t.e) + 2)
            if c1 <= c0 or r1 <= r0:
                continue
            a = ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
            sources.append((a, t.c + c0 * t.a, t.a, t.f + r0 * t.e, t.e))
    lo, hi = spec.ix_range
    no_data = 0
    k = (np.arange(LANDCOVER_CELLS, dtype=np.float64) + 0.5) * (TILE_SIZE_M / LANDCOVER_CELLS)
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            x = np.broadcast_to(ix * TILE_SIZE_M + k[None, :], (k.size, k.size))
            z = np.broadcast_to(iz * TILE_SIZE_M + k[:, None], (k.size, k.size))
            lat, lon = to_geodetic_arrays(g, -z, x)
            cls = np.zeros(lat.shape, dtype=np.uint8)
            for a, west, dlon, north, dlat in sources:
                c = np.floor((lon - west) / dlon).astype(int)
                r = np.floor((lat - north) / dlat).astype(int)
                inside = (r >= 0) & (r < a.shape[0]) & (c >= 0) & (c < a.shape[1])
                cls[inside] = a[r[inside], c[inside]]
            no_data += int((cls == 0).sum())
            cls[cls == 0] = WATER  # WorldCover leaves the open sea without data
            (out / landcover_name(ix, iz)).write_bytes(cls.tobytes())
        log(f"land cover: row {iz - lo + 1} of {hi - lo + 1}")
    total = (hi - lo + 1) ** 2 * LANDCOVER_CELLS**2
    log(f"land cover: {no_data / total:.1%} of the cells without data (open sea), set to water")


def build_shore(spec: RegionSpec, out: Path, log=print) -> None:
    """Distance from each water cell (WorldCover class 80) to the nearest land, over the
    whole region (Euclidean, scipy.ndimage), for the viewer's shallow water colour."""
    from scipy.ndimage import distance_transform_edt

    lo, hi = spec.ix_range
    n = (hi - lo + 1) * LANDCOVER_CELLS
    water = np.zeros((n, n), dtype=bool)
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            r0, c0 = (iz - lo) * LANDCOVER_CELLS, (ix - lo) * LANDCOVER_CELLS
            cls = np.fromfile(out / landcover_name(ix, iz), np.uint8).reshape(LANDCOVER_CELLS, LANDCOVER_CELLS)
            water[r0 : r0 + LANDCOVER_CELLS, c0 : c0 + LANDCOVER_CELLS] = cls == WATER
    dist_m = distance_transform_edt(water) * (TILE_SIZE_M / LANDCOVER_CELLS)
    code = np.where(water, 1 + np.minimum(253, np.round(dist_m / SHORE_STEP_M)), 0).astype(np.uint8)
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            r0, c0 = (iz - lo) * LANDCOVER_CELLS, (ix - lo) * LANDCOVER_CELLS
            (out / shore_name(ix, iz)).write_bytes(code[r0 : r0 + LANDCOVER_CELLS, c0 : c0 + LANDCOVER_CELLS].tobytes())
    log(f"shore distance: {water.mean():.1%} water, at most {dist_m.max():.0f} m from land")


def build_imagery(spec: RegionSpec, paths: list[Path], out: Path, log=print) -> None:
    """Natural-colour imagery per tile from the scenes' red, green and blue reflectance:
    each pixel's centre through our map to the scenes' UTM grid (GDAL's transform),
    bilinear within a scene (the first scene in the region file's order with data there
    wins), then 255 x (gain x reflectance)^(1/gamma), the gain per band (a colour balance).
    JPEG, quality 88."""
    im = spec.sources["imagery"]
    scale, offset, gamma = (float(im[k]) for k in ("scale", "offset", "gamma"))
    gain = np.asarray(im["gain"] if isinstance(im["gain"], list) else [im["gain"]] * 3, np.float32)[:, None]  # per band
    import rasterio
    import rasterio.errors
    import rasterio.shutil
    from rasterio.io import MemoryFile
    from rasterio.warp import transform

    scenes = []
    for p in paths:
        with rasterio.open(p) as ds:
            scenes.append((ds.read().astype(np.float32), ~ds.transform, ds.crs))
    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    lo, hi = spec.ix_range
    k = (np.arange(IMAGERY_PX, dtype=np.float64) + 0.5) * (TILE_SIZE_M / IMAGERY_PX)
    missing = 0
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            x = np.broadcast_to(ix * TILE_SIZE_M + k[None, :], (k.size, k.size)).ravel()
            z = np.broadcast_to(iz * TILE_SIZE_M + k[:, None], (k.size, k.size)).ravel()
            lat, lon = to_geodetic_arrays(g, -z, x)
            rgb = np.zeros((3, lat.size), np.float32)
            done = np.zeros(lat.size, bool)
            projected = {}  # by CRS: the scenes of one UTM zone share it
            for data, inv, crs in scenes:
                if crs not in projected:
                    projected[crs] = tuple(np.asarray(v) for v in transform("EPSG:4326", crs, lon.tolist(), lat.tolist()))
                col, row = inv * projected[crs]
                col, row = col - 0.5, row - 0.5  # pixel centres
                c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
                ok = ~done & (c0 >= 0) & (r0 >= 0) & (c0 + 1 < data.shape[2]) & (r0 + 1 < data.shape[1])
                if not ok.any():
                    continue
                c, r, fc, fr = c0[ok], r0[ok], (col - c0)[ok], (row - r0)[ok]
                q = [data[:, r, c], data[:, r, c + 1], data[:, r + 1, c], data[:, r + 1, c + 1]]
                valid = np.all([v.min(axis=0) > 0 for v in q], axis=0)  # nodata 0 in any corner: not here
                v = q[0] * (1 - fc) * (1 - fr) + q[1] * fc * (1 - fr) + q[2] * (1 - fc) * fr + q[3] * fc * fr
                idx = np.nonzero(ok)[0][valid]
                rgb[:, idx] = v[:, valid]
                done[idx] = True
            missing += int((~done).sum())
            refl = np.clip(rgb * scale + offset, 0.0, None)
            tone = 255.0 * np.clip(gain * refl, 0.0, 1.0) ** (1.0 / gamma)
            img = np.where(done, np.round(tone), 0).clip(0, 255).astype(np.uint8).reshape(3, IMAGERY_PX, IMAGERY_PX)
            with MemoryFile() as mem, warnings.catch_warnings():
                warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)  # a plain picture, by design
                with mem.open(driver="GTiff", width=IMAGERY_PX, height=IMAGERY_PX, count=3, dtype="uint8") as m:
                    m.write(img)
                    with MemoryFile() as jpg:
                        rasterio.shutil.copy(m, jpg.name, driver="JPEG", QUALITY=88)
                        (out / imagery_name(ix, iz)).write_bytes(jpg.read())
        log(f"imagery: row {iz - lo + 1} of {hi - lo + 1}")
    log(f"imagery: {missing / ((hi - lo + 1) ** 2 * IMAGERY_PX**2):.2%} of the pixels without data (black)")


def _measured_heights(spec: RegionSpec, g: geo.Geodesy, tiles: dict, raster: Path, log=print) -> None:
    """Buildings whose height was estimated from their type (no OSM height or floors) take
    the measured average building height of their 100 m cell (GHS-BUILT-H) instead, where
    the cell has buildings; houses, villas and the like at most `small_kinds_max_m`."""
    import rasterio
    from rasterio.warp import transform

    small_max = float(spec.sources["building_height"].get("small_kinds_max_m", 12))
    with rasterio.open(raster) as ds:
        grid, inv, crs = ds.read(1), ~ds.transform, ds.crs
    todo = [(t, k) for t in tiles.values() for k, b in enumerate(t["buildings"]) if b[1] in ("estimate", "estimate_small")]
    if not todo:
        return
    cx = np.array([np.mean(t["buildings"][k][2][0::2]) for t, k in todo])
    cz = np.array([np.mean(t["buildings"][k][2][1::2]) for t, k in todo])
    lat, lon = to_geodetic_arrays(g, -cz, cx)
    mx, my = transform("EPSG:4326", crs, lon.tolist(), lat.tolist())
    col, row = inv * (np.asarray(mx), np.asarray(my))
    col, row = np.floor(col).astype(int), np.floor(row).astype(int)
    inside = (row >= 0) & (row < grid.shape[0]) & (col >= 0) & (col < grid.shape[1])
    h = np.where(inside, grid[np.clip(row, 0, grid.shape[0] - 1), np.clip(col, 0, grid.shape[1] - 1)], -1.0)
    used = 0
    for (t, k), hm in zip(todo, h):
        if hm >= 2.5:  # a cell with buildings
            b = t["buildings"][k]
            measured = min(float(hm), small_max) if b[1] == "estimate_small" else float(hm)
            t["buildings"][k] = [round(measured, 1), "measured", b[2]]
            used += 1
    log(f"building heights: {used} of {len(todo)} estimated buildings take the measured cell average")


def _inside(x: float, z: float, ring: list[float]) -> bool:
    """Point in polygon (even-odd), ring [x0, z0, x1, z1, ...]."""
    inside, n = False, len(ring) // 2
    for i in range(n):
        x0, z0, x1, z1 = ring[2 * i], ring[2 * i + 1], ring[2 * ((i + 1) % n)], ring[2 * ((i + 1) % n) + 1]
        if (z0 > z) != (z1 > z) and x < x0 + (z - z0) * (x1 - x0) / (z1 - z0):
            inside = not inside
    return inside


def _height_at(out: Path, x: float, z: float) -> float:
    """The built ground height at a world point (the nearest post; landmarks only)."""
    ix, iz = math.floor(x / TILE_SIZE_M), math.floor(z / TILE_SIZE_M)
    posts = np.fromfile(out / heights_name(ix, iz), "<f4").reshape(HEIGHT_CELLS + 1, HEIGHT_CELLS + 1)
    i, j = round((x - ix * TILE_SIZE_M) / POST_M), round((z - iz * TILE_SIZE_M) / POST_M)
    return max(0.0, float(posts[j, i]))


class _BuiltTerrain(DemTerrain):
    """The terrain of the tiles built so far (no manifest yet): height and water, loaded
    tile by tile as asked."""

    class _Tiles(dict):
        def __init__(self, out: Path, name, dtype, n):
            super().__init__()
            self.out, self.name, self.dtype, self.n = out, name, dtype, n

        def get(self, key, default=None):
            if key not in self:
                path = self.out / self.name(*key)
                self[key] = np.fromfile(path, self.dtype).reshape(self.n, self.n) if path.exists() else None
            return self[key] if self[key] is not None else default

    def __init__(self, out: Path):
        self.heights = self._Tiles(out, heights_name, "<f4", HEIGHT_CELLS + 1)
        self.landcover = self._Tiles(out, landcover_name, np.uint8, LANDCOVER_CELLS)


def _bridges(spec: RegionSpec, ways: list[dict], out: Path, log=print) -> None:
    """bridges.json (scenery_bridges.py): every OSM road and railway bridge as a deck
    profile; the region file's `bridges` (landmark bridges) set their own clearance."""
    from flightsim.world.scenery_bridges import build_bridges, landmark_summary

    t = _BuiltTerrain(out)
    bridges = build_bridges(ways, t.height_at, t.water_at, spec.sources.get("bridges") or [])
    (out / "bridges.json").write_text(json.dumps(bridges, separators=(",", ":"), sort_keys=True) + "\n")
    log(f"bridges: {len(ways)} OSM ways in {len(bridges)} bridges")
    for name, r in landmark_summary(bridges).items():
        log(f"bridge {name}: {r['chains']} decks, {r['length_m']:.0f} m in all, deck up to {r['deck_max_m']:.1f} m")
    for m in spec.sources.get("bridges") or []:
        if not any(b.get("landmark") == m["name"] for b in bridges):
            log(f"bridge {m['name']}: not found in the OSM extract")


def _osm_ids(mark: dict) -> list[str]:
    """A landmark's OSM objects: `osm` is one ("way/<id>") or a list."""
    return [mark["osm"]] if isinstance(mark["osm"], str) else list(mark["osm"])


def _landmarks(spec: RegionSpec, marks: list[dict], captured: dict, tiles: dict, out: Path, log=print, parts: dict | None = None) -> None:
    """landmarks.json: each landmark of the region file with its OSM footprints
    ("footprints": outer ring, inner rings such as a courtyard, and the height OSM's box
    had: its tag or the measured height, with the source), the first footprint's ring and
    long axis (world bearing of its longest side, degrees from north, clockwise), the
    centre and ground height; with `parts: true` also OSM's domes, building parts and
    pools on it (extract_parts). OSM buildings inside a `replace` landmark's footprints
    are dropped."""
    result = []
    for m in marks:
        shapes = [(o, captured[o]) for o in _osm_ids(m) if o in captured]
        missing = [o for o in _osm_ids(m) if o not in captured]
        if missing:
            log(f"landmark {m['name']}: {', '.join(missing)} not found in the OSM extract")
        if not shapes:
            continue
        footprints, dropped = [], 0
        for o, shape in shapes:
            ring = shape["outer"]
            fp = {"osm": o, "ring": [round(v, 1) for v in ring], "inner": [[round(v, 1) for v in r] for r in shape["inner"]], "height_m": None, "height_source": None}
            if m.get("replace"):
                best = 0.0
                for t in tiles.values():
                    keep = []
                    for b in t["buildings"]:
                        bx, bz = sum(b[2][0::2]) / (len(b[2]) / 2), sum(b[2][1::2]) / (len(b[2]) / 2)
                        if not _inside(bx, bz, ring):
                            keep.append(b)
                            continue
                        area = abs(sum(b[2][2 * k] * b[2][(2 * k + 3) % len(b[2])] - b[2][(2 * k + 2) % len(b[2])] * b[2][2 * k + 1] for k in range(len(b[2]) // 2))) / 2
                        if area > best:  # the largest box inside: the landmark's own
                            best, fp["height_m"], fp["height_source"] = area, b[0], b[1]
                    dropped += len(t["buildings"]) - len(keep)
                    t["buildings"] = keep
            footprints.append(fp)
        ring = shapes[0][1]["outer"]
        xs, zs = ring[0::2], ring[1::2]
        n = len(xs)
        side = max(range(n), key=lambda k: math.hypot(xs[(k + 1) % n] - xs[k], zs[(k + 1) % n] - zs[k]))
        dx, dz = xs[(side + 1) % n] - xs[side], zs[(side + 1) % n] - zs[side]
        axis = math.degrees(math.atan2(dx, -dz)) % 180.0  # bearing: east = 90
        allx = [x for _, s in shapes for x in s["outer"][0::2]]
        allz = [z for _, s in shapes for z in s["outer"][1::2]]
        cx, cz = sum(allx) / len(allx), sum(allz) / len(allz)
        entry = {**m, "footprints": footprints, "ring": footprints[0]["ring"], "inner": footprints[0]["inner"],
                 "centre": [round(cx, 1), round(cz, 1)], "axis_deg": round(axis, 2), "ground_m": round(_height_at(out, cx, cz), 2)}  # fmt: skip
        if m.get("parts"):
            entry.update((parts or {}).get(m["name"], {"domes": [], "building_parts": [], "pools": []}))
        if m["kind"] == "grand_mosque":
            # Qibla: the initial great-circle bearing to the Kaaba (21.4225 N, 39.8262 E), true
            # (the map bearing differs by the grid convergence, a few hundredths of a degree here).
            g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
            lat, lon = g.to_geodetic(-cz, cx)
            lat2, dlon = math.radians(21.4225), math.radians(39.8262) - lon
            entry["qibla_deg"] = round(math.degrees(math.atan2(math.sin(dlon) * math.cos(lat2),
                                       math.cos(lat) * math.sin(lat2) - math.sin(lat) * math.cos(lat2) * math.cos(dlon))) % 360.0, 2)  # fmt: skip
        result.append(entry)
        extra = f", {len(entry['domes'])} domes, {len(entry['building_parts'])} parts, {len(entry['pools'])} pools" if m.get("parts") else ""
        heights = ", ".join(f"{f['height_m']} m ({f['height_source']})" for f in footprints if f["height_m"] is not None)
        log(f"landmark {m['name']}: {len(footprints)} footprints, {dropped} OSM buildings replaced{extra}; OSM heights: {heights or 'none'}")
    (out / "landmarks.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")


# --- Airfields --------------------------------------------------------------------------------

# Runway flattening (project choices): the flat area reaches this far beyond the pavement's
# sides and ends, then blends into the terrain over BLEND_M.
RUNWAY_SIDE_M = 40.0
RUNWAY_END_M = 60.0
RUNWAY_BLEND_M = 150.0


def _smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def build_airfields(spec: RegionSpec, pbf: Path, out: Path, log=print) -> dict:
    from flightsim.world.scenery_osm import extract_airfields

    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    lo, hi = spec.ix_range
    half = max(abs(lo), hi + 1) * TILE_SIZE_M
    fields = extract_airfields(pbf, g, half)
    # The region's height posts as one grid: post (row, col) at x = x0 + col * POST_M
    # (east), z = x0 + row * POST_M (south).
    nt = hi - lo + 1
    npost = nt * HEIGHT_CELLS + 1
    grid = np.empty((npost, npost), dtype=np.float32)
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            r0, c0 = (iz - lo) * HEIGHT_CELLS, (ix - lo) * HEIGHT_CELLS
            grid[r0 : r0 + HEIGHT_CELLS + 1, c0 : c0 + HEIGHT_CELLS + 1] = np.fromfile(out / heights_name(ix, iz), "<f4").reshape(HEIGHT_CELLS + 1, -1)
    x0 = lo * TILE_SIZE_M
    original = grid.astype(np.float64)
    h = original.copy()

    def sample(xs, zs):  # bilinear in the original heights
        u, v = (xs - x0) / POST_M, (zs - x0) / POST_M
        i, j = np.floor(u).astype(int), np.floor(v).astype(int)
        fx, fz = u - i, v - j
        a = original[j, i] + (original[j, i + 1] - original[j, i]) * fx
        b = original[j + 1, i] + (original[j + 1, i + 1] - original[j + 1, i]) * fx
        return a + (b - a) * fz

    for rw in fields["runways"]:
        pub = (spec.airports.get((rw["airport"] or {}).get("icao") or "", {}).get("runways") or {}).get(rw["ref"], {})
        if "width_m" in pub:
            rw["width_m"], rw["width_source"] = float(pub["width_m"]), "published"
        elif rw["width_m"] is not None:
            rw["width_source"] = "osm"
        else:
            rw["width_m"], rw["width_source"] = spec.default_runway_width_m, "default"
        if "length_m" in pub:
            rw["published_length_m"] = float(pub["length_m"])
            if abs(rw["length_m"] - pub["length_m"]) > 30:
                log(f"runway {rw['ref']} ({rw['airport']['icao']}): OSM length {rw['length_m']:.0f} m, published {pub['length_m']} m")
        # Along the pavement (x = east, z = south) from the first end's pavement end.
        (n0, e0), (n1, e1) = rw["ends"][0]["pavement"], rw["ends"][1]["pavement"]
        p0, p1 = np.array([e0, -n0]), np.array([e1, -n1])
        length = float(np.linalg.norm(p1 - p0))
        d = (p1 - p0) / length
        nrm = np.array([-d[1], d[0]])
        s = np.linspace(0.0, length, max(2, int(length / POST_M) + 1))
        prof = sample(p0[0] + d[0] * s, p0[1] + d[1] * s)
        slope, base = np.polyfit(s, prof, 1)
        # Posts near the runway.
        reach = rw["width_m"] / 2 + RUNWAY_SIDE_M + RUNWAY_END_M + RUNWAY_BLEND_M
        xs = [p0[0], p1[0]]
        zs = [p0[1], p1[1]]
        c_lo = max(0, int(np.floor((min(xs) - reach - x0) / POST_M)))
        c_hi = min(npost - 1, int(np.ceil((max(xs) + reach - x0) / POST_M)))
        r_lo = max(0, int(np.floor((min(zs) - reach - x0) / POST_M)))
        r_hi = min(npost - 1, int(np.ceil((max(zs) + reach - x0) / POST_M)))
        if c_lo > c_hi or r_lo > r_hi:
            continue
        px = x0 + np.arange(c_lo, c_hi + 1)[None, :] * POST_M - p0[0]
        pz = x0 + np.arange(r_lo, r_hi + 1)[:, None] * POST_M - p0[1]
        along = px * d[0] + pz * d[1]
        cross = px * nrm[0] + pz * nrm[1]
        da = np.maximum(0.0, np.maximum(-along - RUNWAY_END_M, along - length - RUNWAY_END_M))
        dc = np.maximum(0.0, np.abs(cross) - (rw["width_m"] / 2 + RUNWAY_SIDE_M))
        w = 1.0 - _smoothstep(0.0, RUNWAY_BLEND_M, np.sqrt(da * da + dc * dc))
        flat = base + slope * np.clip(along, 0.0, length)
        block = h[r_lo : r_hi + 1, c_lo : c_hi + 1]
        h[r_lo : r_hi + 1, c_lo : c_hi + 1] = block * (1.0 - w) + flat * w
        for end in rw["ends"]:
            t = end["threshold"]
            a = float((np.array([t[1], -t[0]]) - p0) @ d)
            end["elevation_m"] = float(base + slope * min(max(a, 0.0), length))
            other = rw["ends"][1] if end is rw["ends"][0] else rw["ends"][0]
            end["heading_deg"] = math.degrees(math.atan2(other["threshold"][1] - t[1], other["threshold"][0] - t[0])) % 360.0
        rw["elevation_m"] = float(base + slope * length / 2)
        rw["slope_pct"] = float(slope * 100.0)
        ad = spec.airports.get((rw["airport"] or {}).get("icao") or "")
        if ad and "elevation_ft" in ad:
            rw["published_elevation_m"] = float(ad["elevation_ft"]) * 0.3048
        log(f"runway {rw['ref']} {(rw['airport'] or {}).get('icao') or ''}: {rw['length_m']:.0f} x {rw['width_m']:.0f} m, "
            f"elevation {rw['elevation_m']:.1f} m (slope {rw['slope_pct']:+.2f} %)")  # fmt: skip
    grid = h.astype(np.float32)
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            r0, c0 = (iz - lo) * HEIGHT_CELLS, (ix - lo) * HEIGHT_CELLS
            (out / heights_name(ix, iz)).write_bytes(grid[r0 : r0 + HEIGHT_CELLS + 1, c0 : c0 + HEIGHT_CELLS + 1].astype("<f4").tobytes())
    (out / "airfields.json").write_text(json.dumps(fields, indent=1, sort_keys=True) + "\n")
    return fields


# --- Manifest --------------------------------------------------------------------------------


def write_manifest(spec: RegionSpec, sources: list[dict], out: Path) -> dict:
    lo, hi = spec.ix_range
    files = {}
    for p in sorted((out / "tiles").iterdir()):
        files[f"tiles/{p.name}"] = sha256_file(p)
    for p in sorted(out.glob("*.json")):
        if p.name != "manifest.json":
            files[p.name] = sha256_file(p)
    manifest = {
        "format": FORMAT,
        "name": spec.name,
        "origin_lat_deg": spec.origin_lat_deg,
        "origin_lon_deg": spec.origin_lon_deg,
        "origin_airport": spec.origin_airport,
        "tiles": {"ix_min": lo, "ix_max": hi, "iz_min": lo, "iz_max": hi, "size_m": TILE_SIZE_M,
                  "height_cells": HEIGHT_CELLS, "landcover_cells": LANDCOVER_CELLS},
        "sources": [{k: f[k] for k in ("kind", "name", "url", "sha256")} for f in sources],
        "credits": spec.sources.get("credits", []),
        "credit_short": spec.sources.get("credit_short", ""),
        "files": files,
    }  # fmt: skip
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def build(spec: RegionSpec, root: Path = SCENERY_DIR, log=print) -> dict:
    out = root / spec.name
    sources = download(spec, root, log)
    (out / "tiles").mkdir(parents=True, exist_ok=True)
    src = out / "sources"
    build_heights(spec, [src / f["name"] for f in sources if f["kind"] == "dem"], out, log)
    build_landcover(spec, [src / f["name"] for f in sources if f["kind"] == "landcover"], out, log)
    build_shore(spec, out, log)
    imagery = [src / f["name"] for f in sources if f["kind"] == "imagery"]
    if imagery:
        build_imagery(spec, imagery, out, log)
    pbf = src / next(f["name"] for f in sources if f["kind"] == "osm")
    build_airfields(spec, pbf, out, log)
    bh = next((src / f["name"] for f in sources if f["kind"] == "building_height"), None)
    build_features(spec, pbf, out, log, building_height=bh)
    return write_manifest(spec, sources, out)


def build_features(spec: RegionSpec, pbf: Path, out: Path, log=print, building_height: Path | None = None) -> None:
    from flightsim.world.scenery_osm import extract_features, extract_parts

    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    lo, hi = spec.ix_range
    half = max(abs(lo), hi + 1) * TILE_SIZE_M
    marks = spec.sources.get("landmarks") or []
    tiles = extract_features(pbf, g, region_bounds_deg(spec, 0.0), half, TILE_SIZE_M, capture={o for m in marks for o in _osm_ids(m)})
    captured = tiles.pop("captured", {})
    _bridges(spec, tiles.pop("bridges", []), out, log)
    if building_height is not None:
        _measured_heights(spec, g, tiles, building_height, log)
    sites = {}
    for m in marks:
        rings = [captured[o]["outer"] for o in _osm_ids(m) if o in captured]
        if m.get("parts") and rings:
            xs = [x for r in rings for x in r[0::2]]
            zs = [z for r in rings for z in r[1::2]]
            cx, cz = sum(xs) / len(xs), sum(zs) / len(zs)
            sites[m["name"]] = (cx, cz, max(math.hypot(x - cx, z - cz) for x, z in zip(xs, zs)))
    parts = extract_parts(pbf, g, sites) if sites else {}
    _landmarks(spec, marks, captured, tiles, out, log, parts)
    empty = {"roads": {}, "rail": [], "taxiway": [], "apron": [], "buildings": []}
    counts = {"buildings": 0, "roads": 0}
    for iz in range(lo, hi + 1):
        for ix in range(lo, hi + 1):
            t = tiles.get((ix, iz), empty)
            t = {**t, "roads": dict(sorted(t["roads"].items()))}
            counts["buildings"] += len(t["buildings"])
            counts["roads"] += sum(len(v) for v in t["roads"].values())
            (out / features_name(ix, iz)).write_text(json.dumps(t, separators=(",", ":"), sort_keys=True))
    log(f"features: {counts['buildings']} buildings, {counts['roads']} road pieces")
