"""The viewer's C172 model (flightsim/viewer/aircraft.js), run with Node when available:
overall dimensions against the POH and JSBSim, and control surfaces deflecting the way
the logged positions mean (positive = trailing edge down; rudder: trailing edge left)."""

import json
import math
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
const box = new THREE.Box3(); // structure only: light glows (sprites) are not part of the airframe
m.group.traverse((o) => { if (o.isMesh) box.expandByObject(o, true); });
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
// Shading normals on each lifting surface's end sections must lie across the span: end
// caps that shared vertices with the skin bent them span-wise (one tip looked concave).
let worst = 0;
m.group.traverse((o) => {
  const u = o.geometry?.userData;
  if (!o.isMesh || !u?.ringSize) return;
  const p = o.geometry.attributes.position, n = o.geometry.attributes.normal, per = u.ringSize;
  const centre = (s) => { const c = new THREE.Vector3(); for (let k = 0; k < per; k++) c.add(new THREE.Vector3().fromBufferAttribute(p, s * per + k)); return c.divideScalar(per); };
  const span = centre(u.sections - 1).sub(centre(0)).normalize();
  for (const s of [0, u.sections - 1]) for (let k = 0; k < per; k++) {
    worst = Math.max(worst, Math.abs(new THREE.Vector3().fromBufferAttribute(n, s * per + k).dot(span)));
  }
});
out.worstSpanwiseNormal = worst;
// Gear settling: at JSBSim's rest (CG 1.3276 m above the ground, pitch 2.45 deg; flightsim
// reset_on_ground), heading north, on a surface 3 cm up: every tyre bottom on it.
const pose = (h, thDeg) => {
  const th = (thDeg * Math.PI) / 180;
  return new THREE.Matrix4().makeBasis(new THREE.Vector3(0, Math.sin(th), -Math.cos(th)), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, -Math.cos(th), -Math.sin(th))).setPosition(0, h, 0);
};
const bottoms = (mat) => m.gears.map((g) => g.contact.clone().add(g.group.position).applyMatrix4(mat).y);
const rest = pose(1.3276, 2.45);
out.unsettled = bottoms(rest);
m.settle(rest, () => 0.03);
out.settled = bottoms(rest);
out.rise = m.gears.map((g) => -g.group.position.z);
m.settle(pose(10, 2.45), () => 0.03);
out.airborne = m.gears.map((g) => -g.group.position.z);
out.shadowHigh = m.gears.map((g) => g.shadow.visible);
m.settle(rest, () => 0.03);
// The contact shadow: flat (its normal world up) just above the surface, under the tyre.
out.shadow = m.gears.map((g) => { g.shadow.updateMatrix = () => {}; m.group.matrix.copy(rest); m.group.matrixAutoUpdate = false; m.group.updateMatrixWorld(true);
  const c = new THREE.Vector3().setFromMatrixPosition(g.shadow.matrixWorld), n = new THREE.Vector3(0, 1, 0).transformDirection(g.shadow.matrixWorld);
  const t = g.contact.clone().add(g.group.position).applyMatrix4(rest);
  return { visible: g.shadow.visible, y: c.y, ny: n.y, dx: Math.hypot(c.x - t.x, c.z - t.z) }; });
// Strut and gear-leg roots inside the fuselage: a ray from the root sideways crosses the
// fuselage's skin an odd number of times (rested pose, gear settled).
{
  m.group.matrix.identity(); m.group.updateMatrixWorld(true);
  const fus = m.group.children[0], roots = [];
  m.group.traverse((o) => o.userData.root && roots.push(o));
  const rc = new THREE.Raycaster();
  out.roots = roots.map((o) => {
    const p = o.userData.root.clone().applyMatrix4(o.parent.matrixWorld);
    const side = p.y >= 0 ? 1 : -1;
    rc.set(p, new THREE.Vector3(0, side, 0));
    const a = rc.intersectObject(fus).length;
    rc.set(p, new THREE.Vector3(0, -side, 0));
    return [a, rc.intersectObject(fus).length];
  });
}
// Wing struts: their top ends (structural inches) in the wing (tests compare with the airfoil).
{
  const tips = [];
  m.group.traverse((o) => { if (o.userData.tipStruct && Math.abs(o.userData.tipStruct[1]) > 60) tips.push(o.userData.tipStruct); });
  out.strutTips = tips;
}
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
    for f in ("aircraft.js", "camera.js"):
        shutil.copy(VIEWER / f, d / f)
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


def test_tip_shading_is_not_bent_by_end_caps(model):
    assert model["worstSpanwiseNormal"] < 0.35


def test_gear_settles_on_the_surface(model):
    assert all(y < 0.03 for y in model["unsettled"])  # JSBSim's contacts sit in the ground at rest
    assert model["settled"] == pytest.approx([0.015] * len(model["settled"]), abs=0.003)  # tyres on the surface, 1.5 cm flat spot
    assert all(0 < r < 0.3 for r in model["rise"])
    assert model["airborne"] == [0] * len(model["airborne"])  # nothing moves in the air


def test_contact_shadow_under_each_tyre(model):
    assert model["shadowHigh"] == [False] * 3  # 10 m up: none
    for sh in model["shadow"]:
        assert sh["visible"] and sh["y"] == pytest.approx(0.034, abs=0.002) and sh["ny"] == pytest.approx(1, abs=1e-6) and sh["dx"] < 0.01


def test_struts_and_legs_start_inside_the_fuselage(model):
    assert len(model["roots"]) == 5  # two wing struts, two main legs, the nose leg
    for out_side, in_side in model["roots"]:
        assert out_side % 2 == 1  # inside the skin: one crossing outward


def test_wing_struts_end_inside_the_wing(model):
    # The airfoil (aircraft.js naca: NACA 2412-like, t 0.12, m 0.02, p 0.4) at the strut's
    # station: its top end between the lower and upper surface, not under the wing.
    t, m_, p = 0.12, 0.02, 0.4
    assert len(model["strutTips"]) == 2
    for x, y, z in model["strutTips"]:
        le, chord = 26, 64  # inner panel (|y| <= 100: constant chord); y 102: 64.1 in, LE 26.07
        f = (x - le) / chord
        yt = 5 * t * (0.2969 * math.sqrt(f) - 0.126 * f - 0.3516 * f**2 + 0.2843 * f**3 - 0.1036 * f**4)
        yc = (m_ / p**2) * (2 * p * f - f * f) if f < p else (m_ / (1 - p) ** 2) * (1 - 2 * p + 2 * p * f - f * f)
        chord_z = 65 + abs(y) * math.tan(math.radians(1.73))
        lower, upper = chord_z + (yc - yt) * chord, chord_z + (yc + yt) * chord
        assert lower + 0.8 < z < upper  # (half the strut's 1.6 in thickness inside too)
