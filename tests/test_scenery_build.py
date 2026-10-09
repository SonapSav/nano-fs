"""Real-world scenery build (flightsim/world/scenery_build.py): map positions on arrays match
world/geo.py; heights are FABDEM sampled bilinearly at the posts; land cover is the class
at each cell centre, the open sea (no data) water; source tiles and pinning. The raster
tests need the `scenery` dependency group (uv run --group scenery pytest ...)."""

import math

import numpy as np
import pytest

from flightsim.world import geo
from flightsim.world.scenery import HEIGHT_CELLS, LANDCOVER_CELLS, POST_M, TILE_SIZE_M, heights_name, landcover_name
from flightsim.world.scenery_build import RegionSpec, region_bounds_deg, source_files, to_geodetic_arrays

SPEC = RegionSpec(
    name="test", origin_lat_deg=24.4284648, origin_lon_deg=54.4580337, tiles_radius=1,
    sources={"dem": {"zip_url": "https://example/dem.zip"}, "landcover": {"url": "https://example/WC_{tile}_Map.tif"},
             "osm": {"url": "https://example/area-261008.osm.pbf"}},
)  # fmt: skip


def test_array_positions_match_geo():
    g = geo.Geodesy("wgs84", SPEC.origin_lat_deg, SPEC.origin_lon_deg)
    rng = np.random.default_rng(1)
    n, e = rng.uniform(-60000, 60000, 200), rng.uniform(-60000, 60000, 200)
    lat, lon = to_geodetic_arrays(g, n, e)
    for i in range(n.size):
        la, lo = g.to_geodetic(float(n[i]), float(e[i]))
        assert lat[i] == pytest.approx(math.degrees(la), abs=1e-12)
        assert lon[i] == pytest.approx(math.degrees(lo), abs=1e-12)


def test_source_tiles_cover_the_region():
    s, w, n, e = region_bounds_deg(SPEC)
    assert s < SPEC.origin_lat_deg < n and w < SPEC.origin_lon_deg < e
    names = [f["name"] for f in source_files(SPEC)]
    assert names == ["N24E054_FABDEM_V1-2.tif", "WC_N24E054_Map.tif", "area-261008.osm.pbf"]  # 12 km box inside one tile


def _geotiff(path, array, west, north, step, nodata=None):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    with rasterio.open(path, "w", driver="GTiff", width=array.shape[1], height=array.shape[0], count=1, dtype=array.dtype,
                       crs="EPSG:4326", transform=from_origin(west, north, step, step), nodata=nodata) as ds:  # fmt: skip
        ds.write(array, 1)


def test_heights_are_the_dem_sampled_at_the_posts(tmp_path):
    pytest.importorskip("rasterio")
    from flightsim.world.scenery_build import build_heights

    # A plane in latitude and longitude: bilinear sampling reproduces it exactly.
    step = 1 / 3600
    lats = 25.0 - np.arange(3600) * step  # pixel centres, as FABDEM (corner half a pixel out)
    lons = 54.0 + np.arange(3600) * step
    dem = (100 + 2000 * (lats[:, None] - 24.4) + 500 * (lons[None, :] - 54.4)).astype(np.float32)
    _geotiff(tmp_path / "dem.tif", dem, 54.0 - step / 2, 25.0 + step / 2, step, nodata=-9999.0)
    (tmp_path / "tiles").mkdir()
    build_heights(SPEC, [tmp_path / "dem.tif"], tmp_path, log=lambda *_: None)
    g = geo.Geodesy("wgs84", SPEC.origin_lat_deg, SPEC.origin_lon_deg)
    h = np.fromfile(tmp_path / heights_name(0, -1), "<f4").reshape(HEIGHT_CELLS + 1, HEIGHT_CELLS + 1)
    for row, col in [(0, 0), (5, 77), (128, 128), (64, 3)]:
        x, z = col * POST_M, -TILE_SIZE_M + row * POST_M  # tile (0, -1): east 0..4 km, north 0..4 km
        la, lo = (math.degrees(v) for v in g.to_geodetic(-z, x))
        assert h[row, col] == pytest.approx(100 + 2000 * (la - 24.4) + 500 * (lo - 54.4), abs=2e-3)
    # Neighbouring tiles share their edge posts.
    right = np.fromfile(tmp_path / heights_name(0, 0), "<f4").reshape(HEIGHT_CELLS + 1, -1)
    assert np.array_equal(h[-1, :], right[0, :])


def test_landcover_classes_and_open_sea(tmp_path):
    pytest.importorskip("rasterio")
    from flightsim.world.scenery_build import build_landcover

    step = 1 / 12000  # WorldCover: 10 m
    a = np.full((2400, 1200), 60, np.uint8)  # desert, 24.3-24.5 N, 54.4-54.5 E
    a[:, 600:] = 50  # built-up east of 54.45 E
    a[:300, :] = 0  # no data north of 24.475 N
    _geotiff(tmp_path / "wc.tif", a, 54.4, 24.5, step, nodata=0)
    (tmp_path / "tiles").mkdir()
    build_landcover(SPEC, [tmp_path / "wc.tif"], tmp_path, log=lambda *_: None)
    g = geo.Geodesy("wgs84", SPEC.origin_lat_deg, SPEC.origin_lon_deg)
    lc = np.fromfile(tmp_path / landcover_name(-1, 0), np.uint8).reshape(LANDCOVER_CELLS, LANDCOVER_CELLS)
    cell = TILE_SIZE_M / LANDCOVER_CELLS
    for row in range(0, LANDCOVER_CELLS, 37):
        for col in range(0, LANDCOVER_CELLS, 41):
            x, z = -TILE_SIZE_M + (col + 0.5) * cell, (row + 0.5) * cell
            la, lo = (math.degrees(v) for v in g.to_geodetic(-z, x))
            expect = 80 if la > 24.475 else 50 if lo >= 54.45 else 60
            assert lc[row, col] == expect


def test_pinning_rewrites_only_the_pinned_block(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("build_scenery", Path(__file__).parent.parent / "scripts" / "build_scenery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    region = tmp_path / "r.yaml"
    region.write_text("name: r\npinned: {}\n\nafter: 1\n")
    module.pin(region, [{"name": "b.tif", "sha256": "22"}, {"name": "a.tif", "sha256": "11"}])
    assert region.read_text() == "name: r\npinned:\n  a.tif: 11\n  b.tif: 22\nafter: 1\n"
    module.pin(region, [{"name": "a.tif", "sha256": "33"}])
    assert region.read_text() == "name: r\npinned:\n  a.tif: 33\nafter: 1\n"


def test_shore_distance(tmp_path):
    from flightsim.world.scenery import SHORE_STEP_M, landcover_name, shore_name
    from flightsim.world.scenery_build import build_shore

    (tmp_path / "tiles").mkdir()
    # Tiles -1..0 square; land in the west half of tile column -1, water elsewhere.
    for iz in (-1, 0):
        for ix in (-1, 0):
            lc = np.full((LANDCOVER_CELLS, LANDCOVER_CELLS), 80, np.uint8)
            if ix == -1:
                lc[:, : LANDCOVER_CELLS // 2] = 60
            (tmp_path / landcover_name(ix, iz)).write_bytes(lc.tobytes())
    build_shore(SPEC, tmp_path, log=lambda *_: None)
    cell = TILE_SIZE_M / LANDCOVER_CELLS
    w = np.fromfile(tmp_path / shore_name(0, 0), np.uint8).reshape(LANDCOVER_CELLS, LANDCOVER_CELLS)
    w_west = np.fromfile(tmp_path / shore_name(-1, 0), np.uint8).reshape(LANDCOVER_CELLS, LANDCOVER_CELLS)
    assert (w_west[:, : LANDCOVER_CELLS // 2] == 0).all()  # land
    assert w_west[5, LANDCOVER_CELLS // 2] == 1 + round(cell / SHORE_STEP_M)  # the first water cell: one cell from land
    # Across the tile edge: tile (0, 0)'s first column is 129 cells from the land (no seam).
    assert w[5, 0] == 1 + min(253, round((LANDCOVER_CELLS // 2 + 1) * cell / SHORE_STEP_M))
    assert w[5, -1] == 254  # capped (about 2 km)


def test_imagery_mosaic_and_tone(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    from rasterio.warp import transform

    from flightsim.world.scenery import IMAGERY_PX, imagery_name
    from flightsim.world.scenery_build import build_imagery

    # Two scenes in UTM 39N around the origin: the first covers only the west half of the
    # region (red-ish), the second all of it (blue-ish); reflectance = value x 1e-4 - 0.1.
    (ox,), (oy,) = transform("EPSG:4326", "EPSG:32639", [SPEC.origin_lon_deg], [SPEC.origin_lat_deg])
    west, north, n = ox - 5000, oy + 5000, 1000  # 10 km square, 10 m pixels

    def scene(path, rgb, cols):
        a = np.zeros((3, n, n), np.uint16)
        for b, v in enumerate(rgb):
            a[b, :, :cols] = v
        with rasterio.open(path, "w", driver="GTiff", width=n, height=n, count=3, dtype="uint16", crs="EPSG:32639",
                           transform=from_origin(west, north, 10, 10), nodata=0) as ds:  # fmt: skip
            ds.write(a)

    scene(tmp_path / "a.tif", (1000 + 3000, 1000 + 1000, 1000 + 500), n // 2)  # reflectance 0.3, 0.1, 0.05
    scene(tmp_path / "b.tif", (1000 + 500, 1000 + 1000, 1000 + 3000), n)
    (tmp_path / "tiles").mkdir()
    spec = RegionSpec(**{**SPEC.__dict__, "sources": {"imagery": {"scale": 1e-4, "offset": -0.1, "gain": [2.0, 2.0, 2.0], "gamma": 1.0}}})
    build_imagery(spec, [tmp_path / "a.tif", tmp_path / "b.tif"], tmp_path, log=lambda *_: None)
    with rasterio.open(tmp_path / imagery_name(-1, 0)) as ds:  # west of the origin: scene a
        west_px = ds.read()[:, IMAGERY_PX // 2, IMAGERY_PX // 2]
    with rasterio.open(tmp_path / imagery_name(0, 0)) as ds:  # east: only scene b
        east_px = ds.read()[:, IMAGERY_PX // 2, IMAGERY_PX // 2]
    assert west_px == pytest.approx([153, 51, 26], abs=4)  # 255 x 2 x reflectance (JPEG: a few levels)
    assert east_px == pytest.approx([26, 51, 153], abs=4)
