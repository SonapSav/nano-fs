"""Moving map geometry (flightsim/viewer/map.js), run with Node when available: screen
orientation (north up, track up), the flown track's bookkeeping, the predicted path,
the runway and ring spacing, the belly camera's footprint."""

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
const m = await import(%s);
const out = {};
// 1000 x 800 view, 2 nm across the shorter side; aircraft at the origin.
const north = m.makeView(1000, 800, 0, 0, 0, 2), east = m.makeView(1000, 800, 0, 0, 90, 2);
out.northUp = [north.toScreen(1000, 0), north.toScreen(0, 1000), north.mPerPx];
out.trackUp90 = [east.toScreen(0, 1000), east.toScreen(1000, 0)];
// Track: one point a second, cleared when time goes back.
const t = new m.Track();
for (let i = 0; i <= 50; i++) t.add(i * 0.1, i, 0);
out.trackCount = t.points.length;
t.add(2.0, 0, 0);
out.afterSeek = t.points.length;
// Predicted path: straight east at 50 m/s; and turning right at 3 deg/s from north.
const straight = m.predictedPath({ v_north_mps: 0, v_east_mps: 50, r_radps: 0 }, 0, 60);
out.straightEnd = straight[straight.length - 1];
const turn = m.predictedPath({ v_north_mps: 50, v_east_mps: 0, r_radps: 3 * Math.PI / 180 }, 0, 60);
out.turnEnd = turn[turn.length - 1];
const steep = m.predictedPath({ v_north_mps: 50, v_east_mps: 0, r_radps: 10 * Math.PI / 180 }, 0, 60);
out.steepEnd = steep[steep.length - 1];
out.parked = m.predictedPath({ v_north_mps: 0.1, v_east_mps: 0, r_radps: 0 }, 0, 60).length;
// Runway: the airfield's own without a task; a task's with its threshold.
const own = m.mapRunway(null), task = m.mapRunway({ approach: { threshold_north_m: 0, threshold_east_m: -500, heading_deg: 90, length_m: 1000, width_m: 30 } });
out.runways = [own, task, m.runwayToMap(task, 1000, 0), m.runwayToMap(task, 0, 15)];
out.rings = [0.5, 1, 2, 5, 10, 20].map(m.ringStepNm);
// The belly camera's footprint: a recording 2D context; the patch is the path filled in
// the camera's yellow, its corners at the footprint's screen points.
const calls = [];
let path = [];
const ctx = new Proxy({}, {
  get: (o, k) => (k in o ? o[k] : (...a) => {
    if (k === "beginPath") path = [];
    if (k === "moveTo" || k === "lineTo") path.push(a);
    if (k === "fill") calls.push({ fill: o.fillStyle, path: [...path] });
  }),
  set: (o, k, v) => ((o[k] = v), true),
});
const row = { t_s: 1, lat_rad: 0, lon_rad: 0, alt_msl_m: 500, psi_rad: 0, phi_rad: 0, theta_rad: 0, v_north_mps: 50, v_east_mps: 0, r_radps: 0 };
const { Geodesy } = await import(%s);
m.drawMap(ctx, 1000, 800, { hello: null, row, geodesy: new Geodesy({ model: "wgs84", origin_lat_deg: 0, origin_lon_deg: 0 }), track: new m.Track(),
  rangeNm: 2, northUp: true, camera: { origin: [0, 0], ground: [500, 0], corners: [[700, -200], [700, 200], [300, 200], [300, -200]] } });
out.cameraPatch = calls.find((c) => c.fill === "rgba(255, 207, 58, 0.18)")?.path ?? null;
out.cameraView = m.makeView(1000, 800, 0, 0, 0, 2).toScreen(700, -200);
console.log(JSON.stringify(out));
""" % (json.dumps((VIEWER / "map.js").as_uri()), json.dumps((VIEWER / "geo.js").as_uri()))


@pytest.fixture(scope="module")
def out():
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_north_up_and_track_up(out):
    (nx, ny), (ex, ey), m_per_px = out["northUp"]
    assert m_per_px == pytest.approx(2 * 1852 / 800)
    assert (nx, ny) == pytest.approx((500, 400 - 1000 / m_per_px))  # north is straight up
    assert (ex, ey) == pytest.approx((500 + 1000 / m_per_px, 400))  # east to the right
    (ux, uy), (lx, ly) = out["trackUp90"]
    assert (ux, uy) == pytest.approx((500, 400 - 1000 / m_per_px))  # flying east: east is up
    assert (lx, ly) == pytest.approx((500 - 1000 / m_per_px, 400))  # and north to the left


def test_track_keeps_a_point_a_second_and_resets_on_seek(out):
    assert out["trackCount"] == 6  # t = 0, 1, 2, 3, 4, 5 s
    assert out["afterSeek"] == 1


def test_predicted_path(out):
    assert out["straightEnd"] == pytest.approx([0, 3000])  # a minute at 50 m/s
    n, e = out["turnEnd"]
    assert e > 1000 and 0 < n < 3000  # curving right, toward the east (180 deg in a minute)
    n, e = out["steepEnd"]
    diameter = 2 * 50 / math.radians(10)  # a 10 deg/s turn at 50 m/s
    assert abs(n) < 150 and e == pytest.approx(diameter, abs=60)  # stops after half a turn, due east of the start
    assert out["parked"] == 0


def test_runways_and_rings(out):
    own, task, far_end, right_edge = out["runways"]
    assert own == {"thresholdN": 0, "thresholdE": -500, "headingDeg": 90, "lengthM": 1000, "widthM": 30, "task": False}
    assert task["task"] and far_end == pytest.approx([0, 500]) and right_edge == pytest.approx([-15, -500])
    assert out["rings"] == [0.25, 0.25, 0.5, 2, 5, 5]


def test_camera_footprint_is_drawn_where_the_camera_looks(out):
    patch = out["cameraPatch"]
    assert patch is not None and len(patch) == 4
    assert patch[0] == pytest.approx(out["cameraView"])  # first corner, 700 m north and 200 m west
    xs = [p[0] for p in patch]
    ys = [p[1] for p in patch]
    assert max(ys) < 400  # all north of the aircraft (screen centre y = 400, north up)
    assert min(xs) < 500 < max(xs)  # centred on the aircraft's longitude
