"""World placement and map (flightsim/world/geo.py, flightsim/viewer/geo.js): the WGS84
transverse Mercator map against independent checks (meridian arc by integration, true
local scale, conformality, convergence by finite differences), the original sphere kept
exactly, and the viewer's port agreeing with Python."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from scipy.integrate import quad

from flightsim.world.geo import SPHERE, WGS84_A_M, WGS84_F, Geodesy

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
E2 = WGS84_F * (2 - WGS84_F)
ORIGINS = [(0.0, 0.0), (37.9, 23.7), (-45.0, 170.0), (60.0, -150.0)]  # equator, Athens area, far south, far north


def meridian_radius(lat):
    return WGS84_A_M * (1 - E2) / (1 - E2 * math.sin(lat) ** 2) ** 1.5


def normal_radius(lat):
    return WGS84_A_M / math.sqrt(1 - E2 * math.sin(lat) ** 2)


@pytest.mark.parametrize("origin", ORIGINS)
def test_round_trip_and_origin(origin):
    g = Geodesy("wgs84", *origin)
    assert g.to_map(*map(math.radians, origin)) == pytest.approx((0.0, 0.0), abs=1e-9)
    for n in (-30000.0, 0.0, 30000.0):
        for e in (-30000.0, 0.0, 30000.0):
            assert g.to_map(*g.to_geodetic(n, e)) == pytest.approx((n, e), abs=1e-3)  # within a millimetre


@pytest.mark.parametrize("origin", ORIGINS)
def test_north_along_the_meridian_is_the_meridian_arc(origin):
    g = Geodesy("wgs84", *origin)
    lat0, lon0 = map(math.radians, origin)
    for dlat in (-0.004, 0.004):  # about +/-25 km
        arc = quad(meridian_radius, lat0, lat0 + dlat, epsabs=1e-12)[0]
        assert g.to_map(lat0 + dlat, lon0)[0] == pytest.approx(arc, abs=1e-5)


@pytest.mark.parametrize("origin", ORIGINS)
def test_local_scale_conformality_and_convergence(origin):
    g = Geodesy("wgs84", *origin)
    lat, lon = g.to_geodetic(3000.0, 20000.0)  # 20 km east of the central meridian
    h = 1e-7
    n0, nE, nN = g.to_map(lat, lon), g.to_map(lat, lon + h), g.to_map(lat + h, lon)
    scale_e = math.hypot(nE[0] - n0[0], nE[1] - n0[1]) / (normal_radius(lat) * math.cos(lat) * h)
    scale_n = math.hypot(nN[0] - n0[0], nN[1] - n0[1]) / (meridian_radius(lat) * h)
    k_expected = 1 + 20000.0**2 / (2 * normal_radius(lat) * meridian_radius(lat))  # transverse Mercator scale off the meridian
    assert scale_e == pytest.approx(k_expected, abs=2e-7) and scale_n == pytest.approx(k_expected, abs=2e-7)
    bearing_of_true_north = math.atan2(nN[1] - n0[1], nN[0] - n0[0])  # on the map
    assert bearing_of_true_north == pytest.approx(-g.convergence_rad(lat, lon), abs=1e-7)
    # True north and true east stay perpendicular on the map (conformal).
    east_bearing = math.atan2(nE[1] - n0[1], nE[0] - n0[0])
    assert east_bearing - bearing_of_true_north == pytest.approx(math.pi / 2, abs=1e-6)


def test_sphere_is_the_original_mapping():
    assert SPHERE.to_map(0.001, -0.002) == (0.001 * 6371000.0, -0.002 * 6371000.0)
    assert SPHERE.to_geodetic(5556.0, -100.0) == (5556.0 / 6371000.0, -100.0 / 6371000.0)
    assert SPHERE.convergence_rad(0.5, 0.5) == 0.0
    assert Geodesy.from_config(None) == SPHERE  # logs before the world block
    with pytest.raises(ValueError):
        Geodesy("sphere", 10.0, 0.0)
    with pytest.raises(ValueError):
        Geodesy.from_config({"geodesy": "wgs84", "origin": 1})


def test_the_sphere_was_off_by_this_much():
    """What the original mapping called 5556 m north and east of 0, 0 is, on WGS84."""
    wgs = Geodesy("wgs84")
    assert wgs.to_map(*SPHERE.to_geodetic(5556.0, 0.0))[0] == pytest.approx(5525.0, abs=0.5)  # 0.56 % short
    assert wgs.to_map(*SPHERE.to_geodetic(0.0, 5556.0))[1] == pytest.approx(5562.2, abs=0.5)  # 0.11 % long


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_viewer_port_agrees():
    points = [(o, n, e) for o in ORIGINS for n, e in ((0.0, 0.0), (-18000.0, 7000.0), (25000.0, -22000.0))]
    script = f"""
    const {{ Geodesy }} = await import({json.dumps((VIEWER / "geo.js").as_uri())});
    const pts = {json.dumps(points)};
    const out = pts.map(([o, n, e]) => {{
      const g = new Geodesy({{ model: "wgs84", origin_lat_deg: o[0], origin_lon_deg: o[1] }});
      const [lat, lon] = g.toGeodetic(n, e);
      return [lat, lon, g.toMap(lat, lon), g.convergence(lat, lon)];
    }});
    const s = new Geodesy(null);
    out.push([...s.toGeodetic(5556, -100), s.toMap(0.001, -0.002), s.convergence(0.5, 0.5)]);
    console.log(JSON.stringify(out));
    """
    out = json.loads(subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True).stdout)
    for (o, n, e), (lat, lon, back, gamma) in zip(points, out):
        g = Geodesy("wgs84", *o)
        assert (lat, lon) == pytest.approx(g.to_geodetic(n, e), abs=1e-12)
        assert back == pytest.approx(list(g.to_map(lat, lon)), abs=1e-6)
        assert gamma == pytest.approx(g.convergence_rad(lat, lon), abs=1e-12)
    lat, lon, back, gamma = out[-1]
    assert (lat, lon) == SPHERE.to_geodetic(5556.0, -100.0) and back == list(SPHERE.to_map(0.001, -0.002)) and gamma == 0
