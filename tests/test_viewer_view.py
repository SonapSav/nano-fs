"""Re-centring the view (flightsim/viewer/view.js), run with Node when available: a short
eased move back to the default chase view, the shortest way round."""

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
const v = await import(%s);
const from = { orbit: { azimuth: 2 * Math.PI + 2.8, elevation: 1.2, distance: 220 }, head: { yaw: 1.5, pitch: -0.5 } };
const ks = [0, 0.05, 0.25, 0.5, 0.75, 0.95, 1];
console.log(JSON.stringify({ steps: ks.map((k) => v.recentreView(from, k)), s: v.RECENTRE_S, def: v.DEFAULT_ORBIT }));
""" % json.dumps((VIEWER / "view.js").as_uri())


@pytest.fixture(scope="module")
def out():
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_recentre_ends_at_the_default_view(out):
    end = out["steps"][-1]
    assert end["orbit"] == pytest.approx(out["def"]) and end["head"] == pytest.approx({"yaw": 0, "pitch": 0})
    assert 0.3 <= out["s"] <= 0.8  # short: no long sweeping pan


def test_recentre_starts_where_the_view_was_and_eases(out):
    first, early, mid = out["steps"][0], out["steps"][1], out["steps"][3]
    assert first["orbit"]["azimuth"] == pytest.approx(2.8)  # extra full turns dropped
    assert first["orbit"]["distance"] == pytest.approx(220) and first["head"]["yaw"] == pytest.approx(1.5)
    assert early["orbit"]["azimuth"] > 2.8 * 0.95  # gentle start
    assert mid["orbit"]["azimuth"] == pytest.approx(1.4)  # halfway at half time
    assert mid["orbit"]["distance"] == pytest.approx(math.sqrt(220 * 22))  # even in log scale


def test_recentre_takes_the_shortest_way_round(out):
    az = [s["orbit"]["azimuth"] for s in out["steps"]]
    assert all(a >= b for a, b in zip(az, az[1:]))  # 2.8 rad turns back through 0, not the long way
