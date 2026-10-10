"""The Grand Mosque from its measured layout (configs/scenery/abu_dhabi.yaml, the landmark's
`layout`; flightsim/viewer/landmarks.js), run with Node when available: on a courtyard
like OSM's, its minarets reach the published 107 m on the courtyard's corners, the main
dome its 85 m, the prayer hall lies on the footprint's side of the courtyard (either way
round), and nothing is NaN."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
VIEWER = ROOT / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = r"""
const THREE = await import("three");
const { Landmarks } = await import("./landmarks.js");
const spec = JSON.parse(process.argv[1]);
const rect = (u0, u1, v0, v1) => [u0, v0, u1, v0, u1, v1, u0, v1];
const out = {};
for (const side of [1, -1]) {
  // Courtyard 146 x 120 m centred at the origin (u = x, v = z); footprint reaching to the hall side.
  const court = rect(-73, 73, -60, 60), foot = side > 0 ? rect(-91, 91, -79, 164) : rect(-91, 91, -164, 79);
  const m = { ...spec, ring: foot, inner: [court], centre: [0, 0], axis_deg: 90, ground_m: 10, pools: [] };
  const lm = new Landmarks(new THREE.Scene());
  lm.build([m]);
  let nan = false, top = -Infinity, dome = -Infinity, domeZ = 0;
  const minarets = new Set();
  lm.group.traverse((o) => {
    if (!o.isMesh) return;
    const p = o.geometry.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
      if (![x, y, z].every(Number.isFinite)) nan = true;
      top = Math.max(top, y);
      if (y > 10 + 100) minarets.add(`${Math.round(x / 10)},${Math.round(z / 10)}`);
      if (Math.abs(x) < 30 && Math.abs(z) > 80 && y > dome) [dome, domeZ] = [y, z];
    }
  });
  out[side] = { nan, top, dome, domeZ, minarets: [...minarets].sort() };
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    region = yaml.safe_load((ROOT / "configs" / "scenery" / "abu_dhabi.yaml").read_text())
    spec = next(m for m in region["landmarks"] if m["kind"] == "grand_mosque")
    d = tmp_path_factory.mktemp("mosque")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    for f in ("landmarks.js", "landmarkKit.js"):
        shutil.copy(VIEWER / f, d / f)
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, json.dumps(spec)], cwd=d, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_published_heights_and_corners(result):
    for side, r in result.items():
        assert not r["nan"]
        assert r["top"] == pytest.approx(10 + 107, abs=0.5)  # the minarets (a crescent finial's tip lies a little under its nominal height)
        assert r["dome"] == pytest.approx(10 + 85, abs=0.5)  # the main dome's finial
        assert r["minarets"] == sorted({f"{round(x / 10)},{round(z / 10)}" for x in (-73, 73) for z in (-60, 60)}), side


def test_prayer_hall_on_the_footprints_side(result):
    assert result["1"]["domeZ"] > 100 and result["-1"]["domeZ"] < -100
