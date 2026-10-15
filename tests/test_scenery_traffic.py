"""Moving traffic: the scenery build's routes along OSM roads (world/scenery_traffic.py) and the
viewer's timing (viewer/trafficCore.js, run with Node when available): the model passes its
meeting point when the flown aircraft passes closest to it."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from flightsim.world import scenery_traffic as st
from flightsim.world.scenery_build import MODELS_DIR, load_spec

ROOT = Path(__file__).parent.parent
VIEWER = ROOT / "flightsim" / "viewer"
NODE = shutil.which("node")


def _ways():
    """A one-way road east along north 0 in 500 m ways (ids 1-6, nodes 10, 11, ...), a side
    road with another name joining at node 13, and the other carriageway (west, id 20)."""
    ways = {}
    for i in range(6):
        ways[i + 1] = {"nodes": [10 + i, 11 + i], "pts": [(0.0, 500.0 * i), (0.0, 500.0 * (i + 1))], "name": "Main", "ref": "E1"}
    ways[7] = {"nodes": [13, 99], "pts": [(0.0, 1500.0), (800.0, 1500.0)], "name": "Side", "ref": ""}
    ways[20] = {"nodes": [16, 10], "pts": [(30.0, 3000.0), (30.0, 0.0)], "name": "Main", "ref": "E1"}
    return ways


def test_chain_follows_the_road_in_its_direction_and_stops_at_the_lengths():
    pts, s0, s1 = st.chain(_ways(), 3, before_m=600, after_m=400)
    # back: ways 2 and 1 (600 m needs both); on: way 4 only (400 m); not the side road
    assert pts[0] == (0.0, 0.0) and pts[-1] == (0.0, 2000.0)
    assert (s0, s1) == (1000.0, 1500.0)
    assert all(p[0] == 0.0 for p in pts)


def test_resample_and_smooth_keep_the_ends_and_the_spacing():
    pts = st.resample([(0.0, 0.0), (0.0, 100.0), (100.0, 100.0)], 10.0)
    assert len(pts) == 21 and pts[0] == (0.0, 0.0) and pts[-1] == (100.0, 100.0)
    assert all(math.dist(a, b) == pytest.approx(10.0) for a, b in zip(pts, pts[1:]))
    sm = st.smooth(pts, 60.0)
    assert sm[0] == pts[0] and sm[-1] == pts[-1]
    assert math.dist(sm[10], (0.0, 100.0)) > 5  # the corner cut


def test_route_meets_at_the_middle_of_the_meeting_way():
    r = st.route(_ways(), {"osm_way": 3, "before_m": 1000, "after_m": 1000, "smooth_m": 100}, meet_way=3)
    assert r["meet"] == [0.0, 1250.0]
    assert r["meet_s"] == 1250.0 and r["step_m"] == st.STEP_M
    assert r["points"][int(r["meet_s"] / st.STEP_M)] == pytest.approx([0.0, 1250.0])


def test_the_region_flies_an_aw139_along_the_e20_through_the_mussafah_bridge():
    spec = load_spec(ROOT / "configs" / "scenery" / "abu_dhabi.yaml")
    (t,) = spec.traffic
    assert t["model"] == "aw139" and (MODELS_DIR / "aw139.glb").is_file()
    assert t["speed_kt"] == 80 and t["route"]["osm_way"] == t["meet_osm_way"] == 24151136
    bridge = next(b for b in spec.sources["bridges"] if b["name"] == "Mussafah Bridge")
    assert t["meet_osm_way"] in bridge["ways"]


def test_no_traffic_writes_an_empty_list(tmp_path):
    class Spec:
        traffic = []

    assert st.build_traffic(Spec, tmp_path / "x.pbf", tmp_path, MODELS_DIR) == []
    assert json.loads((tmp_path / "traffic.json").read_text()) == {"format": st.FORMAT, "traffic": []}


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_model_passes_the_meeting_point_when_the_aircraft_does(tmp_path):
    shutil.copy(VIEWER / "trafficCore.js", tmp_path / "trafficCore.js")
    # route east along north 0, 10 km, meeting point at 6 km; the aircraft flies west along
    # north -400 (400 m to the side) at 33 m/s from 9 km past it: closest at t = 9000/33 s
    script = """
const { Schedule, routeAt } = await import("./trafficCore.js");
const points = []; for (let i = 0; i <= 1000; i++) points.push([0, 10 * i]);
const route = { points, step_m: 10, meet_s: 6000, meet: [0, 6000], speed_mps: 41.156, trigger_m: 9000 };
const s = new Schedule(route);
const out = { first: null, at_closest: null, speeds: [], after_seek: null, straight: routeAt(route, 5000) };
let prev = null;
for (let k = 0; k <= 4000; k++) {
  const t = k * 0.1, east = 6000 + 8990 - 33 * t;
  const p = s.update({ t_s: t, north: -400, east, v_north: 0, v_east: -33 });
  if (p && out.first === null) out.first = { t, s: p.s };
  if (Math.abs(east - 6000) < 1.7) out.at_closest = p;
  if (p && prev) out.speeds.push((p.s - prev.s) / 0.1);
  prev = p;
}
out.after_seek = s.update({ t_s: 1, north: -400, east: 14000, v_north: 0, v_east: -33 });
console.log(JSON.stringify(out));
"""
    r = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    # it starts once its start is on the route: 6000 m at 41.156 m/s = 145.8 s before the
    # meeting, when the aircraft is 4811 m from it (t = 126.6 s)
    assert out["first"]["s"] < 10 and out["first"]["t"] == pytest.approx(126.6, abs=0.15)
    assert out["at_closest"]["east"] == pytest.approx(6000, abs=10) and out["at_closest"]["north"] == pytest.approx(0)
    assert all(v == pytest.approx(41.156, rel=1e-6) for v in out["speeds"])  # 80 kt throughout
    assert out["at_closest"]["heading"] == pytest.approx(math.pi / 2)  # east, against the aircraft
    assert out["straight"]["bank"] == pytest.approx(0)
    assert out["after_seek"] is None  # the clock went back: set again, not flying yet
