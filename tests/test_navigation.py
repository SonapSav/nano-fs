"""Navigation task (envs/route.py, envs/navigation.py) and the route autopilot
(control/route.py): leg geometry, GPS-style sequencing with turn arcs, seeded routes,
the task's endings, and the autopilot flying a route."""

import math
from pathlib import Path

import numpy as np
import pytest

from flightsim.control.route import load_route_raw, route_gains_from_raw
from flightsim.envs import load_env_config, make_env
from flightsim.envs.evaluate import run_episode
from flightsim.envs.navigation import NavigationEnv
from flightsim.envs.policies import RoutePolicy
from flightsim.envs.route import Navigator, Route, Waypoint, leg_geometry, random_route, turn_anticipation_m, turn_radius_m

ROOT = Path(__file__).parent.parent
NAV = ROOT / "configs" / "envs" / "navigation.yaml"
BANK = math.radians(20)


def square():
    # North 10 km, then east 10 km (right turn 90), then south 10 km (right turn 90).
    return Route(0.0, 0.0, [Waypoint("A", 10000, 0), Waypoint("B", 10000, 10000), Waypoint("C", 0, 10000)])


def test_leg_geometry():
    r = square()
    along, cross, to_go = leg_geometry(r, 0, 4000.0, 30.0)
    assert (along, cross, to_go) == pytest.approx((4000.0, 30.0, 6000.0))  # 30 m east = right of a northbound leg
    assert r.turn_rad(0) == pytest.approx(math.pi / 2) and r.turn_rad(2) == 0.0
    assert turn_anticipation_m(50.0, math.pi / 2, BANK) == pytest.approx(turn_radius_m(50.0, BANK))  # tan(45) = 1


def test_fly_by_sequencing_and_arc():
    r, gs = square(), 50.0
    nav = Navigator(r, BANK)
    radius = turn_radius_m(gs, BANK)
    n = 0.0
    while nav.active == 0:
        n += 5.0
        nav.update(n, 0.0, gs, n / gs)
    assert n == pytest.approx(10000 - radius, abs=5.0)  # the turn starts one radius before A
    q = nav.quantities(n, 0.0, 0.0)
    assert q["turning"] and q["xtk_m"] == pytest.approx(0.0, abs=6.0) and math.degrees(q["dtk_map_rad"]) == pytest.approx(0.0, abs=1.0)
    nav.update(10000.0, radius + 1.0, gs, 999.0)  # past the arc's end on the next leg
    assert nav.arc is None and nav.active == 1


def test_fly_over_and_last_waypoint():
    r = Route(0.0, 0.0, [Waypoint("A", 10000, 0, fly_over=True), Waypoint("B", 10000, 10000)])
    nav = Navigator(r, BANK)
    nav.update(9990.0, 0.0, 50.0, 1.0)
    assert nav.active == 0  # fly-over: not before abeam
    nav.update(10001.0, 0.0, 50.0, 2.0)
    assert nav.active == 1 and nav.arc is None
    nav.update(10000.0, 10001.0, 50.0, 3.0)
    assert nav.done and nav.sequenced_at == [2.0, 3.0]


def test_random_routes_are_seeded():
    a = random_route(np.random.default_rng(5), 0, 0, 1.0, (3, 6), (5000, 15000), (30, 150))
    b = random_route(np.random.default_rng(5), 0, 0, 1.0, (3, 6), (5000, 15000), (30, 150))
    assert a.as_dict() == b.as_dict() and 3 <= a.legs <= 6
    assert all(5000 <= length <= 15000 for length in a.lengths)
    assert all(30 <= abs(math.degrees(a.turn_rad(i))) <= 150 for i in range(a.legs - 1))


def test_task_start_and_off_course():
    env = make_env(load_env_config(NAV, {"episode_s": 20.0}))
    assert isinstance(env, NavigationEnv)
    obs, info = env.reset(seed=3)
    assert obs.shape == env.observation_space.shape and len(env.obs_names) == len(obs)
    assert info["nav"]["leg"] == 0 and info["nav"]["xtk_m"] == pytest.approx(0.0, abs=1e-6)
    assert info["targets"].alt_msl_m == pytest.approx(3000 * 0.3048)
    assert len(info["route"]["waypoints"]) == env.route.legs
    # Same seed, same route.
    assert make_env(load_env_config(NAV, {"episode_s": 20.0})).reset(seed=3)[1]["route"] == info["route"]
    # A tight cross-track limit ends the flight off course once the aircraft drifts.
    tight = make_env(load_env_config(NAV, {"episode_s": 60.0, "route.max_xtk_nm": 0.001, "route.randomize_heading_deg": 30}))
    tight.reset(seed=4)
    reasons = set()
    for _ in range(1200):
        _, _, terminated, truncated, info = tight.step(np.zeros(4, dtype=np.float32))
        if terminated or truncated:
            reasons.add(info["termination_reason"])
            break
    assert reasons == {"off_course"}


def test_route_autopilot_flies_a_named_route():
    over = {"episode_s": 900.0, "route.random": None, "route.waypoints": [
        {"name": "NORTH", "north_m": 6000, "east_m": 0}, {"name": "EAST", "north_m": 6000, "east_m": 6000},
        {"name": "HOME", "lat_deg": 0.0, "lon_deg": 0.0}]}  # fmt: skip
    env = make_env(load_env_config(NAV, over))
    policy = RoutePolicy(route_gains_from_raw(load_route_raw(ROOT / "configs" / "route_autopilot.yaml")), env.cfg.control_rate_hz)
    run_episode(env, policy, seed=0)
    s = env.route_summary()
    assert s["completed"] and s["legs_done"] == 3
    assert s["xtk_max_m"] < 60 and s["alt_rms_m"] < 5
    assert [w["name"] for w in env.route_info()["waypoints"]] == ["NORTH", "EAST", "HOME"]


def test_viewer_navigation_port_agrees():
    """nav.js gives the same legs, arcs and quantities as envs/route.py for the same samples."""
    import json
    import shutil
    import subprocess

    from flightsim.envs.navigation import turn_speed_mps

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    over = {"episode_s": 600.0}
    env = make_env(load_env_config(NAV, over), record=True)
    policy = RoutePolicy(route_gains_from_raw(load_route_raw(ROOT / "configs" / "route_autopilot.yaml")), env.cfg.control_rate_hz)
    run_episode(env, policy, seed=7)
    states, _ = env.recorded
    geo, route = env.cfg.geodesy, env.route
    samples = []
    for s in states[::4]:  # 30 Hz, as the viewer sees frames
        n, e = geo.to_map(s.lat_rad, s.lon_rad)
        track = math.atan2(s.v_east_mps, s.v_north_mps) - geo.convergence_rad(s.lat_rad, s.lon_rad)
        samples.append([n, e, math.hypot(s.v_north_mps, s.v_east_mps), turn_speed_mps(s), track,
                        s.v_north_mps, s.v_east_mps, s.tas_mps, s.psi_rad])  # fmt: skip
    py = Navigator(route, env.cfg.route.turn_bank_rad)
    expected = []
    for n, e, gs, ts, track, *_ in samples:
        py.update(n, e, gs, 0.0, ts)
        q = py.quantities(n, e, track)
        expected.append([q["leg"], q["turning"], q["dtk_map_rad"], q["xtk_m"], py.done])
    viewer = Path(__file__).parent.parent / "flightsim" / "viewer"
    script = f"""
    const nav = await import({json.dumps((viewer / "nav.js").as_uri())});
    const route = new nav.Route({json.dumps(env.route_info())});
    const nv = new nav.Navigator(route, {env.cfg.route.turn_bank_rad});
    const out = [], ts = [];
    for (const [n, e, gs, tsp, track, vn, ve, tas, psi] of {json.dumps(samples)}) {{
      ts.push(nav.turnSpeed({{ v_north_mps: vn, v_east_mps: ve, tas_mps: tas, psi_rad: psi }}) - tsp);
      nv.update(n, e, gs, tsp);
      const q = nv.quantities(n, e, track);
      out.push([q.leg, q.turning, q.dtkMap, q.xtk, nv.done]);
    }}
    console.log(JSON.stringify({{ out, maxTs: Math.max(...ts.map(Math.abs)) }}));
    """
    got = json.loads(subprocess.run([node, "--input-type=module"], input=script, capture_output=True, text=True, check=True).stdout)
    assert got["maxTs"] < 1e-9
    assert [g[:2] + [g[4]] for g in got["out"]] == [e[:2] + [e[4]] for e in expected]  # same legs, turns, completion
    assert np.allclose([g[2:4] for g in got["out"]], [e[2:4] for e in expected], atol=1e-6)
    assert max(e[0] for e in expected) >= 2 and any(e[1] for e in expected)  # several legs and turn arcs covered
