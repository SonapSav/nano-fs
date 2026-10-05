"""Viewer sound (flightsim/viewer/sound.js): the frame-to-sound mapping, run with Node when
available, and the stall horn threshold checked against the flight model and the POH."""

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from flightsim.analysis.maneuvers import KT_TO_MPS, loading_for, stall_speed, trim_at_cas
from flightsim.core import Controls

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
LB, IN = 0.45359237, 0.0254
HORN_ALPHA_DEG = float(re.search(r"STALL_HORN_ALPHA_DEG = ([\d.]+)", (VIEWER / "sound.js").read_text()).group(1))

SCRIPT = """
const { soundParams } = await import(%s);
const row = (o) => ({ t_s: 10, engine_rpm: 2300, engine_power_w: 0.6 * 160 * 745.7, cas_mps: 95 * 0.514444, alpha_rad: 0.03, flap_pos_rad: 0, ...o });
const out = {
  cruise: soundParams(row({})),
  stopped: soundParams(row({ engine_rpm: 0, engine_power_w: 0 })),
  idle: soundParams(row({ engine_rpm: 900, engine_power_w: 0.05 * 160 * 745.7 })),
  cockpit: soundParams(row({}), null, { view: "cockpit" }),
  far: soundParams(row({}), null, { distanceM: 200 }),
  slow: soundParams(row({ cas_mps: 60 * 0.514444 })),
  hornOn: soundParams(row({ alpha_rad: 7.8 * Math.PI / 180 })),
  hornBelow: soundParams(row({ alpha_rad: 7.4 * Math.PI / 180 })),
  hornHeld: soundParams(row({ alpha_rad: 7.4 * Math.PI / 180 }), null, { horn: true }),
  hornParked: soundParams(row({ alpha_rad: 0.2, cas_mps: 0 })),
  flapsMoving: soundParams(row({ t_s: 10.1, flap_pos_rad: 0.5 * Math.PI / 180 }), row({ flap_pos_rad: 0 })),
  flapsStill: soundParams(row({ t_s: 10.1 }), row({})),
};
console.log(JSON.stringify(out));
""" % json.dumps((VIEWER / "sound.js").as_uri())


@pytest.fixture(scope="module")
def p():
    if NODE is None:
        pytest.skip("node not installed")
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_engine_follows_rpm_and_power(p):
    assert p["cruise"]["engineHz"] == pytest.approx(2300 / 30)  # 2 firings per revolution
    assert p["stopped"]["engineHz"] == 0 and p["stopped"]["engineLevel"] == 0
    assert 0 < p["idle"]["engineLevel"] < p["cruise"]["engineLevel"]
    assert p["idle"]["engineBrightHz"] < p["cruise"]["engineBrightHz"]


def test_cockpit_is_muffled_and_distance_fades(p):
    assert p["cockpit"]["engineBrightHz"] < p["cruise"]["engineBrightHz"]
    assert p["far"]["engineLevel"] < 0.2 * p["cruise"]["engineLevel"]


def test_wind_rises_with_airspeed(p):
    assert p["slow"]["windLevel"] < p["cruise"]["windLevel"] and p["slow"]["windHz"] < p["cruise"]["windHz"]


def test_stall_horn_threshold_with_hysteresis(p):
    assert p["hornOn"]["horn"] and not p["hornBelow"]["horn"] and p["hornHeld"]["horn"]
    assert not p["hornParked"]["horn"]


def test_flap_motor_only_while_flaps_move(p):
    assert p["flapsMoving"]["flapLevel"] > 0 and p["flapsStill"]["flapLevel"] == 0


@pytest.mark.parametrize("cg_in,fuel_lb", [(39.5, 40), (47.3, 370)])
@pytest.mark.parametrize("flaps", [0.0, 1.0])
def test_stall_horn_sounds_5_to_10_kt_above_the_stall(cg_in, fuel_lb, flaps):
    """POH Section 4 (Stalls): the horn sounds 5-10 kt above the stall in all configurations.
    In the model at 2400 lb, the horn's angle of attack must be reached within that band."""
    loading = loading_for("c172p", 2400 * LB, cg_in * IN, fuel_lb * LB)
    vs_kt = stall_speed("c172p", loading, flaps).vs_cas_mps / KT_TO_MPS

    def alpha_deg(margin_kt):
        core, _, u = trim_at_cas("c172p", 1524.0, (vs_kt + margin_kt) * KT_TO_MPS, loading, Controls(flaps=flaps))
        core.step(u)
        return math.degrees(core.state().alpha_rad)

    assert alpha_deg(10.0) < HORN_ALPHA_DEG < alpha_deg(5.0)
