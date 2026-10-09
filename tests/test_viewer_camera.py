"""Belly camera geometry (flightsim/viewer/camera.js), run with Node when available: the
gimbal's limits, wrapping, drag, held rates and eased re-centring; the camera axes; the
mount position; ground intersection on flat and hilly ground; the footprint; replaying
a recorded pointing; and how often the pointing is reported."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
D = math.pi / 180


@pytest.fixture(scope="module")
def out():
    script = f"""
    const c = await import({json.dumps((VIEWER / "camera.js").as_uri())});
    const r = {{}};
    const g = new c.Gimbal();
    r.home = g.state;
    g.slew(-10 * c.DEG, -20 * c.DEG);           // pan wraps below 0; tilt clamps at -90
    r.wrapped = g.state;
    g.slew(0, 200 * c.DEG);                     // tilt clamps at 0 (horizon)
    r.horizon = g.tilt;
    g.zoomBy(100); r.zoomMin = g.hfov;
    g.zoomBy(0.001); r.zoomMax = g.hfov;
    g.set({{ pan: 0, tilt: -45 * c.DEG, hfov: 30 * c.DEG }});
    g.drag(100, 0, 1000);                        // a tenth of the width: a tenth of the field of view
    r.dragPan = g.pan;
    g.set({{ pan: 0, tilt: -45 * c.DEG, hfov: 60 * c.DEG }});
    g.setRates({{ pan: 1 }});
    g.step(1, 0);                                // 60 deg/s at the widest view
    r.ratePanWide = g.pan;
    g.set({{ pan: 0, hfov: 6 * c.DEG }});
    g.step(1, 0);
    r.ratePanNarrow = g.pan;
    g.setRates({{ zoom: 1 }});
    g.set({{ hfov: 40 * c.DEG }});
    g.step(1, 0);
    r.zoomed = c.zoomFactor(g.hfov) / c.zoomFactor(40 * c.DEG);
    g.setRates({{}});
    g.set({{ pan: 350 * c.DEG, tilt: -10 * c.DEG, hfov: 10 * c.DEG }});
    g.startRecentre(5);
    g.step(0, 5 + c.RECENTRE_S / 2);
    r.midRecentre = g.state;                     // halfway, and the short way round (350 -> 360)
    g.step(0, 5 + c.RECENTRE_S);
    r.endRecentre = g.state;
    g.following = true; g.slew(0.01, 0); r.followAfterSlew = g.following;

    r.axes = [[0, 0, 0], [0, 0, -45], [90, 0, -90], [30, 90, -30]].map(([h, p, t]) => c.cameraAxes(h * c.DEG, p * c.DEG, t * c.DEG));
    r.mount = c.MOUNT_BODY_M;
    r.bodyLevelNorth = c.bodyToNed(0, 0, 0, c.MOUNT_BODY_M);
    r.bodyBankedEast = c.bodyToNed(30 * c.DEG, 10 * c.DEG, 90 * c.DEG, [1, 2, 3]);

    const flat = () => 100;
    r.down = c.groundIntersect({{ n: 0, e: 0, h: 1100 }}, [0, 0, 1], flat);
    const s = Math.SQRT1_2;
    r.slant = c.groundIntersect({{ n: 0, e: 0, h: 1100 }}, [s, 0, s], flat);
    r.horizontal = c.groundIntersect({{ n: 0, e: 0, h: 1100 }}, [1, 0, 0], flat);
    const hill = (n, e) => 100 + 300 * Math.exp(-((n - 3000) ** 2 + e * e) / (800 * 800)); // a 300 m hill 3 km north
    r.hill = c.groundIntersect({{ n: 0, e: 0, h: 300 }}, [1, 0, 0], hill);
    const fp = c.footprint({{ n: 0, e: 0, h: 1100 }}, c.cameraAxes(0, 0, -90 * c.DEG), 60 * c.DEG, flat);
    r.footprint = fp;
    r.footprintHorizon = c.footprint({{ n: 0, e: 0, h: 1100 }}, c.cameraAxes(0, 0, 0), 60 * c.DEG, flat);

    const track = new c.CameraTrack({{ columns: ["t_s", "on", "pan_rad", "tilt_rad", "hfov_rad"],
      samples: [[1, 1, 350 * c.DEG, -0.2, 0.5], [1.5, 1, 10 * c.DEG, -0.4, 0.5], [4, 1, 1, -1, 1], [5, 0, 1, -1, 1]] }});
    r.trackBefore = track.at(0.5);
    r.trackMid = track.at(1.25);                 // halfway from 350 to 10 deg: 0 deg
    r.trackGap = track.at(3);                    // samples 2.5 s apart: hold the earlier one
    r.trackOff = track.at(6);
    r.trackUsed = track.used;
    r.trackEmpty = new c.CameraTrack(null).empty;

    const rep = new c.CameraReporter();
    const st = {{ on: true, pan: 0, tilt: -1, hfov: 1 }};
    r.rep = [
      rep.next(st, 0), rep.next({{ ...st, pan: 0.001 }}, 500), rep.next({{ ...st, pan: 0.1 }}, 100),
      rep.next({{ ...st, pan: 0.1 }}, 1000), rep.next({{ ...st, pan: 0.2 }}, 1000), rep.next({{ ...st, on: false }}, 1001),
    ];
    console.log(JSON.stringify(r));
    """
    return json.loads(subprocess.run([NODE, "--input-type=module"], input=script, capture_output=True, text=True, check=True).stdout)


def test_gimbal_limits_and_wrapping(out):
    assert out["home"] == pytest.approx({"pan": 0, "tilt": -math.pi / 2, "hfov": 60 * D})
    assert out["wrapped"]["pan"] == pytest.approx(350 * D)
    assert out["wrapped"]["tilt"] == pytest.approx(-90 * D)
    assert out["horizon"] == 0
    assert out["zoomMin"] == pytest.approx(5 * D) and out["zoomMax"] == pytest.approx(60 * D)


def test_gimbal_drag_rates_and_zoom(out):
    assert out["dragPan"] == pytest.approx(3 * D)  # 100 px of 1000 at 30 deg
    assert out["ratePanWide"] == pytest.approx(60 * D)
    assert out["ratePanNarrow"] == pytest.approx(6 * D)  # slower when zoomed in, the same on screen
    assert out["zoomed"] == pytest.approx(2)


def test_gimbal_recentre_is_eased_the_short_way(out):
    mid, end = out["midRecentre"], out["endRecentre"]
    assert mid["pan"] == pytest.approx(355 * D)  # 350 -> 360, not back through 180
    assert mid["tilt"] == pytest.approx(-50 * D)
    assert mid["hfov"] == pytest.approx(math.sqrt(10 * 60) * D)  # zoom eased in log scale
    assert end["pan"] % (2 * math.pi) == pytest.approx(0, abs=1e-9)
    assert end["tilt"] == pytest.approx(-90 * D) and end["hfov"] == pytest.approx(60 * D)
    assert out["followAfterSlew"] is False  # moving it stops following a recording


def test_camera_axes(out):
    for a in out["axes"]:
        f, u, r = a["forward"], a["up"], a["right"]
        for v in (f, u, r):
            assert sum(x * x for x in v) == pytest.approx(1)
        assert sum(x * y for x, y in zip(f, u)) == pytest.approx(0, abs=1e-12)
        assert sum(x * y for x, y in zip(f, r)) == pytest.approx(0, abs=1e-12)
        assert r[2] == pytest.approx(0, abs=1e-12)  # stabilized: never rolls
    level = out["axes"][0]
    assert level["forward"] == pytest.approx([1, 0, 0], abs=1e-12) and level["up"] == pytest.approx([0, 0, -1], abs=1e-12)
    assert level["right"] == pytest.approx([0, 1, 0], abs=1e-12)
    down = out["axes"][2]  # heading east, straight down: the picture's top points east
    assert down["forward"] == pytest.approx([0, 0, 1], abs=1e-12) and down["up"] == pytest.approx([0, 1, 0], abs=1e-12)
    side = out["axes"][3]  # heading 30, pan 90: looking 120 deg, 30 deg down
    assert side["forward"] == pytest.approx([math.cos(30 * D) * math.cos(120 * D), math.cos(30 * D) * math.sin(120 * D), math.sin(30 * D)])


def test_mount_under_the_belly(out):
    x, y, z = out["mount"]
    assert x == pytest.approx(0.0229, abs=1e-3) and y == 0 and z == pytest.approx(0.942, abs=1e-3)
    assert out["bodyLevelNorth"] == pytest.approx(out["mount"])  # level, nose north: body = NED
    # Banked 30, pitched 10, heading east: the body x axis points east and 10 deg up.
    n, e, d = out["bodyBankedEast"]
    ref = [0, 0, 0]
    phi, th = 30 * D, 10 * D
    # body (1, 2, 3) -> NED with the standard ZYX rotation, heading 90
    xb = [0, math.cos(th), -math.sin(th)]
    yb = [-math.cos(phi), math.sin(phi) * math.sin(th), math.sin(phi) * math.cos(th)]
    zb = [math.sin(phi), math.cos(phi) * math.sin(th), math.cos(phi) * math.cos(th)]
    ref = [xb[i] * 1 + yb[i] * 2 + zb[i] * 3 for i in range(3)]
    assert [n, e, d] == pytest.approx(ref, abs=1e-12)


def test_ground_intersection(out):
    assert out["down"]["range"] == pytest.approx(1000, abs=1e-6) and out["down"]["h"] == pytest.approx(100, abs=1e-6)
    assert out["slant"]["n"] == pytest.approx(1000, abs=1e-6) and out["slant"]["range"] == pytest.approx(1000 * math.sqrt(2), abs=1e-6)
    assert out["horizontal"] is None  # level, above flat ground: no ground in sight
    # Level at 300 m toward a hill reaching 400 m: it hits the hill's near slope at 300 m.
    hill = out["hill"]
    assert hill["h"] == pytest.approx(300, abs=1e-3)
    assert 1500 < hill["n"] < 3000


def test_footprint(out):
    fp = out["footprint"]
    assert fp["centre"]["n"] == pytest.approx(0, abs=1e-6)
    half_w = 1000 * math.tan(30 * D)
    half_h = 1000 * math.tan(math.atan(math.tan(30 * D) / (16 / 9)))
    # Straight down, heading north: the picture's top is north, its right east.
    corners = [v for c in fp["corners"] for v in (c["n"], c["e"])]
    assert corners == pytest.approx([half_h, -half_w, half_h, half_w, -half_h, half_w, -half_h, -half_w], abs=1e-6)
    hz = out["footprintHorizon"]
    assert hz["centre"] is None and hz["corners"][0] is None and hz["corners"][3] is not None  # upper corners in the sky


def test_recorded_track(out):
    assert out["trackBefore"] is None
    mid = out["trackMid"]
    assert mid["pan"] % (2 * math.pi) == pytest.approx(0, abs=1e-9) or mid["pan"] == pytest.approx(2 * math.pi)
    assert mid["tilt"] == pytest.approx(-0.3)
    assert out["trackGap"]["pan"] == pytest.approx(10 * D) and out["trackGap"]["tilt"] == pytest.approx(-0.4)
    assert out["trackOff"]["on"] is False
    assert out["trackUsed"] is True and out["trackEmpty"] is True


def test_reporting_rate_and_thresholds(out):
    first, tiny, too_soon, later, small_step, turned_off = out["rep"]
    assert first == {"type": "camera", "on": True, "pan_rad": 0, "tilt_rad": -1, "hfov_rad": 1}
    assert tiny is None  # under 0.25 deg
    assert too_soon is None  # 100 ms after the last report
    assert later["pan_rad"] == pytest.approx(0.1)
    assert small_step is None  # same time as the last report
    assert turned_off == {"type": "camera", "on": False, "pan_rad": 0, "tilt_rad": -1, "hfov_rad": 1}  # on/off at once
