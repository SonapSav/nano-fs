"""Gamepad settings (flightsim/viewer/stick.js), run with Node when available: per-device
axis mapping, inversion, centre calibration, throttle lever, axis detection, device
choice, migration of the earlier settings, button bindings (buttons or hat axes) and the
feel (sensitivity, expo, dead zone) per device."""

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
// Buttons: standard defaults, nothing bound on a joystick; values from buttons and hat axes.
out.stdButtons = s.defaultProfile(true).buttons;
out.joyButtons = s.defaultProfile(false).buttons;
const hotasPad = { buttons: [{ pressed: true, value: 1 }, { pressed: false, value: 0 }], axes: [0, 0, 0.9, 0, 0, 0, -1] };
out.values = [{ button: 0 }, { button: 1 }, { button: 9 }, { axis: 6, dir: -1 }, { axis: 6, dir: 1 }, null].map((b) => s.buttonValue(hotasPad, b));
out.trigger = s.buttonValue({ buttons: [{ pressed: true, value: 0.4 }], axes: [] }, { button: 0 });
// Detection: a newly pressed button; else a hat axis from rest; never a mapped or resting-deflected axis.
const rest = { buttons: [0, 1, 0], axes: [0, 0, -1, 0, 0] };
out.detBtn = s.detectButton(rest, [{ buttons: [0, 1, 0], axes: [0, 0, -1, 0, 0] }, { buttons: [0, 1, 1], axes: [0, 0, -1, 0, -1] }]);
out.detHat = s.detectButton(rest, [{ buttons: [0, 1, 0], axes: [0, 0, -1, 0, -1] }]);
out.detLever = s.detectButton(rest, [{ buttons: [0, 1, 0], axes: [0, 0, 1, 0, 0] }]);
out.detMapped = s.detectButton(rest, [{ buttons: [0, 1, 0], axes: [0.9, 0, -1, 0, 0] }], [0]);
// A profile saved before button mapping gets the default buttons; bindings round-trip through storage.
const st = { ...structuredClone(s.DEFAULTS), devices: { Xbox: { map: s.defaultProfile(true).map, centre: {} } } };
out.filled = s.profileFor(st, xbox).buttons.brake;
const h = s.profileFor(st, hotas);
h.buttons.trim_nose_down = { axis: 6, dir: -1 }; h.buttons.brake = { button: 0 };
s.saveSettings(st);
const back = s.loadSettings();
out.saved = back.devices.HOTAS.buttons;
s.saveSettings({ ...st, devices: { HOTAS: { ...h, buttons: { brake: { axis: 1, dir: 2 } } } } });
out.badBindingRejected = Object.keys(s.loadSettings().devices).length === 0;
// Feel per device: an older profile gets a copy of the shared feel; devices then differ.
const fs = { ...structuredClone(s.DEFAULTS), pitch: { sensitivity: 0.4, expo: 0.6 }, devices: { Xbox: { map: s.defaultProfile(true).map, centre: {} } } };
const xf = s.profileFor(fs, xbox).feel, hf = s.profileFor(fs, hotas).feel;
hf.pitch.sensitivity = 0.8; hf.roll = { sensitivity: 1, expo: 0.2 }; hf.deadzone = 0.05;
out.feel = { xbox: xf, hotas: hf, template: { pitch: fs.pitch, deadzone: fs.deadzone } };
s.saveSettings(fs);
out.feelSaved = s.loadSettings().devices.HOTAS.feel;
s.saveSettings({ ...fs, devices: { HOTAS: { ...fs.devices.HOTAS, feel: { ...hf, deadzone: 0.9 } } } });
out.badFeelRejected = Object.keys(s.loadSettings().devices).length === 0;
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


def test_default_buttons(out):
    b = out["stdButtons"]
    assert (b["flaps_up"], b["flaps_down"], b["brake"]) == ({"button": 4}, {"button": 5}, {"button": 1})
    assert (b["trim_nose_down"], b["trim_nose_up"]) == ({"button": 12}, {"button": 13})
    assert (b["throttle_up"], b["throttle_down"]) == ({"button": 7}, {"button": 6})
    assert all(v is None for v in out["joyButtons"].values())


def test_button_values_from_buttons_and_hat_axes(out):
    assert out["values"] == [1, 0, 0, 1, 0, 0]  # pressed, released, missing, hat down, hat up, unbound
    assert out["trigger"] == pytest.approx(0.4)  # analog buttons keep their value


def test_button_detection(out):
    assert out["detBtn"] == {"button": 2}  # buttons win; button 1 was already held
    assert out["detHat"] == {"axis": 4, "dir": -1}
    assert out["detLever"] is None  # the throttle lever rested at an end
    assert out["detMapped"] is None  # the roll axis is mapped to a stick control


def test_button_bindings_saved_and_old_profiles_filled(out):
    assert out["filled"] == {"button": 1}
    assert out["saved"]["trim_nose_down"] == {"axis": 6, "dir": -1}
    assert out["saved"]["brake"] == {"button": 0} and out["saved"]["flaps_up"] is None
    assert out["badBindingRejected"]


def test_feel_per_device(out):
    f = out["feel"]
    assert f["xbox"]["pitch"] == {"sensitivity": 0.4, "expo": 0.6}  # copied from the shared feel
    assert f["hotas"]["pitch"] == {"sensitivity": 0.8, "expo": 0.6} and f["hotas"]["roll"] == {"sensitivity": 1, "expo": 0.2}
    assert f["template"] == {"pitch": {"sensitivity": 0.4, "expo": 0.6}, "deadzone": 0.08}  # untouched by device edits
    assert out["feelSaved"]["deadzone"] == 0.05 and out["feelSaved"]["roll"]["sensitivity"] == 1
    assert out["badFeelRejected"]
