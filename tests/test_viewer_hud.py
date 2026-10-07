"""The HUD (flightsim/viewer/hud.js), run with Node when available: conformal geometry
(world and body directions project where they should), the flight path marker direction,
and the combiner being fixed to the aircraft (nothing drawn when looking away)."""

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
const hud = await import("./hud.js");
const out = {};
const D = Math.PI / 180;
// Camera at the origin looking north (world -z), 70 deg vertical field of view, square view.
const cam = new THREE.PerspectiveCamera(70, 1, 0.05, 120000);
cam.lookAt(0, 0, -1);
cam.updateMatrixWorld();
const W = 1000, H = 1000;
out.ahead = hud.project(hud.worldDirection(0, 0), cam, W, H);
out.up5 = hud.project(hud.worldDirection(0, 5 * D), cam, W, H);
out.right10 = hud.project(hud.worldDirection(10 * D, 0), cam, W, H);
out.behind = hud.project(hud.worldDirection(Math.PI, 0), cam, W, H);
out.body = [hud.bodyVector(0, 0), hud.bodyVector(90, 0), hud.bodyVector(0, 90)].map((v) => v.toArray());
const row = (vn, ve, vd) => ({ v_north_mps: vn, v_east_mps: ve, v_down_mps: vd });
out.fpLevel = hud.flightPathDirection(row(50, 0, 0)).toArray();
out.fpDescent = hud.flightPathDirection(row(50, 0, 5)).toArray();
out.fpParked = hud.flightPathDirection(row(1, 0, 0));
// Drawing: a stub 2D context; an aircraft facing north, level, at speed.
const calls = { stroke: 0, text: [] };
const ctx = new Proxy({}, { get: (t, k) => k === "measureText" ? (s) => ({ width: s.length * 8 })
  : k === "fillText" ? (s) => calls.text.push(String(s)) : k === "stroke" ? () => calls.stroke++ : k in t ? t[k] : () => {}, set: (t, k, v) => ((t[k] = v), true) });
const m = new THREE.Matrix4().makeBasis(new THREE.Vector3(0, 0, -1), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, -1, 0));
const state = { psi_rad: 0, phi_rad: 0, theta_rad: 0, v_north_mps: 50, v_east_mps: 0, v_down_mps: 0, alt_msl_m: 1000, cas_mps: 50, alpha_rad: 0.05, az_mps2: -9.81, ay_mps2: 0, flap_pos_rad: 0 };
out.drawnAhead = hud.drawHud(ctx, W, H, { camera: cam, aircraftMatrix: m, row: state, targets: { alt_msl_m: 1100, heading_rad: 0 } });
out.texts = [...calls.text];
const back = new THREE.PerspectiveCamera(70, 1, 0.05, 120000);
back.lookAt(0, 0, 1);
back.updateMatrixWorld();
out.drawnLookingBack = hud.drawHud(ctx, W, H, { camera: back, aircraftMatrix: m, row: state, targets: null });
// Runway: threshold 500 m north of the origin, heading north, 1000 x 30 m, at 0 m.
const rw = { threshold_north_m: 500, threshold_east_m: 0, heading_deg: 0, length_m: 1000, width_m: 30, elevation_m: 0, aim_point_m: 250, glide_path_deg: 3 };
out.aimWorld = hud.runwayPoint(rw, 250, 0).toArray();
out.rightEdge = hud.runwayPoint(rw, 0, 15).toArray();
// On the glide path: the aim point lies glide_path_deg below the horizon, straight ahead.
const dist = 1000, height = dist * Math.tan(3 * D);
const eye = new THREE.PerspectiveCamera(70, 1, 0.05, 120000);
eye.position.set(0, height, -(500 + 250 - dist));
eye.lookAt(eye.position.x, eye.position.y, eye.position.z - 1);
eye.updateMatrixWorld();
out.aimScreen = hud.projectPoint(hud.runwayPoint(rw, 250, 0), eye, W, H);
out.gpScreen = hud.project(hud.worldDirection(0, -3 * D), eye, W, H);
calls.text.length = 0;
const onApproach = { ...state, alt_msl_m: height, cas_mps: 33.4 };
out.drawnApproach = hud.drawHud(ctx, W, H, { camera: eye, aircraftMatrix: m, row: onApproach, runway: rw, approach: true, speedBugs: [{ kt: 65, label: "A" }, { kt: 140, label: "X" }] });
out.approachTexts = [...calls.text];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("hud")
    three = d / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js"}}')
    for f in ("hud.js", "gauges.js"):
        shutil.copy(VIEWER / f, d / f)
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_conformal_directions_project_through_the_camera(out):
    assert out["ahead"] == pytest.approx({"x": 500, "y": 500}, abs=0.01)
    scale = 500 / math.tan(math.radians(35))  # pixels per unit tangent
    assert out["up5"]["y"] == pytest.approx(500 - scale * math.tan(math.radians(5)), abs=0.05)
    assert out["right10"]["x"] == pytest.approx(500 + scale * math.tan(math.radians(10)), abs=0.05)
    assert out["behind"] is None


def test_body_directions(out):
    fwd, right, up = out["body"]
    assert fwd == pytest.approx([1, 0, 0]) and right == pytest.approx([0, 1, 0], abs=1e-12) and up == pytest.approx([0, 0, -1], abs=1e-12)


def test_flight_path_direction(out):
    assert out["fpLevel"] == pytest.approx([0, 0, -1])  # north, level (world: z south, y up)
    x, y, z = out["fpDescent"]
    assert math.degrees(math.atan2(y, -z)) == pytest.approx(-math.degrees(math.atan(0.1)))  # 5.7 deg down
    assert out["fpParked"] is None


def test_hud_is_fixed_to_the_aircraft(out):
    assert out["drawnAhead"] is True
    assert "3,281" in out["texts"] and "000" in out["texts"]  # altitude box (1000 m) and heading box
    assert out["drawnLookingBack"] is False  # the combiner is behind the pilot's view


def test_runway_points_and_the_glide_path_line_meet_at_the_aim_point(out):
    assert out["aimWorld"] == pytest.approx([0, 0, -750])  # 750 m north (world z is south)
    assert out["rightEdge"] == pytest.approx([15, 0, -500])  # right of a northbound runway is east
    # Seen from the glide path, the aim point is exactly on the glide path reference line.
    assert out["aimScreen"]["x"] == pytest.approx(500, abs=0.01)
    assert out["aimScreen"]["y"] == pytest.approx(out["gpScreen"]["y"], abs=0.05)


def test_speed_bugs_on_the_tape(out):
    assert out["drawnApproach"] is True
    assert "A" in out["approachTexts"] and "GP" in out["approachTexts"]
    assert "X" not in out["approachTexts"]  # 140 kt is off the tape at 65 kt
