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
5. manifest.json: the region, its sources, every file's sha256.

The same sources and code give byte-identical files.
"""

import hashlib
import json
import math
import struct
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from flightsim.world import geo
from flightsim.world.scenery import (
    FORMAT, HEIGHT_CELLS, LANDCOVER_CELLS, POST_M, SCENERY_DIR, TILE_SIZE_M, heights_name, landcover_name,
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
        tiles_radius=int(raw["tiles_radius"]), sources=raw["sources"], pinned=raw.get("pinned", {}) or {},
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
    build_airfields(spec, src / next(f["name"] for f in sources if f["kind"] == "osm"), out, log)
    return write_manifest(spec, sources, out)
