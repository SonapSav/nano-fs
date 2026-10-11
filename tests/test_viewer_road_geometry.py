"""The drawn road network's tile geometry (flightsim/viewer/roadGeometry.js), run with Node
when available: a road draped on the ground as drawn (a point wherever it crosses the
mesh's edges), simplified where straight, its markings' arc length running on, every
triangle facing up; marks only on near tiles."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
const { roadTileArrays } = await import("./roadGeometry.js");
const { HEIGHT_CELLS } = await import("./demCore.js");
// Ground rising 1 m per 100 m east; a straight road east (5 m points), then a bend.
const n = HEIGHT_CELLS + 1, posts = new Float32Array(n * n);
for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) posts[j * n + i] = 5 + (i * 4000 / HEIGHT_CELLS) / 100;
const tiles = { heights: (ix, iz) => (ix === 0 && iz === 0 ? posts : posts), landcover: () => null };
const pts = [];
for (let x = 100; x <= 1100; x += 5) pts.push([x, 500, x > 400 && x < 450 ? 0 : 1]);
const road = { roads: [{ pts, width: 9.3, lanes: 2, oneway: true, s0: 1000 }], fills: [], gores: [],
  marks: [{ kind: "zebra", x: 600, z: 500, ux: 1, uz: 0, width: 9.3 }] };
const out = {};
for (const seg of [96, 16]) {
  const a = roadTileArrays(road, tiles, seg);
  const p = a.roadnet.position, idx = a.roadnet.index, r = a.roadnet.aRoad;
  let up = true, maxErr = 0;
  for (let i = 0; i < idx.length; i += 3) {
    const [A, B, C] = [idx[i], idx[i + 1], idx[i + 2]].map((k) => [p[3 * k], p[3 * k + 1], p[3 * k + 2]]);
    const ny = (B[2] - A[2]) * (C[0] - A[0]) - (B[0] - A[0]) * (C[2] - A[2]);
    if (ny <= 0) up = false;
  }
  for (let k = 0; k < p.length / 3; k++) maxErr = Math.max(maxErr, Math.abs(p[3 * k + 1] - (5 + p[3 * k] / 100) - 0.06));
  const ss = []; for (let k = 0; k < r.length / 4; k += 2) ss.push(r[4 * k]);
  out[seg] = { verts: p.length / 3, up, maxErr, s0: ss[0], s1: ss[ss.length - 1], marks: Boolean(a.roadmarks),
    markZero: [...a.roadnet.aMark].some((m) => m === 0) };
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("roadgeo")
    for f in ("roadGeometry.js", "featureGeometry.js", "demCore.js"):
        shutil.copy(VIEWER / f, d / f)
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_draped_simplified_and_facing_up(result):
    near, far = result["96"], result["16"]
    assert near["up"] and far["up"]
    assert near["maxErr"] < 0.01  # on the drawn ground (a sloping plane here)
    assert near["verts"] < 2 * 201 * 0.6  # fewer than the 201 points of the source line
    assert near["s0"] == pytest.approx(1000) and near["s1"] == pytest.approx(2000, abs=0.5)  # arc length runs on
    assert near["markZero"]  # the junction gap in the markings kept through the simplification


def test_marks_only_on_near_tiles(result):
    assert result["96"]["marks"] and not result["16"]["marks"]
