"""Indicated airspeed (flightsim/viewer/gauges.js): the model's calibrated airspeed through
the POH's airspeed calibration (Figure 5-1, normal static source). Run with Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
KT, DEG = 1852 / 3600, 3.141592653589793 / 180


def _ias(cases):
    script = f"""
const {{ indicatedKt }} = await import({json.dumps((VIEWER / "gauges.js").as_uri())});
console.log(JSON.stringify({json.dumps(cases)}.map(([kcas, flap]) => indicatedKt({{ cas_mps: kcas * {KT}, flap_pos_rad: flap * {DEG} }}))));
"""
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_table_points_and_interpolation():
    # POH Figure 5-1: flaps up 62 KCAS = 60 KIAS, 107 = 110; flaps 10 49 = 40; flaps 30 47 = 40, 84 = 85.
    got = _ias([[62, 0], [107, 0], [49, 10], [47, 30], [84, 30], [70, 0], [59, 0]])
    assert got[:6] == pytest.approx([60, 110, 40, 40, 85, 70], abs=0.01)
    assert got[6] == pytest.approx(55, abs=0.01)  # halfway between 56 and 62 KCAS


def test_flaps_blend_and_ends():
    mid = _ias([[50, 20]])[0]
    ten, thirty = _ias([[50, 10], [50, 30]])
    assert mid == pytest.approx((ten + thirty) / 2, abs=0.01)
    low, high, still = _ias([[30, 0], [170, 0], [0, 0]])
    assert low == pytest.approx(24, abs=0.01) and high == pytest.approx(176, abs=0.01)  # end offsets
    assert still == 0.0  # never negative
