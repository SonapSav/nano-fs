"""Keyboard flying (flightsim/viewer/input.js), run with Node when available: arrow keys
start gentle and build up while held; Shift gives full deflection. Gamepad buttons as
bound per device (flaps one detent per press, trim from a hat axis, brakes)."""

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
let pads = [];
globalThis.navigator = { getGamepads: () => pads };
const m = await import(%s);
const p = new m.PilotInput();
p.reset(0.5);
const run = (s) => { let v; for (let t = 0; t < s; t += 0.02) v = p.update(0.02); return v; };
const out = {};
p.keydown({ code: "ArrowRight", shiftKey: false, repeat: false });
out.tap = run(0.2).aileron;
out.held1 = run(0.8).aileron;
out.held3 = run(2.0).aileron;
p.keyup({ code: "ArrowRight", shiftKey: false });
out.released = run(1.5).aileron;
p.keydown({ code: "ArrowRight", shiftKey: false, repeat: false });
out.retap = run(0.3).aileron;  // building starts again after a release
p.keyup({ code: "ArrowRight", shiftKey: false });
run(1.5);
p.keydown({ code: "ArrowDown", shiftKey: true, repeat: false });
out.shift = run(1.0).elevator;
out.consts = [m.KEY_START, m.KEY_MAX, m.KEY_BUILD_S];
p.keyup({ code: "ArrowDown", shiftKey: false });
// A joystick without a standard layout: hat (axis 6) bound to trim, buttons to flaps and brakes.
const btn = (on) => ({ pressed: on, value: on ? 1 : 0 });
const hotas = { id: "HOTAS", mapping: "", connected: true, axes: [0, 0, 0, 0, 0, 0, 0], buttons: Array.from({ length: 12 }, () => btn(false)) };
pads = [hotas];
p.stick.devices.HOTAS = { ...(await import(%s)).defaultProfile(false) };
Object.assign(p.stick.devices.HOTAS.buttons, { flaps_down: { button: 3 }, trim_nose_up: { axis: 6, dir: 1 }, brake: { button: 0 } });
p.reset(0.5);
run(0.1);
out.unboundFlaps = p.value.flaps;
hotas.buttons[3] = btn(true); run(1.0);
out.flapsHeld = p.value.flaps;  // one detent per press, however long held
hotas.buttons[3] = btn(false); run(0.1); hotas.buttons[3] = btn(true); run(0.1);
out.flapsTwice = p.value.flaps;
hotas.axes[6] = 1; out.trim = run(1.0).pitch_trim; hotas.axes[6] = 0;
hotas.buttons[0] = btn(true); out.brake = run(1.0).brake;
// A standard pad keeps its defaults (D-pad up = trim nose down).
const xbox = { id: "Xbox", mapping: "standard", connected: true, axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => btn(false)) };
pads = [xbox]; p.reset(0.5); xbox.buttons[12] = btn(true);
out.stdTrim = run(1.0).pitch_trim;
console.log(JSON.stringify(out));
""" % (json.dumps((VIEWER / "input.js").as_uri()), json.dumps((VIEWER / "stick.js").as_uri()))


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("input")
    shutil.copytree(VIEWER, d / "viewer", ignore=shutil.ignore_patterns("vendor"))
    script = SCRIPT.replace((VIEWER / "input.js").as_uri(), (d / "viewer" / "input.js").as_uri())
    script = script.replace((VIEWER / "stick.js").as_uri(), (d / "viewer" / "stick.js").as_uri())
    res = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=d, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_keys_start_gentle_and_build_up_while_held(out):
    start, peak, build = out["consts"]
    assert start == pytest.approx(0.2) and peak == pytest.approx(0.6) and build == pytest.approx(1.0)
    assert 0.15 < out["tap"] < 0.3  # a short press stays a small correction
    assert out["tap"] < out["held1"] < out["held3"]
    assert out["held3"] == pytest.approx(0.6, abs=0.01)  # full build-up after holding
    assert abs(out["released"]) < 0.01  # back to centre on release
    assert out["retap"] < 0.35  # the build-up restarts after a release


def test_shift_gives_full_deflection(out):
    assert out["shift"] == pytest.approx(-1.0, abs=0.01)  # ArrowDown = pull


def test_joystick_buttons_as_bound(out):
    assert out["unboundFlaps"] == 0
    assert out["flapsHeld"] == pytest.approx(1 / 3)  # one detent per press
    assert out["flapsTwice"] == pytest.approx(2 / 3)
    assert out["trim"] == pytest.approx(-0.15, abs=0.01)  # hat up bound to nose-up trim, 0.15/s
    assert out["brake"] == pytest.approx(1.0, abs=0.01)
    assert out["stdTrim"] == pytest.approx(0.15, abs=0.01)  # standard D-pad up = nose down
