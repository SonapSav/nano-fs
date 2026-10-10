"""High-resolution imagery around the camera (flightsim/viewer/imageryClip.js), run with
Node when available: the window of chunks fills every slot once, keeps the camera at
least 3.5 chunks from its edges, each level fades out inside that margin, and a road
material drops its fragments where the imagery shows."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = r"""
const THREE = await import("three");
const { clipWindow, SLOTS, FADE_M, hideUnderImagery, clipUniforms } = await import("./imageryClip.js");
const out = { SLOTS, FADE_M, cases: [] };
// A road material: its shader drops fragments where the imagery shows.
const m = hideUnderImagery(new THREE.MeshLambertMaterial());
const shader = { uniforms: {}, vertexShader: "#include <common>\n#include <begin_vertex>", fragmentShader: "#include <common>\nvoid main() {\n#include <clipping_planes_fragment>\n}" };
m.onBeforeCompile(shader);
out.road = { discard: shader.fragmentShader.includes("if (clipShown(vClipXZ)"), varying: shader.vertexShader.includes("vClipXZ ="),
  uniforms: Object.keys(clipUniforms).every((k) => shader.uniforms[k] === clipUniforms[k]), key: m.customProgramCacheKey() };
for (const [x, z, m] of [[0, 0, 512], [805.5, -1300.2, 512], [-25000, 24999, 512], [255.9, 256.1, 512], [7000, -3, 2048]]) {
  const w = clipWindow(x, z, m);
  const xs = w.map((c) => c.cx), zs = w.map((c) => c.cz);
  out.cases.push({ x, z, m, n: w.length, slots: [...new Set(w.map((c) => c.slot))].length,
    slotOk: w.every((c) => c.slot === (((c.cz % SLOTS) + SLOTS) % SLOTS) * SLOTS + (((c.cx % SLOTS) + SLOTS) % SLOTS)),
    margin: Math.min(x / m - Math.min(...xs), Math.max(...xs) + 1 - x / m, z / m - Math.min(...zs), Math.max(...zs) + 1 - z / m) });
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("clip")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    shutil.copy(VIEWER / "imageryClip.js", d / "imageryClip.js")
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_window_fills_every_slot_once(result):
    for c in result["cases"]:
        assert c["n"] == result["SLOTS"] ** 2 and c["slots"] == c["n"] and c["slotOk"], c


def test_camera_well_inside_window(result):
    for c in result["cases"]:
        assert c["margin"] >= result["SLOTS"] / 2 - 0.5 - 1e-9, c


def test_roads_give_way_to_the_imagery(result):
    r = result["road"]
    assert r["discard"] and r["varying"] and r["uniforms"] and "underImagery" in r["key"]


def test_levels_fade_before_window_edge(result):
    # Level 0: 512 m chunks, level 1: 2048 m (scenery_hires.py); the fade ends inside the margin.
    for (start, end), chunk in zip(result["FADE_M"], (512, 2048)):
        assert start < end <= (result["SLOTS"] / 2 - 0.5) * chunk
