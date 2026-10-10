"""Trees from the canopy height map (flightsim/viewer/trees.js), run with Node when
available: one batched mesh per tile, palms (5 m and up) or round trees scaled to their
heights with their bases on the ground; near trees take the detailed shape, farther ones
the simple one, the farthest and those behind the view are hidden."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = r"""
globalThis.document = { createElement: () => ({ getContext: () => new Proxy({}, { get: () => () => {}, set: () => true }), width: 0, height: 0 }) };
const THREE = await import("three");
const T = await import("./trees.js");
// Three trees: a 12 m palm and a 4 m tree, and a 9 m palm 2 km away.
const data = new Float32Array([100, 5, 100, 12, 120, 5, 110, 4, 2100, 2, 100, 9]);
const b = T.treeBatch(data);
const ids = b.userData.trees.ids, m = new THREE.Matrix4(), p = new THREE.Vector3(), q = new THREE.Quaternion(), sc = new THREE.Vector3();
const state = () => [0, 1, 2].map((i) => ({ visible: b.getVisibleAt(i), geometry: Object.keys(ids).find((k) => ids[k] === b.getGeometryIdAt(i)) }));
const out = { count: b.instanceCount ?? null };
out.scale = [0, 1].map((i) => { b.getMatrixAt(i, m); m.decompose(p, q, sc); return [p.y, sc.y]; });
T.updateTreeBatch(b, 0, 0, 1, 0); // looking east, toward all three
out.behind = (T.updateTreeBatch(b, 0, 0, -1, 0), [0, 1, 2].map((i) => b.getVisibleAt(i))); // looking west: only those within 300 m
b.userData.trees.key = null; T.updateTreeBatch(b, 0, 0, 1, 0);
out.near = state();
T.updateTreeBatch(b, 20000, 0);
out.gone = state();
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("trees")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    shutil.copy(VIEWER / "trees.js", d / "trees.js")
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_sizes(result):
    # The palm and the round tree scaled to their heights (geometry 10 m tall), bases 0.2 m into the ground.
    assert result["scale"] == [[pytest.approx(4.8), pytest.approx(1.2)], [pytest.approx(4.8), pytest.approx(0.4)]]


def test_detail_by_distance(result):
    assert result["near"] == [{"visible": True, "geometry": "nearPalm"}, {"visible": True, "geometry": "nearRound"}, {"visible": True, "geometry": "farPalm"}]
    assert all(not s["visible"] for s in result["gone"])
    assert result["behind"] == [True, True, False]  # behind the view: only the nearby ones
