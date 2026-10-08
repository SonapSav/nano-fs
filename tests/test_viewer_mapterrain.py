"""Moving-map terrain tiles (flightsim/viewer/mapTerrain.js), run with Node when available:
pixels follow the shared terrain (water where the ground is below the lake level, forest
colour in forests), the river port draws on valley floors only, levels by scale."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
const t = await import(%s);
const core = await import(%s);
const out = {};
const t0 = performance.now();
const px = t.mapTilePixels(16, 1, -2); // x 4096..8192, z -8192..-4096
out.ms = performance.now() - t0;
const n = t.TILE_PX, mpp = 16, x0 = 1 * n * mpp, z0 = -2 * n * mpp;
let checked = { water: 0, waterOk: 0, forest: 0, forestOk: 0 };
for (let j = 0; j < n; j += 7) for (let i = 0; i < n; i += 7) {
  const x = x0 + (i + 0.5) * mpp, z = z0 + (j + 0.5) * mpp, h = core.height(x, z), o = (j * n + i) * 4;
  const rgb = [px[o], px[o + 1], px[o + 2]];
  if (h < core.WATER_LEVEL_M) { checked.water++; if (rgb.join() === "63,107,140") checked.waterOk++; }
  else if (core.isForest(x, z, h) && t.riverDistanceM(x, z, h) > 30) {
    checked.forest++; if (rgb[1] > rgb[0] && rgb[1] < 90) checked.forestOk++;  // dark green, shaded
  }
}
out.checked = checked;
// Rivers: only on valley floors away from the airfield.
let river = 0, riverHigh = 0;
for (let x = -20000; x < 20000; x += 37) for (let z = -2000; z < 2000; z += 211) {
  const h = core.height(x, z), d = t.riverDistanceM(x, z, h);
  if (d < 10.5) { river++; if (h > 2) riverHigh++; }
}
out.river = [river, riverHigh, t.riverDistanceM(0, 0, 0)];
out.levels = [1, 3, 6, 12, 20, 40, 100].map(t.levelFor);
console.log(JSON.stringify(out));
""" % (json.dumps((VIEWER / "mapTerrain.js").as_uri()), json.dumps((VIEWER / "terrainCore.js").as_uri()))


@pytest.fixture(scope="module")
def out():
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_tiles_follow_the_terrain(out):
    c = out["checked"]
    assert c["water"] == c["waterOk"] and c["forest"] == c["forestOk"]
    assert c["forest"] > 0 or c["water"] > 0  # the sampled tile has some of either


def test_river_on_valley_floors_only(out):
    river, high, at_airfield = out["river"]
    assert river > 0 and high == 0
    assert at_airfield is None or at_airfield > 1e9  # Infinity (JSON null): never on the airfield


def test_levels(out):
    assert out["levels"] == [4, 4, 8, 16, 16, 32, 64]  # tile pixels at least 3/4 of a screen pixel
    print("tile ms", out["ms"])
