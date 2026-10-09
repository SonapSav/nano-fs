"""Real-world terrain (flightsim/world/dem.py, viewer/demCore.js): bilinear heights between
the posts, continuous across tiles, never below sea level, sea level outside the region;
water from the land cover; the files checked against the manifest; Python and JavaScript
bit-identical. With the Abu Dhabi region built (scripts/build_scenery.py), an episode flies
over it, records the scenery, and ends on touching the sea."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from flightsim.world.dem import DemTerrain
from flightsim.world.scenery import (
    FORMAT, HEIGHT_CELLS, LANDCOVER_CELLS, POST_M, SCENERY_DIR, TILE_SIZE_M, heights_name, landcover_name, load_region,
)  # fmt: skip
from flightsim.world.scenery_build import sha256_file

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
TILES = [(ix, iz) for iz in (-1, 0) for ix in (-1, 0)]


@pytest.fixture(scope="module")
def region(tmp_path_factory):
    """A 2 x 2 tile region of random heights (some below sea level) and land cover."""
    root = tmp_path_factory.mktemp("scenery")
    path = root / "synthetic"
    (path / "tiles").mkdir(parents=True)
    rng = np.random.default_rng(5)
    n = HEIGHT_CELLS + 1
    full = rng.uniform(-8, 120, (2 * HEIGHT_CELLS + 1, 2 * HEIGHT_CELLS + 1)).astype("<f4")  # shared edges
    for ix, iz in TILES:
        r0, c0 = (iz + 1) * HEIGHT_CELLS, (ix + 1) * HEIGHT_CELLS
        (path / heights_name(ix, iz)).write_bytes(full[r0 : r0 + n, c0 : c0 + n].tobytes())
        lc = rng.choice(np.array([50, 60, 80], np.uint8), (LANDCOVER_CELLS, LANDCOVER_CELLS))
        (path / landcover_name(ix, iz)).write_bytes(lc.tobytes())
    files = {f"tiles/{p.name}": sha256_file(p) for p in sorted((path / "tiles").iterdir())}
    manifest = {"format": FORMAT, "name": "synthetic", "origin_lat_deg": 24.0, "origin_lon_deg": 54.0,
                "tiles": {"ix_min": -1, "ix_max": 0, "iz_min": -1, "iz_max": 0}, "files": files}  # fmt: skip
    (path / "manifest.json").write_text(json.dumps(manifest))
    return load_region("synthetic", root), full


def test_heights_between_and_at_the_posts(region):
    reg, full = region
    t = DemTerrain(reg)
    # At a post: its value (or sea level); tile (-1, -1) starts at x = z = -4000.
    for r, c in [(0, 0), (10, 200), (128, 128), (255, 3), (77, 129)]:  # (256 is the outer edge: outside)
        x, z = -TILE_SIZE_M + c * POST_M, -TILE_SIZE_M + r * POST_M
        assert t.height_at(x, z) == max(0.0, float(full[r, c]))
    # Halfway between four posts (where none is below sea level): their mean.
    for r, c in [(5, 5), (130, 60), (200, 250)]:
        x, z = -TILE_SIZE_M + (c + 0.5) * POST_M, -TILE_SIZE_M + (r + 0.5) * POST_M
        quad = full[r : r + 2, c : c + 2].astype(np.float64)
        if quad.min() > 0:
            assert t.height_at(x, z) == pytest.approx(quad.mean(), abs=1e-9)
    # Continuous across a tile edge, never below sea level, sea level outside.
    assert t.height_at(-1e-9, 1000.0) == pytest.approx(t.height_at(0.0, 1000.0), abs=1e-6)
    rng = np.random.default_rng(2)
    assert min(t.height_at(x, z) for x, z in rng.uniform(-4000, 4000, (2000, 2))) >= 0.0
    assert t.height_at(5000.0, 0.0) == 0.0 and not t.water_at(5000.0, 0.0)


def test_files_must_match_the_manifest(region, tmp_path):
    reg, _ = region
    copy = tmp_path / "synthetic"
    shutil.copytree(reg.path, copy)
    p = copy / heights_name(0, 0)
    raw = bytearray(p.read_bytes())
    raw[0] ^= 1
    p.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="differs from its manifest"):
        DemTerrain(load_region("synthetic", tmp_path))


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_python_and_javascript_identical(region):
    reg, _ = region
    t = DemTerrain(reg)
    rng = np.random.default_rng(9)
    pts = [*rng.uniform(-4500, 4500, (3000, 2)).tolist(), [0.0, 0.0], [-4000.0, -4000.0], [3999.999999, 3999.999999],
           [-0.0, 2000.0], [4000.0, 0.0], [-2000.0, -1e-12], [31.25 * 7, -31.25 * 100]]  # fmt: skip
    script = f"""
    import {{ readFileSync, existsSync }} from "node:fs";
    const d = await import({json.dumps((VIEWER / "demCore.js").as_uri())});
    const dir = {json.dumps(str(reg.path))};
    const load = (name, T) => {{ const p = dir + "/" + name; if (!existsSync(p)) return null; const b = readFileSync(p); return new T(b.buffer, b.byteOffset, b.byteLength / T.BYTES_PER_ELEMENT); }};
    const cache = new Map();
    const get = (key, f) => {{ if (!cache.has(key)) cache.set(key, f()); return cache.get(key); }};
    const tiles = {{
      heights: (ix, iz) => get("h" + ix + "," + iz, () => load(d.heightsName(ix, iz), Float32Array)),
      landcover: (ix, iz) => get("l" + ix + "," + iz, () => load(d.landcoverName(ix, iz), Uint8Array)),
    }};
    const pts = {json.dumps(pts)};
    console.log(JSON.stringify(pts.map(([x, z]) => [d.heightAt(tiles, x, z), d.waterAt(tiles, x, z)])));
    """
    out = json.loads(subprocess.run([NODE, "--input-type=module"], input=script, capture_output=True, text=True, check=True).stdout)
    for (x, z), (h_js, w_js) in zip(pts, out):
        assert t.height_at(x, z) == h_js, (x, z)  # exactly, not approximately
        assert t.water_at(x, z) == w_js, (x, z)


# --- The built Abu Dhabi region (skipped when it is not built) -------------------------------

ABU_DHABI = SCENERY_DIR / "abu_dhabi" / "manifest.json"
needs_abu_dhabi = pytest.mark.skipif(not ABU_DHABI.exists(), reason="Abu Dhabi scenery not built (scripts/build_scenery.py)")


@needs_abu_dhabi
def test_episode_over_abu_dhabi_records_its_scenery():
    from flightsim.envs import make_env
    from flightsim.envs.config import load_env_config

    cfg = load_env_config("configs/envs/abu_dhabi.yaml")
    env = make_env(cfg)
    env.reset(seed=3)
    assert env.ground.scenery["name"] == "abu_dhabi"
    assert env.ground.scenery["hash"] == load_region("abu_dhabi").scenery_hash
    # Al Bateen sits a few metres above the sea; the core flies with that ground under it.
    assert 0.0 < env.ground.elevation_at_m(0.0, 0.0) < 15.0
    s = env._state
    assert s.alt_msl_m - s.alt_agl_m == pytest.approx(env.ground.elevation_m(s.lat_rad, s.lon_rad), abs=0.01)
    for _ in range(20):
        env.step(env.action_space.sample() * 0)
    assert env.provenance().scenery == env.ground.scenery


@needs_abu_dhabi
def test_touching_the_sea_ends_the_flight():
    from flightsim.envs import make_env
    from flightsim.envs.config import load_env_config

    # Low over the open sea 20 km north-west of the airfield, descending.
    cfg = load_env_config("configs/envs/abu_dhabi.yaml", {
        "initial_conditions.alt_msl_m": 20, "initial_conditions.north_m": 20000, "initial_conditions.east_m": -20000,
        "randomize.alt_msl_m": 0, "termination.min_alt_agl_m": -10,
    })  # fmt: skip
    env = make_env(cfg)
    env.reset(seed=1)
    assert env.ground.water(env._state.lat_rad, env._state.lon_rad)
    reason = None
    for _ in range(400):
        action = np.zeros(env.action_space.shape, np.float32)
        action[0] = 0.3  # elevator: nose down
        _, _, terminated, truncated, info = env.step(action)
        if terminated or truncated:
            reason = info["termination_reason"]
            break
    assert reason == "water"
    assert math.isfinite(env._state.alt_msl_m)
