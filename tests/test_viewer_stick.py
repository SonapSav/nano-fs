"""Gamepad settings (flightsim/viewer/stick.js), run with Node when available: per-device
axis mapping, inversion, centre calibration, throttle lever, axis detection, device
choice and migration of the earlier settings."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
const store = {};
globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } };
const s = await import(%s);
const out = {};
const std = s.defaultProfile(true);
// Standard pad: stick right -> roll +, stick forward (axis 1 = -1) -> pitch +, right stick X right -> rudder +.
out.std = ["roll", "pitch", "rudder", "throttle"].map((c) => s.controlValue(std, [0.5, -1, 0.25, 0], c));
// The owner's faulty pad: roll moved to the right stick X (axis 2), rudder unmapped.
const p = s.defaultProfile(true);
p.map.roll = { axis: 2, invert: false }; p.map.rudder = { axis: null, invert: false };
p.centre = { 2: 0.2, 1: -0.01 };
out.remapped = ["roll", "pitch", "rudder"].map((c) => s.controlValue(p, [-0.727, -0.01, 0.2, 0], c));
out.remappedRight = s.controlValue(p, [-0.727, -0.01, 1.0, 0], "roll");
// Joystick without a standard layout: nothing mapped until set; throttle lever absolute and invertible.
const j = s.defaultProfile(false);
out.joyUnmapped = s.controlValue(j, [0.3, 0.3, 0.3], "roll");
j.map.throttle = { axis: 2, invert: true };
out.lever = [s.controlValue(j, [0, 0, -1], "throttle"), s.controlValue(j, [0, 0, 1], "throttle"), s.controlValue(j, [0, 0, 0], "throttle")];
// Detection: the axis that moved most; inverted when it moved negative; nothing for small moves.
const base = [-0.727, -0.01, 0.2, 0.02, -1, -1];
out.detectRight = s.detectAxis(base, [[-0.727, -0.01, 0.6, 0.02, -1, -1], [-0.73, 0.0, 0.98, 0.03, -1, -1]]);
out.detectForwardStd = s.detectAxis(base, [[-0.727, -0.95, 0.2, 0.02, -1, -1]]);
out.detectNone = s.detectAxis(base, [[-0.6, 0.1, 0.3, 0.02, -1, -1]]);
// Device choice: saved profile first, then standard layout, then anything connected.
const settings = s.loadSettings();
const hotas = { id: "HOTAS", mapping: "", connected: true }, xbox = { id: "Xbox", mapping: "standard", connected: true };
out.choose1 = s.choosePad([hotas, xbox], settings).id;
settings.devices.HOTAS = s.defaultProfile(false);
out.choose2 = s.choosePad([xbox, hotas], settings).id;
out.choose3 = s.choosePad([null, hotas], { devices: {} }).id;
// Migration: v2 feel kept, its axis-0-2 centre dropped.
store["flightsim.stick.v2"] = JSON.stringify({ pitch: { sensitivity: 0.4, expo: 0.6 }, roll: { sensitivity: 0.7, expo: 0.3 }, rudder: { sensitivity: 0.7, expo: 0.3 }, deadzone: 0.23, centre: [-0.282, 0.008, 0.216] });
delete store["flightsim.stick.v3"];
const m = s.loadSettings();
out.migrated = { pitch: m.pitch, deadzone: m.deadzone, devices: m.devices, centre: m.centre ?? null };
// A saved v3 profile with an implausible centre is rejected as a whole.
s.saveSettings({ ...m, devices: { Xbox: { ...s.defaultProfile(true), centre: { 0: -0.72 } } } });
out.badCentreRejected = Object.keys(s.loadSettings().devices).length === 0;
console.log(JSON.stringify(out));
""" % json.dumps((VIEWER / "stick.js").as_uri())


@pytest.fixture(scope="module")
def out():
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_standard_pad_defaults_keep_the_pilots_sense(out):
    roll, pitch, rudder, throttle = out["std"]
    assert roll == pytest.approx(0.5) and pitch == pytest.approx(1.0) and rudder == pytest.approx(0.25)
    assert throttle is None  # triggers, not an axis


def test_remapped_roll_uses_its_own_axis_and_centre(out):
    roll, pitch, rudder = out["remapped"]
    assert roll == pytest.approx(0.0) and rudder is None  # the faulty axis 0 no longer matters
    assert pitch == pytest.approx(0.0)
    assert out["remappedRight"] == pytest.approx(1.0)  # full right still reaches +1 after centring


def test_joystick_axes_and_throttle_lever(out):
    assert out["joyUnmapped"] is None
    assert out["lever"] == [pytest.approx(1.0), pytest.approx(0.0), pytest.approx(0.5)]  # inverted lever


def test_axis_detection(out):
    assert out["detectRight"] == {"axis": 2, "invert": False}
    assert out["detectForwardStd"] == {"axis": 1, "invert": True}
    assert out["detectNone"] is None


def test_device_choice(out):
    assert (out["choose1"], out["choose2"], out["choose3"]) == ("Xbox", "HOTAS", "HOTAS")


def test_migration_keeps_the_feel_and_drops_the_old_centre(out):
    m = out["migrated"]
    assert m["pitch"] == {"sensitivity": 0.4, "expo": 0.6} and m["deadzone"] == 0.23
    assert m["devices"] == {} and m["centre"] is None
    assert out["badCentreRejected"]
