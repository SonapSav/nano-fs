"""The windsock (flightsim/viewer/scenery.js) points downwind and rises with the wind;
run with Node when available."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
const THREE = await import("three");
const { Windsock } = await import("./scenery.js");
const scene = new THREE.Scene();
const sock = new Windsock(scene);
const tip = () => {
  sock.group.updateMatrixWorld(true);
  const base = new THREE.Vector3().setFromMatrixPosition(sock.pivot.matrixWorld);
  const end = new THREE.Vector3(3.6, 0, 0).applyMatrix4(sock.pivot.matrixWorld);
  return end.sub(base).toArray();
};
const out = {};
sock.setWind(129, 17); out.strong = tip();
sock.setWind(270, 7.5); out.half = tip();
sock.setWind(0, 0); out.calm = tip();
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def tips(tmp_path_factory):
    d = tmp_path_factory.mktemp("windsock")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js","./addons/*":"./addons/*"}}')
    shutil.copytree(VIEWER / "vendor" / "addons", three / "addons")
    for f in ("scenery.js", "terrain.js", "terrainCore.js", "demTiles.js", "demCore.js", "world.js", "groundDetail.js", "imageryClip.js", "groundTextures.js", "nightLights.js", "trees.js", "aircraft.js", "camera.js"):
        shutil.copy(VIEWER / f, d / f)
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _bearing_deg(v):
    # World: x east, z south; bearing clockwise from north.
    return math.degrees(math.atan2(v[0], -v[2])) % 360


def test_points_downwind_and_stands_out_in_a_strong_wind(tips):
    x, y, z = tips["strong"]
    assert _bearing_deg(tips["strong"]) == pytest.approx((129 + 180) % 360, abs=0.5)  # downwind
    assert abs(y) < 0.05 * 3.6  # straight out at 15 kt and above


def test_droops_in_lighter_winds_and_hangs_when_calm(tips):
    assert _bearing_deg(tips["half"]) == pytest.approx(90, abs=0.5)  # wind from 270 blows toward 090
    assert -3.6 * 0.9 < tips["half"][1] < -3.6 * 0.4
    assert tips["calm"][1] < -3.6 * 0.95
