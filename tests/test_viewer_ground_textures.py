"""Close-up ground detail of real-world regions (flightsim/viewer/groundTextures.js), run
with Node when available: the patterns are seeded (same seed, same pixels), tile without
a seam (the step across the wrap is like any other neighbour step), and sit around 0.5."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = r"""
const { detailPatterns, DETAIL_GLSL } = await import("./groundTextures.js");
const n = 128, a = detailPatterns(n, 1), b = detailPatterns(n, 1), c = detailPatterns(n, 2);
const out = { glsl: DETAIL_GLSL.includes("vec3 groundDetail("), channels: {} };
for (const k of Object.keys(a)) {
  const p = a[k];
  let mean = 0, step = 0, wrap = 0;
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      mean += p[y * n + x];
      if (x < n - 1) step += Math.abs(p[y * n + x + 1] - p[y * n + x]);
    }
    wrap += Math.abs(p[y * n] - p[y * n + n - 1]);
  }
  out.channels[k] = { mean: mean / (n * n), step: step / (n * (n - 1)), wrap: wrap / n,
    min: Math.min(...p), max: Math.max(...p), same: p.every((v, i) => v === b[k][i]), differs: p.some((v, i) => v !== c[k][i]) };
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("detail")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    for f in ("groundTextures.js", "groundDetail.js"):
        shutil.copy(VIEWER / f, d / f)
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_patterns_seeded_and_in_range(result):
    assert result["glsl"]
    for k, c in result["channels"].items():
        assert c["same"] and c["differs"], k
        assert 0 <= c["min"] and c["max"] <= 1 and c["mean"] == pytest.approx(0.5, abs=0.06), k


def test_patterns_tile_without_a_seam(result):
    for k, c in result["channels"].items():
        assert c["wrap"] < 2.5 * c["step"] + 0.01, k
