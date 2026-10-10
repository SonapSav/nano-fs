"""Landmark models (flightsim/viewer/landmarks.js, landmarkKit.js), run with Node when
available: walls face out of a footprint (into a courtyard for inner rings) and carry the
facade coordinates; a leaning tower's top sits where its average lean puts it; every kind
builds from a landmarks.json-like entry. (Their sun shadows: test_viewer_sun_shadows.py.)"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
const THREE = await import("three");
const kit = await import("./landmarkKit.js");
const { Landmarks } = await import("./landmarks.js");
const out = {};
const square = [0, 0, 10, 0, 10, 10, 0, 10]; // x east, z south: clockwise seen from above
const normalsOf = (g) => {
  const p = g.attributes.position, n = g.attributes.normal, res = [];
  for (let i = 0; i < p.count; i += 6) res.push([(p.getX(i) + p.getX(i + 2)) / 2 - 5, (p.getZ(i) + p.getZ(i + 2)) / 2 - 5, n.getX(i), n.getY(i), n.getZ(i)]);
  return res;
};
out.outer = normalsOf(kit.walls(square, 0, 10, 2));
out.inner = normalsOf(kit.walls(square, 0, 10, 2, { out: false }));
const f = kit.walls(square, 0, 10, 2).attributes.facade;
out.facadeLast = [f.getX(f.count - 1), f.getY(f.count - 1), f.getZ(f.count - 1), f.getW(f.count - 1)];

const ring = [-20, -20, 20, -20, 20, 20, -20, 20];
const big = [-100, -100, 100, -100, 100, 100, -100, 100];
const marks = [
  { name: "M", kind: "grand_mosque", ring: big, inner: [[-50, -50, 50, -50, 50, 50, -50, 50]], centre: [0, 0], axis_deg: 90, ground_m: 12,
    roof_m: 33, dome_diameter_m: 32.6, dome_top_m: 84, minaret_m: 107, qibla_deg: 260,
    domes: [{ x: -70, z: 0, d: 37, top: 85 }, { x: 70, z: 70, d: 10, top: 43 }], pools: [[110, 0, 130, 0, 130, 20, 110, 20]] },
  { name: "T", kind: "leaning_tower", ring, centre: [0, 0], axis_deg: 0, ground_m: 3, height_m: 160, lean_deg: 18, lean_toward_deg: 270,
    storeys: 35, vertical_storeys: 12, diagrid_panels: 700, splash_toward_deg: 294 },
  { name: "L", kind: "flat_dome", ring: big, centre: [0, 0], axis_deg: 45, ground_m: 0, dome_diameter_m: 180, pier_spacing_m: 110, frame_m: 5,
    museum_buildings: 55, domes: [{ x: 0, z: 0, d: 182, top: null, min: 10, roof_h: 20 }] },
  { name: "P", kind: "palace", ring: big, inner: [], centre: [0, 0], axis_deg: 0, ground_m: 9, footprints: [{ ring: big, inner: [], height_m: 27.4 }],
    dome_diameter_m: 42, dome_top_m: 72.6, colour: "sand", mosaic: true, domes: [{ x: 0, z: 0, d: 41, top: 45 }, { x: 60, z: 60, d: 12, top: 32, colour: "golden" }],
    building_parts: [], pools: [] },
  { name: "E", kind: "glass_towers", ring, centre: [0, 0], axis_deg: 0, ground_m: 2, footprints: [{ ring, inner: [], height_m: 305 }] },
];
for (const m of marks) {
  const lm = new Landmarks(new THREE.Scene());
  lm.build([m]);
  const box = new THREE.Box3().setFromObject(lm.group.children[0]);
  out[m.name] = { min: box.min.toArray(), max: box.max.toArray(), meshes: lm.group.children[0].children.length };
}
// The leaning tower's top storey: its centre's offset.
{
  const lm = new Landmarks(new THREE.Scene());
  lm.build([marks[1]]);
  const roof = lm.group.children[0].children[1].geometry;
  roof.computeBoundingBox();
  const c = roof.boundingBox.getCenter(new THREE.Vector3());
  out.leanTop = c.toArray();
}
// Shadows: two landmarks far apart; the camera near the second.
// Static merging (staticMerge.js): two placements of a group with two materials, one part
// mirrored, one transparent, one hidden: one mesh per opaque material, every triangle
// still facing away from the part's centre.
{
  const { mergeByMaterial } = await import("./staticMerge.js");
  const a = new THREE.MeshLambertMaterial(), b = new THREE.MeshLambertMaterial(), glass = new THREE.MeshBasicMaterial({ transparent: true });
  const grp = new THREE.Group();
  const box = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), a);
  const mirrored = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), a);
  mirrored.position.set(3, 0, 0);
  mirrored.scale.set(-1, 1, 1);
  const other = new THREE.Mesh(new THREE.SphereGeometry(1), b), clear = new THREE.Mesh(new THREE.BoxGeometry(), glass), hidden = new THREE.Mesh(new THREE.BoxGeometry(), b);
  hidden.visible = false;
  grp.add(box, mirrored, other, clear, hidden);
  const meshes = mergeByMaterial([{ object: grp, matrix: new THREE.Matrix4() }, { object: grp, matrix: new THREE.Matrix4().makeTranslation(0, 10, 0) }]);
  const ga = meshes.find((m) => m.material === a).geometry;
  // Winding: the geometric normal of each triangle points away from its box's centre.
  let outward = 0;
  const p = ga.attributes.position, v = [0, 1, 2].map(() => new THREE.Vector3()), n = new THREE.Vector3(), c = new THREE.Vector3();
  for (let t = 0; t < p.count; t += 3) {
    v.forEach((w, i) => w.fromBufferAttribute(p, t + i));
    n.subVectors(v[1], v[0]).cross(new THREE.Vector3().subVectors(v[2], v[0]));
    const mid = v[0].clone().add(v[1]).add(v[2]).divideScalar(3);
    c.set(mid.x > 1.5 ? 3 : 0, mid.y > 5 ? 10 : 0, 0);
    if (n.dot(mid.sub(c)) > 0) outward++;
  }
  out.merge = { meshes: meshes.length, triangles: p.count / 3, outward };
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("landmarks")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js","./addons/*":"./addons/*"}}')
    for f in ("landmarks.js", "landmarkKit.js", "staticMerge.js"):
        shutil.copy(VIEWER / f, d / f)
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_walls_face_out_or_into_a_courtyard(result):
    for name, sign in (("outer", 1), ("inner", -1)):
        for mx, mz, nx, ny, nz in result[name]:
            assert ny == pytest.approx(0, abs=1e-6)
            assert sign * (mx * nx + mz * nz) > 0  # along the outward direction from the centre (or against it)
    along, up, height, bay = result["facadeLast"]
    assert along == pytest.approx(40) and up == pytest.approx(10) and height == 10 and bay == 2  # perimeter, top, wall height, bay


def test_leaning_tower_top(result):
    x, y, z = result["leanTop"]
    assert y == pytest.approx(3 + 160)
    assert x == pytest.approx(-160 * math.tan(math.radians(18)), abs=0.5) and z == pytest.approx(0, abs=0.5)  # west by H tan 18


def test_every_kind_builds(result):
    assert result["M"]["max"][1] == pytest.approx(12 + 107, abs=0.5)  # the minarets' tips
    assert result["P"]["max"][1] == pytest.approx(9 + 72.6, abs=0.5)  # the main dome at its published height, not OSM's
    assert result["E"]["max"][1] == pytest.approx(2 + 305)
    assert result["L"]["max"][1] == pytest.approx(0 + 10 + 20, abs=0.1)  # OSM: rim at min_height, top at min_height + roof:height
    assert result["L"]["max"][0] - result["L"]["min"][0] >= 180
    for k in "MTLPE":
        assert result[k]["meshes"] >= 2


def test_static_merge_joins_per_material(result):
    m = result["merge"]
    assert m["meshes"] == 2  # two opaque materials; the transparent and the hidden parts left out
    assert m["triangles"] == 2 * 2 * 12 and m["outward"] == m["triangles"]  # two boxes, twice; the mirrored one turned back
