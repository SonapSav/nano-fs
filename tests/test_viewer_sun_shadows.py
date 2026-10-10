"""The sun's shadows over a region (flightsim/viewer/sunShadows.js), run with Node when
available: the shadowed square sits ahead of the camera and grows with height, the light
keeps the sun's direction, and the map is drawn again only when the square moves, the
scene changes or the shadows are switched on."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = r"""
const THREE = await import("three");
const { SunShadows } = await import("./sunShadows.js");
const renderer = { shadowMap: { autoUpdate: true, needsUpdate: false } };
const sun = new THREE.DirectionalLight();
const sh = new SunShadows(renderer, sun);
sh.set(true, 2048);
const dir = new THREE.Vector3(-0.45, 0.62, 0.65).normalize();
const cam = new THREE.PerspectiveCamera();
const frame = (x, y, z, lookX, lookZ) => {
  cam.position.set(x, y, z);
  cam.lookAt(x + lookX, y - 10, z + lookZ);
  cam.updateMatrixWorld();
  renderer.shadowMap.needsUpdate = false;
  sh.update(cam, 0, dir);
  const t = sun.target.position, d = sun.position.clone().sub(t).normalize(), c = sun.shadow.camera;
  return { drawn: renderer.shadowMap.needsUpdate, target: [t.x, t.z], dir: d.toArray(), half: c.right / 1.15, auto: renderer.shadowMap.autoUpdate };
};
const out = {};
out.low = frame(1000, 30, 2000, 1, 0);      // looking east, low
out.same = frame(1001, 30, 2000, 1, 0);     // a metre on: same square, not drawn again
sh.invalidate();
out.invalidated = frame(1001, 30, 2000, 1, 0);
out.moved = frame(1300, 30, 2000, 1, 0);
out.high = frame(1000, 800, 2000, 1, 0);
sh.set(false);
out.off = { cast: sun.castShadow };
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("sun")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    shutil.copy(VIEWER / "sunShadows.js", d / "sunShadows.js")
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_square_ahead_of_the_camera_and_light_along_the_sun(result):
    low = result["low"]
    assert low["drawn"] and not low["auto"]
    assert low["target"][0] > 1000 and low["target"][1] == pytest.approx(2000, abs=low["half"] / 4)  # ahead (east)
    n = math.sqrt(0.45**2 + 0.62**2 + 0.65**2)
    assert low["dir"] == pytest.approx([-0.45 / n, 0.62 / n, 0.65 / n], abs=1e-6)


def test_drawn_again_only_when_needed(result):
    assert not result["same"]["drawn"]
    assert result["invalidated"]["drawn"] and result["moved"]["drawn"]


def test_wider_from_altitude(result):
    assert result["high"]["half"] > 2 * result["low"]["half"]
    assert result["off"]["cast"] is False
