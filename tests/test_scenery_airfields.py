"""Airfields of a real-world region (world/scenery_osm.py, scenery_build.build_airfields):
runways from OSM ways (split ways joined, displaced threshold sections extend the pavement,
each end named by its landing number, helipads left out, the aerodrome with an ICAO code
preferred); published widths applied; the ground along each runway flattened onto a
straight slope and blended into the terrain. With the Abu Dhabi region built, the task
configs use its runway 31 as built. Needs the `scenery` group (osmium) for the OSM part."""

import json
import math

import numpy as np
import pytest
import yaml

from flightsim.world import geo
from flightsim.world.scenery import HEIGHT_CELLS, POST_M, SCENERY_DIR, TILE_SIZE_M, heights_name
from flightsim.world.scenery_build import RegionSpec

ORIGIN = (24.0, 54.0)
G = geo.Geodesy("wgs84", *ORIGIN)


def _ll(north, east):
    lat, lon = G.to_geodetic(north, east)
    return math.degrees(lat), math.degrees(lon)


def _osm(path):
    """Runway 09/27, 1600 m east-west through the origin, split in two ways, with a 200 m
    displaced threshold section at the west end; helipad H1/H2; two aerodromes on it
    (one without an ICAO code, nearer); a 07/25 strip 7 km north with no aerodrome."""
    nodes = {1: (0, -800), 2: (0, 0), 3: (0, 800), 4: (0, -1000), 5: (100, 0), 6: (100, 30),
             7: (7000, -300), 8: (7000 + 600 * math.sin(math.radians(20)), -300 + 600 * math.cos(math.radians(20))),
             10: (10, 10), 11: (300, 300)}  # fmt: skip
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6">']
    for i, (n, e) in nodes.items():
        lat, lon = _ll(n, e)
        tags = ""
        if i == 10:
            tags = '<tag k="aeroway" v="aerodrome"/><tag k="name" v="Duplicate"/>'
        if i == 11:
            tags = '<tag k="aeroway" v="aerodrome"/><tag k="icao" v="TEST"/><tag k="name:en" v="Test Field"/>'
        xml.append(f'<node id="{i}" version="1" lat="{lat:.9f}" lon="{lon:.9f}">{tags}</node>')
    ways = [(100, [1, 2], {"aeroway": "runway", "ref": "09/27", "surface": "asphalt"}),
            (101, [2, 3], {"aeroway": "runway", "ref": "09/27", "surface": "asphalt"}),
            (102, [4, 1], {"aeroway": "runway", "ref": "09/27", "runway": "displaced_threshold"}),
            (103, [5, 6], {"aeroway": "runway", "ref": "H1/H2"}),
            (104, [7, 8], {"aeroway": "runway", "ref": "07/25", "width": "18 m"})]  # fmt: skip
    for wid, nds, tags in ways:
        xml.append(f'<way id="{wid}" version="1">' + "".join(f'<nd ref="{n}"/>' for n in nds)
                   + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()) + "</way>")  # fmt: skip
    xml.append("</osm>")
    path.write_text("\n".join(xml))
    return path


def test_runways_from_osm(tmp_path):  # (OSM keeps positions to 1e-7 deg, about 1 cm)
    pytest.importorskip("osmium")
    from flightsim.world.scenery_osm import extract_airfields

    f = extract_airfields(_osm(tmp_path / "t.osm"), G, 8000)
    rws = {r["ref"]: r for r in f["runways"]}
    assert set(rws) == {"09/27", "07/25"}  # the helipad is left out
    r = rws["09/27"]
    assert r["osm_ways"] == [100, 101, 102]
    assert r["airport"] == {"icao": "TEST", "name": "Test Field"}  # the ICAO one, though farther
    e09, e27 = r["ends"]
    assert e09["ident"] == "09" and e27["ident"] == "27"
    assert e09["threshold"] == pytest.approx((0, -800), abs=0.02)  # 09 lands eastward: west end
    assert e09["pavement"] == pytest.approx((0, -1000), abs=0.02)  # displaced section beyond it
    assert e27["threshold"] == pytest.approx((0, 800), abs=0.02) and e27["pavement"] == e27["threshold"]
    assert r["length_m"] == pytest.approx(1800, abs=0.02)
    s = rws["07/25"]
    assert s["airport"] is None and s["width_m"] == 18.0
    assert s["ends"][0]["ident"] == "07" and s["ends"][0]["threshold"] == pytest.approx((7000, -300), abs=0.02)


def test_runways_are_flattened(tmp_path):
    pytest.importorskip("osmium")
    from flightsim.world.scenery_build import build_airfields

    spec = RegionSpec(name="t", origin_lat_deg=ORIGIN[0], origin_lon_deg=ORIGIN[1], tiles_radius=1, sources={},
                      airports={"TEST": {"elevation_ft": 30, "runways": {"09/27": {"width_m": 40, "length_m": 1800}}}})  # fmt: skip
    (tmp_path / "tiles").mkdir()
    rng = np.random.default_rng(4)
    n = HEIGHT_CELLS + 1
    full = (10 + rng.uniform(-3, 3, (2 * HEIGHT_CELLS + 1,) * 2)).astype("<f4")
    for iz in (-1, 0):
        for ix in (-1, 0):
            r0, c0 = (iz + 1) * HEIGHT_CELLS, (ix + 1) * HEIGHT_CELLS
            (tmp_path / heights_name(ix, iz)).write_bytes(full[r0 : r0 + n, c0 : c0 + n].tobytes())
    fields = build_airfields(spec, _osm(tmp_path / "t.osm"), tmp_path, log=lambda *_: None)
    r = next(x for x in fields["runways"] if x["ref"] == "09/27")
    assert r["width_m"] == 40 and r["width_source"] == "published"
    assert r["published_elevation_m"] == pytest.approx(30 * 0.3048)
    assert r["ends"][0]["heading_deg"] == pytest.approx(90.0, abs=1e-4) and r["ends"][1]["heading_deg"] == pytest.approx(270.0, abs=1e-4)
    assert {x["ref"] for x in json.loads((tmp_path / "airfields.json").read_text())["runways"]} == {"09/27"}  # the strip 7 km north is outside this 8 km region
    out = np.empty_like(full)
    for iz in (-1, 0):
        for ix in (-1, 0):
            r0, c0 = (iz + 1) * HEIGHT_CELLS, (ix + 1) * HEIGHT_CELLS
            out[r0 : r0 + n, c0 : c0 + n] = np.fromfile(tmp_path / heights_name(ix, iz), "<f4").reshape(n, n)
    post = lambda north, east: (round((-north + TILE_SIZE_M) / POST_M), round((east + TILE_SIZE_M) / POST_M))  # noqa: E731
    along = [out[post(0.0, e)] for e in np.arange(-1000, 801, POST_M * 4)]
    assert np.ptp(along) < 0.2  # a straight, nearly level line (the random terrain was +/-3 m)
    assert out[post(30.0, 0.0)] == pytest.approx(out[post(0.0, 0.0)], abs=0.05)  # flat across the strip too
    assert out[post(-1500.0, 0.0)] == full[post(-1500.0, 0.0)]  # far away: untouched
    assert r["elevation_m"] == pytest.approx(10.0, abs=0.5)


# --- The Abu Dhabi configs against the built region ------------------------------------------

AIRFIELDS = SCENERY_DIR / "abu_dhabi" / "airfields.json"


@pytest.mark.skipif(not AIRFIELDS.exists(), reason="Abu Dhabi scenery not built (scripts/build_scenery.py)")
@pytest.mark.parametrize("name", ["approach", "approach_crosswind", "takeoff", "takeoff_crosswind", "circuit", "manual_approach"])
def test_abu_dhabi_tasks_use_runway_31_as_built(name):
    rw = next(r for r in json.loads(AIRFIELDS.read_text())["runways"] if (r["airport"] or {}).get("icao") == "OMAD")
    e31 = next(e for e in rw["ends"] if e["ident"] == "31")
    e13 = next(e for e in rw["ends"] if e["ident"] == "13")
    raw = yaml.safe_load(open(f"configs/envs/abu_dhabi_{name}.yaml"))
    cfg = (raw.get("approach") or raw.get("takeoff"))["runway"]
    assert cfg["threshold_north_m"] == pytest.approx(e31["threshold"][0], abs=0.1)
    assert cfg["threshold_east_m"] == pytest.approx(e31["threshold"][1], abs=0.1)
    assert cfg["heading_deg"] == pytest.approx(e31["heading_deg"], abs=0.01)
    assert cfg["length_m"] == pytest.approx(math.dist(e31["threshold"], e13["pavement"]), abs=0.1)
    assert cfg["width_m"] == rw["width_m"]
