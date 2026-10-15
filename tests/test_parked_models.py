"""Parked models: the region file's parked_models placed on the map by the scenery build
(scenery_build.parked_models), the AW139 model the viewer loads
(flightsim/viewer/models/aw139.glb, built by scripts/blender/aw139.py) against the
dimensions it was built to, and the vendored GLTFLoader reading it (Node, when available)."""

import json
import math
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from flightsim.world import geo
from flightsim.world.scenery_build import RegionSpec, load_spec, parked_models

ROOT = Path(__file__).parent.parent
VIEWER = ROOT / "flightsim" / "viewer"
GLB = VIEWER / "models" / "aw139.glb"
NODE = shutil.which("node")


def _spec(models, origin=(24.4284648, 54.4580337)):
    return RegionSpec(name="t", origin_lat_deg=origin[0], origin_lon_deg=origin[1], tiles_radius=1, sources={},
                      airports={"OMAD": {"parked_models": models}})  # fmt: skip


def test_parked_models_are_placed_on_the_map_with_the_map_heading():
    g = geo.Geodesy("wgs84", 24.4284648, 54.4580337)
    lat, lon = g.to_geodetic(-1053.0, 1197.0)
    out = parked_models(_spec([{"model": "aw139", "lat_deg": math.degrees(lat), "lon_deg": math.degrees(lon), "heading_deg": 37.5, "turning": True}]), g)
    assert len(out) == 1
    m = out[0]
    assert (m["airport"], m["model"], m["turning"]) == ("OMAD", "aw139", True)
    assert m["north"] == pytest.approx(-1053.0, abs=0.01) and m["east"] == pytest.approx(1197.0, abs=0.01)
    # map bearing = true bearing - grid convergence
    assert m["heading_deg"] == pytest.approx(37.5 - math.degrees(g.convergence_rad(lat, lon)), abs=1e-3)
    assert parked_models(_spec([{"model": "aw139", "lat_deg": 24.43, "lon_deg": 54.46, "heading_deg": 0}]), g)[0]["turning"] is False


def test_parked_models_must_name_a_model_of_the_viewer():
    g = geo.Geodesy("wgs84", 24.4284648, 54.4580337)
    for name in ("no_such_model", "../aw139", "AW139"):
        with pytest.raises(ValueError, match="parked model"):
            parked_models(_spec([{"model": name, "lat_deg": 24.43, "lon_deg": 54.46, "heading_deg": 0}]), g)


def test_the_region_parks_the_aw139_at_omad():
    spec = load_spec(ROOT / "configs" / "scenery" / "abu_dhabi.yaml")
    out = parked_models(spec, geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg))
    assert [(m["airport"], m["model"], m["turning"]) for m in out] == [("OMAD", "aw139", True)]


def _gltf():
    b = GLB.read_bytes()
    magic, version, length = struct.unpack("<4sII", b[:12])
    assert (magic, version, length) == (b"glTF", 2, len(b))
    n, kind = struct.unpack("<I4s", b[12:20])
    assert kind == b"JSON"
    return json.loads(b[20 : 20 + n])


def _bounds(j, names):
    """Bounding box (min, max) of the named nodes' meshes (the accessors' min/max; the nodes
    carry no transforms: the build places every vertex)."""
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for node in j["nodes"]:
        if not any(node["name"].startswith(n) for n in names):
            continue
        assert "translation" not in node and "rotation" not in node and "scale" not in node
        for prim in j["meshes"][node["mesh"]]["primitives"]:
            a = j["accessors"][prim["attributes"]["POSITION"]]
            lo = [min(x, y) for x, y in zip(lo, a["min"])]
            hi = [max(x, y) for x, y in zip(hi, a["max"])]
    return lo, hi


def test_the_aw139_model_has_the_published_dimensions():
    """x nose, y up, z right; origin on the ground under the main rotor shaft. Leonardo's
    three-view: wheelbase 4.34 m, main track 3.04 m, stabiliser 4.22 m, tail rotor 2.70 m
    across with its shaft 8.40 m behind the main one, main rotor 13.80 m, height 4.98 m to the
    top of the tail rotor."""
    j = _gltf()
    (nx0, ny0, _), (nx1, _, _) = _bounds(j, ["AW139_NoseWheelLTyre"])
    (mx0, my0, mz0), (mx1, _, mz1) = _bounds(j, ["AW139_MainWheelLTyre"])
    assert (nx0 + nx1) / 2 - (mx0 + mx1) / 2 == pytest.approx(4.34, abs=0.02)
    assert ny0 == pytest.approx(0.0, abs=0.01) and my0 == pytest.approx(0.0, abs=0.01)  # standing on the ground
    assert 2 * abs((mz0 + mz1) / 2) == pytest.approx(3.04, abs=0.02)
    (_, _, sz0), (_, _, sz1) = _bounds(j, ["AW139_Stabilizer", "AW139_EndPlate"])
    assert sz1 - sz0 == pytest.approx(4.22, abs=0.1)
    # tail rotor: each blade's farthest box corner from the hub (x -8.40, y 3.70) about a
    # 1.35 m radius (the boxes reach a little past the tips: chord)
    reach = []
    for i in range(4):
        (x0, y0, _), (x1, y1, _) = _bounds(j, [f"AW139_TailBlade{i}"])
        reach.append(max(math.hypot(x + 8.40, y - 3.70) for x in (x0, x1) for y in (y0, y1)))
    assert all(1.35 <= r < 1.45 for r in reach)
    (_, _, _), (_, ty1, _) = _bounds(j, ["AW139_TailBlade"])
    assert ty1 == pytest.approx(4.98, abs=0.1)
    # main rotor: the same about the shaft (13.80 m across; the 5 deg tilt shortens it by 0.4 %)
    reach = []
    for i in range(5):
        (x0, _, z0), (x1, _, z1) = _bounds(j, [f"AW139_MainBlade{i}"])
        reach.append(max(math.hypot(x, z) for x in (x0, x1) for z in (z0, z1)))
    assert all(6.85 <= r < 7.1 for r in reach)
    (fx0, _, fz0), (fx1, fy1, fz1) = _bounds(j, ["AW139_Fuselage"])
    assert fx1 == pytest.approx(4.66, abs=0.02) and fz1 - fz0 == pytest.approx(2.2, abs=0.05)


def test_the_aw139_model_marks_its_turning_parts_and_decals():
    j = _gltf()
    spin = {n["name"]: n["extras"] for n in j["nodes"] if "spin_axis" in n.get("extras", {})}
    main = {k for k, v in spin.items() if v["spin_rev_per_s"] == pytest.approx(296 / 60)}  # 296 rpm, 100 % NR
    tail = {k for k, v in spin.items() if v["spin_rev_per_s"] == 6.0}
    assert main == {"AW139_Mast", "AW139_HubPlate", "AW139_HubCap", *(f"AW139_MainBlade{i}" for i in range(5))}
    assert tail == {"AW139_TailHub", *(f"AW139_TailBlade{i}" for i in range(4))}
    # blade counts: the viewer caps each rotor's turn per frame to a share of its blade spacing
    assert {spin[k]["spin_blades"] for k in main} == {5} and {spin[k]["spin_blades"] for k in tail} == {4}
    m = spin["AW139_MainBlade0"]
    assert m["spin_pivot"] == pytest.approx([0, 3.1, 0]) and math.degrees(math.atan2(m["spin_axis"][0], m["spin_axis"][1])) == pytest.approx(5.0)  # tilted forward
    t = spin["AW139_TailBlade0"]
    assert t["spin_pivot"] == pytest.approx([-8.4, 3.7, 0.3]) and t["spin_axis"] == pytest.approx([0, 0, -1])  # right side, axis across
    assert {m["name"] for m in j["materials"] if (m.get("extras") or {}).get("polygon_offset")} == {"AW139_Glass", "AW139_Grille"}


def test_the_aw139_model_fits_its_budget():
    j = _gltf()
    tris = sum(j["accessors"][p["indices"]]["count"] // 3 for m in j["meshes"] for p in m["primitives"])
    assert tris < 60_000 and GLB.stat().st_size < 1_500_000
    assert not j.get("images") and not j.get("textures")  # plain materials: nothing else to fetch


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_vendored_gltf_loader_reads_the_model_and_its_extras(tmp_path):
    three = tmp_path / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    shutil.copytree(VIEWER / "vendor" / "addons", three / "addons")
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js","./addons/*":"./addons/*"}}')
    script = f"""
const {{ GLTFLoader }} = await import("three/addons/loaders/GLTFLoader.js");
const fs = await import("node:fs");
const b = fs.readFileSync({json.dumps(str(GLB))});
new GLTFLoader().parse(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), "", (g) => {{
  let meshes = 0, spin = 0, offset = new Set();
  g.scene.traverse((o) => {{ if (!o.isMesh) return; meshes++; if (o.userData.spin_axis) spin++; if (o.material.userData.polygon_offset) offset.add(o.material.name); }});
  console.log(JSON.stringify({{ meshes, spin, offset: [...offset].sort() }}));
}}, (e) => {{ console.error(e); process.exit(1); }});
"""
    r = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {"meshes": 55, "spin": 13, "offset": ["AW139_Glass", "AW139_Grille"]}
