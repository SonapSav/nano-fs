"""Bridge models (flightsim/viewer/bridges.js), run with Node when available: a deck
with its parapets and piers only where it stands high enough, and each landmark kind
(Sheikh Zayed's wave of arches to its published 60 m, Al Maqta's tied arch over the
water, Sheikh Khalifa's haunched girder and V-piers) builds without gaps or NaNs."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
const THREE = await import("three");
const { Bridges } = await import("./bridges.js");
// A straight bridge west to east, 1000 m, water from 300 to 700 m; deck profile like the build's.
const line = (z, top, shape = (x) => Math.max(3, Math.min(top, 3 + 0.05 * Math.min(x, 1000 - x)))) => {
  const pts = [];
  for (let x = 0; x <= 1000; x += 10) pts.push([x, z, shape(x), x >= 300 && x <= 700 ? 0 : 3, x >= 300 && x <= 700 ? 1 : 0]);
  return pts;
};
const base = { kind: "roads", cls: "motorway", width_m: 17, depth_m: 2.5, length_m: 1000 };
const cases = {
  plain: [{ ...base, ids: [1], pts: line(0, 12) }],
  zayed: [0, 40].map((z, i) => ({ ...base, ids: [10 + i], pts: line(z, 18.5), landmark: "Z",
           structure: { kind: "wave_arch", main_arch_m: 235, arch_top_m: 60, side_arch_m: 150, side_arch_top_m: 32 } })),
  maqta: [{ ...base, ids: [20], pts: line(0, 7.5), landmark: "M", structure: { kind: "tied_arch", arch_rise_ratio: 0.18 } }],
  khalifa: [{ ...base, ids: [30], depth_m: 3.5, pts: line(0, 35, (x) => Math.min(35, 3 + 0.1 * Math.min(x, 1000 - x))), landmark: "K",
             structure: { kind: "box_girder", main_spans_m: [110, 200, 135, 70, 45], girder_max_m: 10.25, v_pier_deg: 27.45 } }],
  low: [{ ...base, ids: [40], pts: line(0, 4, () => 4) }],  // 1 m over dry ground everywhere: no piers
};
const out = {};
for (const [name, list] of Object.entries(cases)) {
  const scene = new THREE.Scene(), br = new Bridges(scene);
  br.build(list);
  let nan = false, tris = 0, meshes = 0;
  const box = new THREE.Box3();
  br.group.traverse((o) => {
    if (!o.isMesh) return;
    meshes++;
    const a = o.geometry.attributes.position.array;
    if (a.some((v) => !Number.isFinite(v))) nan = true;
    tris += (o.geometry.index ? o.geometry.index.count : a.length / 3) / 3;
    box.expandByObject(o);
  });
  // Pier bottoms: lowest geometry (piers in water reach the sea floor at -4 m).
  out[name] = { nan, tris, meshes, top: box.max.y, bottom: box.min.y };
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("bridges")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js","./addons/*":"./addons/*"}}')
    for f in ("bridges.js", "landmarkKit.js"):
        shutil.copy(VIEWER / f, d / f)
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_all_kinds_build(result):
    for k, r in result.items():
        assert not r["nan"] and r["tris"] > 0 and r["meshes"] >= 2, k


def test_heights(result):
    assert result["zayed"]["top"] == pytest.approx(60, abs=2)  # the principal arch above the water
    assert result["maqta"]["top"] > 7.5 + 0.18 * 50  # the tied arch rises over the deck
    assert result["khalifa"]["top"] == pytest.approx(35 + 1.0, abs=0.2)  # deck 35 m + parapet
    assert result["plain"]["bottom"] == pytest.approx(-4)  # piers down into the water
    assert result["low"]["bottom"] > 0  # low over land: no piers, only the deck
