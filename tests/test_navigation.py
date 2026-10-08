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
