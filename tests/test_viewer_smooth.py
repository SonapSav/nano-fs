"""Smooth display between frames (flightsim/viewer/smooth.js) and the performance readout
(flightsim/viewer/perf.js), run with Node when available."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
const sm = await import(%s);
const pf = await import(%s);
const out = {};
// Frames at 30 Hz arriving with jitter; the display refreshes at 60 Hz.
const fb = new sm.FrameBuffer();
const row = (t) => ({ t_s: t, x: t * 10, psi_rad: 3.1 + t * 0.1, label: "r" + t.toFixed(3) });
let next = 0, wall = 0, frameAt = [], shown = [];
const jitter = [0, 4, -3, 5, -4, 1, 30, -2]; // ms; one 30 ms late frame
for (let k = 0; k < 120; k++) frameAt.push(k / 30 + jitter[k %% jitter.length] / 1000);
for (let r = 0; r < 220; r++) {
  wall = r / 60;
  while (next < frameAt.length && frameAt[next] <= wall) fb.push(row(next++ / 30));
  const s = fb.sample(r ? 1 / 60 : 0, 1, false);
  if (s && r > 30) shown.push([wall, s.t_s, s.x, s.psi_rad]);
}
const steps = shown.slice(1).map((s, i) => s[1] - shown[i][1]);
out.minStep = Math.min(...steps); out.maxStep = Math.max(...steps);
out.lag = shown.map(([w, t]) => w - t);
out.xMatches = shown.every(([, t, x]) => Math.abs(x - t * 10) < 1e-9);
out.angleWraps = sm.lerpRow({ psi_rad: 3.1 }, { psi_rad: -3.1 }, 0.5).psi_rad;
// A seek back resets; a long stall jumps instead of rushing.
fb.push(row(0.5)); out.afterSeek = fb.sample(0.016, 1, false).t_s;
// Paused: the display time stays.
const p0 = fb.sample(0.5, 1, true).t_s, p1 = fb.sample(0.5, 1, true).t_s; out.pausedSame = p0 === p1;
// Frame statistics: 60 Hz with one 50 ms hitch (two refreshes missed).
const st = new pf.FrameStats(10000);
let t = 0; for (let i = 0; i < 120; i++) { t += i === 60 ? 50 : 1000 / 60; st.frame(t); }
for (let i = 0; i < 30; i++) st.message(i * 33.3);
out.stats = st.summary();
// Frame-rate limit at 144 Hz with jitter: half (72 fps) and a third (48 fps); a refresh the
// browser misses does not shift the cadence.
const pace = (divisor, miss) => {
  const pc = new pf.FramePacer(divisor), drawn = [];
  let tt = 0;
  for (let i = 0; i < 1440; i++) {
    tt += 1000 / 144 + (i %% 3 - 1) * 0.3;
    if (i === miss) continue; // no callback for this refresh
    if (pc.tick(tt)) drawn.push(tt);
  }
  const late = drawn.filter((x) => x > 1000); // after the refresh rate is known
  return { fps: late.length / ((late[late.length - 1] - late[0]) / 1000), gaps: late.slice(1).map((x, k) => x - late[k]), refresh: pc.refreshMs };
};
out.pace1 = pace(1, -1); out.pace2 = pace(2, 701); out.pace3 = pace(3, -1);
console.log(JSON.stringify(out));
""" % (json.dumps((VIEWER / "smooth.js").as_uri()), json.dumps((VIEWER / "perf.js").as_uri()))


@pytest.fixture(scope="module")
def out():
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_display_moves_evenly_despite_jitter(out):
    # Every refresh advances about 1/60 s of flight time: no stalls, no double steps.
    assert out["minStep"] > 0.010 and out["maxStep"] < 0.024
    assert out["xMatches"]  # interpolated, not stepped
    lags = out["lag"]
    assert 0.03 < min(lags) and max(lags) < 0.12  # a little behind the newest frame, steady


def test_angles_interpolate_the_short_way_round(out):
    assert abs(out["angleWraps"]) == pytest.approx(3.14159, abs=0.01)  # through 180 deg, not through 0


def test_seek_and_pause(out):
    assert out["afterSeek"] == pytest.approx(0.5)
    assert out["pausedSame"]


def test_frame_statistics(out):
    s = out["stats"]
    assert s["refreshMs"] == pytest.approx(1000 / 60, abs=0.01)
    assert s["dropped"] == 2 and s["worstMs"] == pytest.approx(50)
    assert s["msgMedianMs"] == pytest.approx(33.3, abs=0.01)


def test_frame_limit_draws_a_whole_fraction_of_the_refreshes(out):
    assert out["pace1"]["refresh"] == pytest.approx(1000 / 144, abs=0.4)
    assert out["pace1"]["fps"] == pytest.approx(144, rel=0.01)
    for key, fps in (("pace2", 72), ("pace3", 48)):
        p = out[key]
        assert p["fps"] == pytest.approx(fps, rel=0.02)
        divisor = 144 // fps
        refreshes = [round(g / (1000 / 144)) for g in p["gaps"]]
        # Every gap is `divisor` refreshes; the one missed refresh delays one frame by one.
        assert all(r in (divisor, divisor + 1) for r in refreshes)
        assert sum(r != divisor for r in refreshes) <= 1
