"""The circuit's traffic pattern drawing (flightsim/viewer/pattern.js) matches the circuit
autopilot's geometry; run with Node when available."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from flightsim.control.circuit import circuit_gains_from_raw, load_circuit_raw
from flightsim.envs import load_env_config, make_env

ROOT = Path(__file__).parent.parent
VIEWER = ROOT / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    env = make_env(load_env_config(ROOT / "configs" / "envs" / "circuit.yaml"))
    env.reset(seed=0)
    a = env.approach_info()
    p = circuit_gains_from_raw(load_circuit_raw(ROOT / "configs" / "circuit_autopilot.yaml")).pattern
    pattern = {"height_m": p.pattern_height_m, "crosswind_below_m": p.crosswind_below_m, "downwind_offset_m": p.downwind_offset_m}
    d = tmp_path_factory.mktemp("pattern")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    for f in ("pattern.js", "patternPath.js"):
        shutil.copy(VIEWER / f, d / f)
    script = f"""
const {{ patternPath, buildPattern }} = await import("./pattern.js");
const a = {json.dumps(a)}, p = {json.dumps(pattern)};
const out = patternPath(a, p);
out.children = buildPattern(a, p).children.length;
console.log(JSON.stringify(out));
"""
    res = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=d, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return a, pattern, json.loads(res.stdout)


def test_pattern_legs_follow_the_autopilot_geometry(drawn):
    a, p, out = drawn
    pts, d, top = out["points"], p["downwind_offset_m"], p["height_m"]
    tan = math.tan(math.radians(a["glide_path_deg"]))
    assert pts[1][2] == pytest.approx(top - p["crosswind_below_m"])  # crosswind turn 300 ft below
    assert pts[2][1] == pts[3][1] == -d and pts[2][2] == pts[3][2] == top  # downwind, left of the runway
    assert out["markers"]["abeam"] == [0, -d, top]  # abeam the threshold
    assert out["markers"]["base"] == pytest.approx([-d, -d, (2 * d + a["aim_point_m"]) * tan])  # 45 deg
    assert pts[-1] == [a["aim_point_m"], 0, 0]
    assert out["children"] == 3  # ribbon (crosswind, downwind, base) and two markers


def test_server_sends_the_pattern_only_for_circuits():
    from flightsim.control.autopilot import load_autopilot_gains
    from flightsim.stream.server import ServerConfig

    cfg = ServerConfig(ROOT / "data", load_env_config(ROOT / "configs" / "envs" / "altitude_heading_hold.yaml"),
                       load_autopilot_gains(ROOT / "configs" / "autopilot.yaml"))  # fmt: skip
    assert cfg.pattern_info() is None
    cfg.circuit_gains = circuit_gains_from_raw(load_circuit_raw(ROOT / "configs" / "circuit_autopilot.yaml"))
    assert cfg.pattern_info()["height_m"] == pytest.approx(304.8)


def test_radio_mast_marks_the_circuit_base_turn(tmp_path):
    """The airfield's radio mast (scenery.js) stands under the circuit autopilot's base turn:
    45 deg from the 09 threshold, the downwind offset north of it."""
    d = tmp_path
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    shutil.copytree(VIEWER / "vendor" / "addons", three / "addons")
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js","./addons/*":"./addons/*"}}')
    for f in ("scenery.js", "terrain.js", "terrainCore.js", "groundDetail.js", "aircraft.js"):
        shutil.copy(VIEWER / f, d / f)
    script = 'const s = await import("./scenery.js"); console.log(JSON.stringify(s.BASE_TURN_MAST));'
    res = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=d, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    mast = json.loads(res.stdout)
    p = circuit_gains_from_raw(load_circuit_raw(ROOT / "configs" / "circuit_autopilot.yaml")).pattern
    env = make_env(load_env_config(ROOT / "configs" / "envs" / "circuit.yaml"))
    env.reset(seed=0)
    a = env.approach_info()
    # World frame x east, z south; runway 09: along = east, left (pattern side) = north (-z).
    assert mast["x"] == pytest.approx(a["threshold_east_m"] - p.downwind_offset_m)
    assert mast["z"] == pytest.approx(-(a["threshold_north_m"] + p.downwind_offset_m))
