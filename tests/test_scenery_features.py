"""OpenStreetMap features of a real-world region: building heights (tagged height, floors,
or an estimate), lines cut at tile edges, areas by centroid (world/scenery_osm.py, needs
osmium for the extraction); and their meshes in the viewer (viewer/featureGeometry.js,
run with Node): roads draped on the ground, ear clipping, buildings extruded with outward
walls and upward roofs."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from flightsim.world import geo
from flightsim.world.scenery_osm import _polylines_by_tile, building_height

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")


def test_building_heights():
    assert building_height({"height": "120 m"}, 900) == (120.0, "height")
    assert building_height({"building:levels": "10"}, 900) == (10 * 3.3 + 1.0, "levels")
    assert building_height({"building": "villa"}, 400)[1] == "estimate_small"  # capped when a measured height replaces it
    assert building_height({"building": "yes", "height": "65 ft"}, 100) == (7.0, "estimate_small")  # feet: not understood, estimated
    assert building_height({"building": "commercial"}, 6000) == (15.0, "estimate")


def test_lines_are_cut_at_tile_edges():
    tiles = _polylines_by_tile([(100.0, 100.0), (5000.0, 100.0), (5000.0, 3000.0)], 4000.0)
    assert set(tiles) == {(0, 0), (1, 0)}
    assert tiles[(0, 0)] == [[100.0, 100.0, 4000.0, 100.0]]
    assert tiles[(1, 0)] == [[4000.0, 100.0, 5000.0, 100.0, 5000.0, 3000.0]]  # joined across its own corner


def test_features_from_osm(tmp_path):
    pytest.importorskip("osmium")
    from flightsim.world.scenery_osm import extract_features

    g = geo.Geodesy("wgs84", 24.0, 54.0)
    ll = lambda n, e: [math.degrees(v) for v in g.to_geodetic(n, e)]  # noqa: E731
    pts = {1: (100, -100), 2: (100, 6000), 3: (-1000, 1000), 4: (-1000, 1040), 5: (-1030, 1040), 6: (-1030, 1000), 7: (50, 50), 8: (60, 60)}
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6">']
    for i, (n, e) in pts.items():
        lat, lon = ll(n, e)
        xml.append(f'<node id="{i}" version="1" lat="{lat:.9f}" lon="{lon:.9f}"/>')
    ways = [(10, [1, 2], {"highway": "primary"}), (11, [3, 4, 5, 6, 3], {"building": "yes", "building:levels": "4"}),
            (12, [7, 8], {"highway": "footway"})]  # fmt: skip
    for wid, nds, tags in ways:
        xml.append(f'<way id="{wid}" version="1">' + "".join(f'<nd ref="{n}"/>' for n in nds)
                   + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()) + "</way>")  # fmt: skip
    xml.append("</osm>")
    (tmp_path / "t.osm").write_text("\n".join(xml))
    f = extract_features(tmp_path / "t.osm", g, (23.9, 53.9, 24.1, 54.1), 8000, 4000)
    roads = {k: v["roads"] for k, v in f.items() if v["roads"]}
    assert set(roads) == {(-1, -1), (0, -1), (1, -1)}  # x -100..6000 at z = -100 (north 100): three tiles
    assert all(set(r) == {"primary"} for r in roads.values())  # the footway is left out
    (b,) = [b for t in f.values() for b in t["buildings"]]
    assert b[0] == pytest.approx(4 * 3.3 + 1.0) and b[1] == "levels" and len(b[2]) == 8  # 4 corners, x z each
    corners = {(round(b[2][k]), round(b[2][k + 1])) for k in range(0, 8, 2)}
    assert corners == {(1000, 1000), (1040, 1000), (1040, 1030), (1000, 1030)}  # x east, z south


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_feature_meshes():
    script = f"""
    const fg = await import({json.dumps((VIEWER / "featureGeometry.js").as_uri())});
    const d = await import({json.dumps((VIEWER / "demCore.js").as_uri())});
    const n = d.HEIGHT_CELLS + 1;
    const h = new Float32Array(n * n).fill(10);
    const tiles = {{ heights: (ix, iz) => (ix === 0 && iz === 0 ? h : null), landcover: () => null }};
    const f = {{
      roads: {{ primary: [[100, 100, 300, 100]] }}, rail: [], taxiway: [],
      apron: [[1000, 1000, 1100, 1000, 1100, 1100, 1050, 1050, 1000, 1100]],  // concave
      buildings: [[20, "levels", [2000, 2000, 2030, 2000, 2030, 2020, 2000, 2020]], [80, "height", [3000, 3000, 3010, 3000, 3010, 3010, 3000, 3010]]],
    }};
    const g = fg.featureGroundData(f, tiles);
    const b = fg.buildingData(f, tiles, 0), tall = fg.buildingData(f, tiles, 50);
    const ys = (m) => [...m.position].filter((_, i) => i % 3 === 1);
    // Wall normals point away from the footprint centre; roof normals up.
    let outward = true;
    for (let i = 0; i < b.position.length / 3; i++) {{
      const nx = b.normal[3 * i], nz = b.normal[3 * i + 2];
      if (nx === 0 && nz === 0) continue;
      const cx = b.position[3 * i] < 2500 ? 2015 : 3005, cz = b.position[3 * i + 2] < 2500 ? 2010 : 3005;
      if ((b.position[3 * i] - cx) * nx + (b.position[3 * i + 2] - cz) * nz <= 0) outward = false;
    }}
    console.log(JSON.stringify({{
      roadY: [Math.min(...ys(g.roads)), Math.max(...ys(g.roads))], roadTris: g.roads.index.length / 3,
      apronTris: g.paved.index.length / 3, concave: fg.triangulate([0, 0, 10, 0, 10, 10, 5, 5, 0, 10]).length / 3,
      top: Math.max(...ys(b)), outward, tallCount: tall.position.length / 3, allCount: b.position.length / 3,
    }}));
    """
    out = json.loads(subprocess.run([NODE, "--input-type=module"], input=script, capture_output=True, text=True, check=True).stdout)
    assert out["roadY"] == pytest.approx([10.35, 10.35])  # on the ground, lifted 0.35 m
    assert out["roadTris"] == 2 * math.ceil(200 / 25)
    assert out["apronTris"] == 3 and out["concave"] == 3  # 5 corners: 3 triangles
    assert out["top"] == pytest.approx(10 + 80)
    assert out["outward"]
    assert 0 < out["tallCount"] < out["allCount"]  # only the 80 m tower on distant tiles


def test_landmark_parts_and_courtyard(tmp_path):
    """A building with a courtyard (multipolygon with an inner ring), a dome part on it and
    a pool beside it: the capture keeps the courtyard; extract_parts finds the dome (centre,
    diameter, top) and the pool near the site, not the far dome."""
    pytest.importorskip("osmium")
    from flightsim.world.scenery_osm import extract_features, extract_parts

    g = geo.Geodesy("wgs84", 24.0, 54.0)
    ll = lambda n, e: [math.degrees(v) for v in g.to_geodetic(n, e)]  # noqa: E731
    sq = lambda i, n, e, h: {i: (n - h, e - h), i + 1: (n - h, e + h), i + 2: (n + h, e + h), i + 3: (n + h, e - h)}  # noqa: E731
    pts = {**sq(1, 0, 0, 100), **sq(11, 0, 0, 40), **sq(21, 70, 70, 5), **sq(31, -70, 0, 10), **sq(41, 3000, 3000, 5)}
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6">']
    for i, (n, e) in pts.items():
        lat, lon = ll(n, e)
        xml.append(f'<node id="{i}" version="1" lat="{lat:.9f}" lon="{lon:.9f}"/>')
    way = lambda wid, first, tags: (f'<way id="{wid}" version="1">' + "".join(f'<nd ref="{first + k % 4}"/>' for k in range(5))  # noqa: E731
                                    + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()) + "</way>")  # fmt: skip
    xml += [way(100, 1, {}), way(101, 11, {}), way(102, 21, {"building:part": "dome", "height": "43"}),
            way(103, 31, {"natural": "water", "water": "pond"}), way(104, 41, {"building:part": "dome", "height": "20"}),
            '<relation id="7" version="1"><member type="way" ref="100" role="outer"/><member type="way" ref="101" role="inner"/>'
            '<tag k="type" v="multipolygon"/><tag k="building" v="mosque"/></relation>', "</osm>"]  # fmt: skip
    (tmp_path / "t.osm").write_text("\n".join(xml))
    f = extract_features(tmp_path / "t.osm", g, (23.9, 53.9, 24.1, 54.1), 8000, 4000, capture={"relation/7"})
    shape = f["captured"]["relation/7"]
    assert len(shape["outer"]) == 8 and len(shape["inner"]) == 1 and len(shape["inner"][0]) == 8
    assert max(abs(v) for v in shape["inner"][0]) == pytest.approx(40, abs=0.2)
    parts = extract_parts(tmp_path / "t.osm", g, {"m": (0.0, 0.0, 150.0)})["m"]
    (dome,) = parts["domes"]
    assert (dome["x"], dome["z"], dome["d"], dome["top"]) == (pytest.approx(70, abs=0.2), pytest.approx(-70, abs=0.2), pytest.approx(10, abs=0.2), 43.0)
    assert dome["min"] is None and parts["building_parts"] == []
    assert len(parts["pools"]) == 1 and len(parts["pools"][0]) == 8


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_curved_road_is_one_mitred_strip():
    """A road with an L-turn and a gentle curve (the owner's photo: teeth on the outside of
    bends): one continuous strip, every triangle facing the same way (no folds), the outer
    corner of the L at the mitre (half width x sqrt 2 from the centreline point)."""
    script = f"""
    const fg = await import({json.dumps((VIEWER / "featureGeometry.js").as_uri())});
    const d = await import({json.dumps((VIEWER / "demCore.js").as_uri())});
    const n = d.HEIGHT_CELLS + 1, h = new Float32Array(n * n).fill(10);
    const tiles = {{ heights: (ix, iz) => (ix === 0 && iz === 0 ? h : null), landcover: () => null }};
    const curve = [];
    for (let a = 0; a <= 90; a += 10) curve.push(500 + 100 * Math.sin(a * Math.PI / 180), 500 + 100 - 100 * Math.cos(a * Math.PI / 180));
    const out = {{}};
    for (const [name, line] of [["L", [100, 100, 200, 100, 200, 200]], ["curve", curve]]) {{
      const g = fg.featureGroundData({{ roads: {{ primary: [line] }} }}, tiles).roads;
      const P = (i) => [g.position[3 * i], g.position[3 * i + 2]];
      const signs = new Set();
      for (let t = 0; t < g.index.length; t += 3) {{
        const [a, b, c] = [0, 1, 2].map((k) => P(g.index[t + k]));
        signs.add(Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])));
      }}
      out[name] = {{ signs: [...signs], verts: g.position.length / 3, corner: name === "L" ? Math.max(...[...Array(g.position.length / 3).keys()].map((i) => Math.hypot(P(i)[0] - 200, P(i)[1] - 100)).filter((r) => r < 20)) : null }};
    }}
    console.log(JSON.stringify(out));
    """
    out = json.loads(subprocess.run([NODE, "--input-type=module"], input=script, capture_output=True, text=True, check=True).stdout)
    for k in ("L", "curve"):
        assert out[k]["signs"] == [out[k]["signs"][0]] and out[k]["signs"][0] != 0  # no folded or degenerate triangles
    assert out["L"]["corner"] == pytest.approx(7 * math.sqrt(2), abs=1e-3)  # primary: 14 m wide
    assert out["L"]["verts"] == 2 * (1 + 4 + 4)  # one strip: start, then 4 drape steps per 100 m segment (no mesh: every 25 m)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_roads_lie_on_the_drawn_terrain():
    """The owner's photo: the drawn terrain (a tile mesh of planar triangles, coarser than
    the heights) rose through straight roads on embankments. On a bumpy tile, a road
    crossing it diagonally: every road vertex sits exactly ROAD_LIFT_M above the drawn
    mesh (demTiles.js, same segments), and between vertices the mesh never comes within
    0.3 m of the road's top (sampled every metre along both edges)."""
    script = f"""
    const fg = await import({json.dumps((VIEWER / "featureGeometry.js").as_uri())});
    const dt = await import({json.dumps((VIEWER / "demTiles.js").as_uri())});
    const d = await import({json.dumps((VIEWER / "demCore.js").as_uri())});
    const n = d.HEIGHT_CELLS + 1, h = new Float32Array(n * n);
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) h[r * n + c] = 10 + 4 * Math.sin(c * 0.9) * Math.cos(r * 0.7) + (c % 3 === 0 ? 3 : 0);
    const lc = new Uint8Array(d.LANDCOVER_CELLS ** 2).fill(60);
    const tiles = {{ heights: (ix, iz) => (ix === 0 && iz === 0 ? h : null), landcover: (ix, iz) => (ix === 0 && iz === 0 ? lc : null), shore: () => null }};
    const S = 96, mesh = dt.demTileGeometryData(0, 0, S, tiles);
    // The mesh's own height at (x, z): find its triangle (same split as demTiles.js).
    const step = d.TILE_SIZE_M / S, N = S + 1, Y = (i, j) => mesh.position[3 * (i * N + j) + 1];
    const meshY = (x, z) => {{ const u = x / step, v = z / step, j = Math.floor(u), i = Math.floor(v), fx = u - j, fz = v - i;
      if (fx + fz <= 1) return Y(i, j) + (Y(i, j + 1) - Y(i, j)) * fx + (Y(i + 1, j) - Y(i, j)) * fz;
      return Y(i + 1, j + 1) + (Y(i + 1, j) - Y(i + 1, j + 1)) * (1 - fx) + (Y(i, j + 1) - Y(i + 1, j + 1)) * (1 - fz); }};
    const g = fg.featureGroundData({{ roads: {{ primary: [[300, 410, 2100, 1290, 3500, 1300]] }} }}, tiles, S).roads;
    let vertexErr = 0, minClear = Infinity;
    const P = (k) => [g.position[3 * k], g.position[3 * k + 1], g.position[3 * k + 2]];
    for (let k = 0; k < g.position.length / 3; k++) {{ const [x, y, z] = P(k); vertexErr = Math.max(vertexErr, Math.abs(y - 0.35 - meshY(x, z))); }}
    for (let k = 0; k + 2 < g.position.length / 3; k++) {{ // along each edge: vertex k to k + 2
      const a = P(k), b = P(k + 2), len = Math.hypot(b[0] - a[0], b[2] - a[2]);
      for (let s = 0; s <= len; s += 1) {{ const t = len ? s / len : 0, x = a[0] + (b[0] - a[0]) * t, z = a[2] + (b[2] - a[2]) * t;
        minClear = Math.min(minClear, a[1] + (b[1] - a[1]) * t - meshY(x, z)); }}
    }}
    console.log(JSON.stringify({{ vertexErr, minClear }}));
    """
    out = json.loads(subprocess.run([NODE, "--input-type=module"], input=script, capture_output=True, text=True, check=True).stdout)
    assert out["vertexErr"] < 1e-3
    assert out["minClear"] > 0.3
