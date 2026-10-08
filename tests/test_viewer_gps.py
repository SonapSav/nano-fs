"""GPS display computations (flightsim/viewer/gps.js), run with Node when available:
formatting, the target, ground speed and track, distance, true bearing (also off the
origin's meridian, where map and true north differ) and time en route."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from flightsim.world.geo import Geodesy

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


@pytest.fixture(scope="module")
def out():
    wgs0 = Geodesy("wgs84")
    athens = Geodesy("wgs84", 37.9, 23.7)
    # 1: 2 km west of the threshold (-500, 0 on the map at 0, 0), flying east at 30 m/s.
    lat1, lon1 = wgs0.to_geodetic(0.0, -2500.0)
    # 2: 20 km east of the Athens origin's meridian; the target 1 km due true north.
    lat2, lon2 = athens.to_geodetic(3000.0, 20000.0)
    tn, te = athens.to_map(lat2 + 1000.0 / 6.36e6, lon2)
    cases = {
        "approach": [{"lat_rad": lat1, "lon_rad": lon1, "v_north_mps": 0.0, "v_east_mps": 30.0},
                     {"model": "wgs84", "origin_lat_deg": 0, "origin_lon_deg": 0},
                     {"approach": {"heading_deg": 90, "threshold_north_m": 0, "threshold_east_m": -500}}],
        "offMeridian": [{"lat_rad": lat2, "lon_rad": lon2, "v_north_mps": 10.0, "v_east_mps": 0.0},
                        {"model": "wgs84", "origin_lat_deg": 37.9, "origin_lon_deg": 23.7},
                        {"target": {"name": "T", "north_m": tn, "east_m": te}}],
        "parked": [{"lat_rad": 0.0, "lon_rad": 0.0, "v_north_mps": 0.2, "v_east_mps": 0.1}, None, {}],
    }
    script = f"""
    const gps = await import({json.dumps((VIEWER / "gps.js").as_uri())});
    const {{ Geodesy }} = await import({json.dumps((VIEWER / "geo.js").as_uri())});
    const cases = {json.dumps(cases)};
    const out = {{}};
    for (const [k, [row, world, s]] of Object.entries(cases)) out[k] = gps.gpsData(row, new Geodesy(world), s.target ?? gps.gpsTarget(s));
    out.fmt = [gps.formatLat(37.9), gps.formatLat(-0.5), gps.formatLon(23.7), gps.formatLon(-122.375), gps.formatLon(0), gps.formatLat(-1e-9), gps.formatLat(37.999999)];
    out.rwy = [90, 270, 3, 0, 184].map(gps.runwayNumber);
    out.targets = [gps.gpsTarget(null), gps.gpsTarget({{ takeoff: {{ heading_deg: 90, threshold_north_m: 0, threshold_east_m: -500 }} }})];
    console.log(JSON.stringify(out));
    """
    r = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_formatting_and_targets(out):
    assert out["fmt"] == ["N 37°54.000'", "S 00°30.000'", "E 023°42.000'", "W 122°22.500'", "E 000°00.000'", "N 00°00.000'", "N 38°00.000'"]
    assert out["rwy"] == ["09", "27", "36", "36", "18"]
    assert out["targets"][0] == {"name": "AIRFIELD", "north_m": 0, "east_m": 0}
    assert out["targets"][1] == {"name": "RWY 09", "north_m": 0, "east_m": -500}


def test_on_final(out):
    d = out["approach"]
    assert d["target"] == "RWY 09"
    assert d["distNm"] == pytest.approx(2000 / 1852, rel=1e-6)
    assert d["bearingDeg"] == pytest.approx(90.0, abs=1e-6) and d["trackDeg"] == pytest.approx(90.0)
    assert d["gsKt"] == pytest.approx(30 * 3600 / 1852)
    assert d["eteS"] == pytest.approx(2000 / 30, rel=1e-6)


def test_bearing_is_true_off_the_origin_meridian(out):
    d = out["offMeridian"]
    # The target is due true north; on the map it is 0.13 deg off map north (convergence).
    assert d["bearingDeg"] % 360 == pytest.approx(0.0, abs=0.01) or d["bearingDeg"] == pytest.approx(360.0, abs=0.01)
    assert d["distNm"] == pytest.approx(1000 / 1852, rel=0.01)
    assert d["trackDeg"] == pytest.approx(0.0, abs=1e-9)


def test_parked(out):
    d = out["parked"]
    assert d["trackDeg"] is None and d["eteS"] is None and d["target"] == "AIRFIELD"
