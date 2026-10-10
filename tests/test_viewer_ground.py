"""Ground features (flightsim/viewer/terrain.js villages and landmarks, roads.js), run with
Node: deterministic, on dry land, connected, and the road network reaches the airfield."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
const t = await import("./terrain.js");
const r = await import("./roads.js");
const villages = [];
for (let i = -6; i <= 6; i++) for (let j = -6; j <= 6; j++) { const v = t.villageCentre(i, j); if (v) villages.push(v); }
const segs = r.roadSegments(0, 0), again = r.roadSegments(0, 0);
const len = (s) => Math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]);
console.log(JSON.stringify({
  villages: villages.length,
  heights: villages.map(([x, z]) => t.height(x, z)),
  segs: segs.length, same: JSON.stringify(segs) === JSON.stringify(again), maxLen: Math.max(...segs.map(len)),
  airfield: segs.some((s) => s[0][0] === r.AIRFIELD_GATE[0] && s[0][1] === r.AIRFIELD_GATE[1]),
}));
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("ground")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    for f in ("terrain.js", "terrainCore.js", "demTiles.js", "demCore.js", "world.js", "roads.js", "roadNet.js", "groundDetail.js", "imageryClip.js", "groundTextures.js", "nightLights.js", "trees.js"):
        shutil.copy(VIEWER / f, d / f)
    res = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_villages_sit_on_dry_low_land(out):
    assert 20 < out["villages"] < 80  # of 169 cells around the airfield
    assert all(-0.6 < h <= 120 for h in out["heights"])


def test_roads_link_villages_and_reach_the_airfield(out):
    assert out["same"] and out["segs"] > 15
    assert out["maxLen"] <= 5000 * 1.6 + 1  # neighbours within 5 km (the airfield road up to 8 km)
    assert out["airfield"]
