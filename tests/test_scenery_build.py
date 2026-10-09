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
