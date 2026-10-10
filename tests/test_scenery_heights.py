"""Building heights from GlobalBuildingAtlas (flightsim/world/scenery_heights.py), on
synthetic files: a building without an OSM height takes the GBA footprint's height under
its centre plus the bias, at least the minimum, houses capped; OSM's own heights and
buildings without a GBA footprint are kept; GBA's no-data (-999) is ignored."""

import json
import math

import pytest

pytest.importorskip("rasterio")

from flightsim.world import geo  # noqa: E402
from flightsim.world.scenery_build import load_spec  # noqa: E402
from flightsim.world.scenery_heights import WEB_MERCATOR_R, gba_heights  # noqa: E402


def _merc(g, x, z):
    lat, lon = g.to_geodetic(-z, x)
    return [lon * WEB_MERCATOR_R, WEB_MERCATOR_R * math.log(math.tan(math.pi / 4 + lat / 2))]


def test_gba_heights(tmp_path):
    spec = load_spec("configs/scenery/abu_dhabi.yaml")
    spec.sources["gba_heights"] = {"files": [{"name": "h.json", "url": ""}, {"name": "p.geojson", "url": ""}], "bias_m": 2.0, "min_m": 3.0}
    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    sq = lambda x, z, h=10: [[x - h, z - h], [x + h, z - h], [x + h, z + h], [x - h, z + h], [x - h, z - h]]  # noqa: E731
    feats = [(100, 100, "a", 20.0), (300, 100, "b", 0.5), (500, 100, "c", -999.0), (700, 100, "d", 15.0)]
    (tmp_path / "p.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"source": "ms", "id": i, "region": "ARE"},
         "geometry": {"type": "Polygon", "coordinates": [[_merc(g, px, pz) for px, pz in sq(x, z)]]}} for x, z, i, _ in feats]}))  # fmt: skip
    (tmp_path / "h.json").write_text(json.dumps({f"ms{i}ARE": {"height": h, "var": 1.0} for _, _, i, h in feats}))
    ring = lambda x, z: [v for p in sq(x, z, 5)[:-1] for v in p]  # noqa: E731
    tiles = {(0, 0): {"buildings": [
        [9.0, "estimate", ring(100, 100)],         # -> 20 + 2
        [9.0, "estimate", ring(300, 100)],         # 0.5 + 2 -> the 3 m minimum
        [9.0, "estimate", ring(500, 100)],         # no data: kept
        [7.0, "estimate_small", ring(700, 100)],   # 15 + 2 -> the houses' cap (12)
        [40.0, "height", ring(100, 100)],          # OSM's own: kept
        [9.0, "estimate", ring(900, 900)],         # no GBA footprint: kept
    ]}}  # fmt: skip
    gba_heights(spec, g, tiles, tmp_path, log=lambda m: None)
    got = [(b[0], b[1]) for b in tiles[(0, 0)]["buildings"]]
    assert got == [(22.0, "gba"), (3.0, "gba"), (9.0, "estimate"), (12.0, "gba"), (40.0, "height"), (9.0, "estimate")]
