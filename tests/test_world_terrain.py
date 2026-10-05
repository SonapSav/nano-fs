"""Terrain shared by the viewer and the physics: flightsim/world/terrain.py must give
bit-identical heights to flightsim/viewer/terrain.js (compared with Node when available)."""

import json
import math
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

from flightsim.world import terrain

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")


def _points():
    pts = [(0.0, 0.0), (1399.9, 0.0), (1400.0, 0.0), (2000.0, -350.5), (-2600.0, 1.0), (100000.0, -100000.0), (-73421.25, 55120.5)]
    s = 12345
    for _ in range(1993):  # deterministic pseudo-random points over +/-60 km
        s = (s * 1103515245 + 12345) % 2**31
        x = (s / 2**31 - 0.5) * 120000
        s = (s * 1103515245 + 12345) % 2**31
        pts.append((x, (s / 2**31 - 0.5) * 120000))
    return pts


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_python_port_matches_the_viewer_bit_for_bit(tmp_path):
    three = tmp_path / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    shutil.copy(VIEWER / "terrain.js", tmp_path / "terrain.js")
    script = f"""
    const t = await import("./terrain.js");
    const pts = {json.dumps(_points())};
    const buf = new DataView(new ArrayBuffer(8));
    console.log(JSON.stringify({{ seed: t.WORLD_SEED, water: t.WATER_LEVEL_M, flat: t.AIRFIELD.flatRadiusM,
      bits: pts.map(([x, z]) => {{ buf.setFloat64(0, t.height(x, z)); return buf.getBigUint64(0).toString(16); }}) }}));
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout)
    assert (js["seed"], js["water"], js["flat"]) == (terrain.WORLD_SEED, terrain.WATER_LEVEL_M, terrain.AIRFIELD_FLAT_RADIUS_M)
    py = [format(struct.unpack(">Q", struct.pack(">d", terrain.height_m(x, z)))[0], "x") for x, z in _points()]
    mismatches = [(p, a, b) for p, a, b in zip(_points(), js["bits"], py) if a != b]
    assert not mismatches, mismatches[:5]


def test_terrain_shape():
    heights = [terrain.height_m(x, z) for x, z in _points()]
    assert terrain.height_m(0.0, 0.0) == 0.0  # the airfield is flat at 0 m (the runway)
    assert 150 < max(heights) < 400 and min(heights) < terrain.WATER_LEVEL_M  # hills and lakes
    ground = [max(h, terrain.WATER_LEVEL_M) for h in heights]
    assert min(ground) == terrain.WATER_LEVEL_M  # lakes are flat water for the physics


def test_ground_elevation_uses_the_viewers_lat_lon_mapping():
    north, east = 5000.0, -12000.0
    lat, lon = north / terrain.R_EARTH_M, east / terrain.R_EARTH_M
    assert terrain.ground_elevation_m(lat, lon) == max(terrain.height_m(east, -north), terrain.WATER_LEVEL_M)
    assert math.isfinite(terrain.ground_elevation_m(0.0, 0.0))


# --- The physics on the terrain ---------------------------------------------------------

from flightsim.core import Controls, InitialConditions, JSBSimCore  # noqa: E402
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config  # noqa: E402

ROOT = Path(__file__).parent.parent
HILL_EAST_M, HILL_NORTH_M = 13500.0, -500.0  # a 248.5 m hilltop (viewer x 13500, z 500)


def _hill_env(terrain_model, alt_m, **extra):
    over = {
        "terrain": terrain_model, "episode_s": 1.0,
        "initial_conditions.north_m": HILL_NORTH_M, "initial_conditions.east_m": HILL_EAST_M,
        "initial_conditions.alt_msl_m": alt_m, "randomize.alt_msl_m": 0.0, "randomize.tas_mps": 0.0,
        "randomize.heading_deg": 0.0, **extra,
    }  # fmt: skip
    return AltitudeHeadingHoldEnv(load_env_config(ROOT / "configs" / "envs" / "altitude_heading_hold.yaml", over))


def test_height_above_ground_follows_the_terrain():
    ground = terrain.ground_elevation_m(HILL_NORTH_M / terrain.R_EARTH_M, HILL_EAST_M / terrain.R_EARTH_M)
    assert ground == pytest.approx(248.5, abs=0.5)
    _, info = _hill_env("procedural", 400.0).reset(seed=0)
    assert info["state"].alt_agl_m == pytest.approx(400.0 - ground, abs=0.5)
    _, flat = _hill_env("flat", 400.0).reset(seed=0)
    assert flat["state"].alt_agl_m == pytest.approx(400.0, abs=0.5)  # the default: flat ground at 0 m


def test_flying_into_the_hill_ends_the_flight():
    env = _hill_env("procedural", 290.0, **{"termination.min_alt_agl_m": 50.0})  # 41 m above the hilltop
    env.reset(seed=0)
    _, _, terminated, _, info = env.step(np.zeros(4, dtype=np.float32))
    assert terminated and info["termination_reason"] == "ground"
    flat = _hill_env("flat", 290.0, **{"termination.min_alt_agl_m": 50.0})
    flat.reset(seed=0)
    assert not flat.step(np.zeros(4, dtype=np.float32))[2]  # 290 m above flat ground is fine


def test_the_gear_rests_on_the_hillside():
    lat, lon = HILL_NORTH_M / terrain.R_EARTH_M, HILL_EAST_M / terrain.R_EARTH_M
    ground = terrain.ground_elevation_m(lat, lon)
    core = JSBSimCore("c172p", 1 / 120)
    core.reset(InitialConditions(ground + 2.0, 0.0, 0.0, lat_rad=lat, lon_rad=lon), ground_elevation_m=ground)
    for _ in range(120 * 5):
        core.set_ground_elevation_m(terrain.ground_elevation_m(core.state().lat_rad, core.state().lon_rad))
        s = core.step(Controls(throttle=0.0))
    assert s.alt_msl_m - ground == pytest.approx(4.36 * 0.3048, abs=0.05)  # CG height on the gear, as on flat ground
