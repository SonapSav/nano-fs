"""Procedural terrain checks (flightsim/viewer/terrain.js), run with Node when available.
Node is not in the Docker image; there these tests are skipped."""

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
const t = await import("./terrain.js");
const out = {};
// Determinism: same height twice, and a fixed reference value for this seed.
out.h1 = t.height(1234.5, -6789.0); out.h2 = t.height(1234.5, -6789.0);
// Airfield region is flat at 0 m (the physics' ground level).
let maxAirfield = 0;
for (let x = -600; x <= 600; x += 50) for (let z = -300; z <= 300; z += 50) maxAirfield = Math.max(maxAirfield, Math.abs(t.height(x, z)));
out.maxAirfield = maxAirfield;
// Water fraction and highest point over a 40 x 40 km area.
let water = 0, n = 0, top = 0;
for (let x = -20000; x <= 20000; x += 200) for (let z = -20000; z <= 20000; z += 200) { const h = t.height(x, z); n++; if (h < t.WATER_LEVEL_M) water++; top = Math.max(top, h); }
out.waterFraction = water / n; out.top = top;
// Seams: a shared edge between neighbouring tiles of different detail matches exactly.
const terr = new t.Terrain(new THREE.Scene());
do { terr.update(0, 0, 50); } while (terr.pending);
const a = terr.tiles.get("1,0").mesh.geometry, b = terr.tiles.get("2,0").mesh.geometry;  // 96 vs 48 segments
const find = (g, x, z) => { const p = g.attributes.position; for (let i = 0; i < p.count; i++) if (Math.abs(p.getX(i) - x) < 1e-6 && Math.abs(p.getZ(i) - z) < 1e-6 && p.getY(i) > -30) return i; return -1; };
let mismatch = 0;
for (let z = 0; z <= 4000; z += 500) {
  const i = find(a, 8000, z), j = find(b, 8000, z);
  for (const attr of ["position", "normal"]) for (const c of ["getX", "getY", "getZ"]) mismatch = Math.max(mismatch, Math.abs(a.attributes[attr][c](i) - b.attributes[attr][c](j)));
}
out.seamMismatch = mismatch; out.tiles = terr.tiles.size;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def terrain(tmp_path_factory):
    d = tmp_path_factory.mktemp("terrain")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    for f in ("terrain.js", "terrainCore.js", "demTiles.js", "demCore.js", "world.js", "groundDetail.js", "imageryClip.js", "groundTextures.js", "nightLights.js", "trees.js", "treeInstances.js"):
        shutil.copy(VIEWER / f, d / f)
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_terrain_is_deterministic(terrain):
    assert terrain["h1"] == terrain["h2"]


def test_airfield_is_flat_at_sea_level_like_the_physics(terrain):
    assert terrain["maxAirfield"] < 1e-9


def test_terrain_stays_far_below_cruise_altitudes(terrain):
    assert 100 < terrain["top"] < 400  # tasks fly at 1000-2100 m


def test_water_is_a_small_fraction(terrain):
    assert 0.0 < terrain["waterFraction"] < 0.1


def test_tiles_of_different_detail_join_without_seams(terrain):
    assert terrain["tiles"] == 121
    assert terrain["seamMismatch"] < 1e-6
