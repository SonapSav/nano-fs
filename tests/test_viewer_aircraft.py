"""The viewer's C172 model (flightsim/viewer/aircraft.js), run with Node when available:
overall dimensions against the POH and JSBSim, and control surfaces deflecting the way
the logged positions mean (positive = trailing edge down; rudder: trailing edge left)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
IN = 0.0254

SCRIPT = """
const THREE = await import("three");
const { buildC172 } = await import("./aircraft.js");
const m = buildC172();
m.update({});
m.group.updateMatrixWorld(true);
const box = new THREE.Box3().setFromObject(m.group);
// Most aft point of a control surface (its trailing edge), in body axes.
const te = (pivot) => {
  pivot.updateMatrixWorld(true);
  const p = pivot.children[0].geometry.attributes.position, v = new THREE.Vector3();
  let best = null;
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i).applyMatrix4(pivot.children[0].matrixWorld);
    if (!best || v.x < best.x) best = v.clone();
  }
  return best.toArray();
};
const names = { elevator: "elevator_pos_rad", aileronL: "aileron_left_pos_rad", aileronR: "aileron_right_pos_rad", flapL: "flap_pos_rad", flapR: "flap_pos_rad", rudder: "rudder_pos_rad" };
const out = { size: box.getSize(new THREE.Vector3()).toArray(), neutral: {}, deflected: {} };
for (const k of Object.keys(names)) out.neutral[k] = te(m.pivots[k]);
const row = {};
for (const k of Object.keys(names)) row[names[k]] = 0.3;
m.update(row);
for (const k of Object.keys(names)) out.deflected[k] = te(m.pivots[k]);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    d = tmp_path_factory.mktemp("aircraft")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    shutil.copy(VIEWER / "aircraft.js", d / "aircraft.js")
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_dimensions_match_the_poh_and_jsbsim(model):
    length, span, height = model["size"]  # body axes: x forward, y right, z down
    assert length == pytest.approx((26 * 12 + 11) * IN, abs=0.15)  # POH: 26 ft 11 in
    assert span == pytest.approx(35.8 * 12 * IN, abs=0.15)  # JSBSim c172p wingspan (POH 36 ft 1 in with strobes)
    assert height == pytest.approx((8 * 12 + 9.5) * IN, abs=0.15)  # POH: 8 ft 9.5 in


def test_surfaces_deflect_the_way_logged_positions_mean(model):
    n, d = model["neutral"], model["deflected"]
    for k in ("elevator", "aileronL", "aileronR", "flapL", "flapR"):
        assert d[k][2] - n[k][2] > 0.05, k  # trailing edge down (body z is down)
    assert d["rudder"][1] - n["rudder"][1] < -0.05  # trailing edge left (body y is right)
