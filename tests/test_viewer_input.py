"""Keyboard flying (flightsim/viewer/input.js), run with Node when available: arrow keys
start gentle and build up while held; Shift gives full deflection."""

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
globalThis.navigator = { getGamepads: () => [] };
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
console.log(JSON.stringify(out));
""" % json.dumps((VIEWER / "input.js").as_uri())


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("input")
    shutil.copytree(VIEWER, d / "viewer", ignore=shutil.ignore_patterns("vendor"))
    script = SCRIPT.replace((VIEWER / "input.js").as_uri(), (d / "viewer" / "input.js").as_uri())
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
