"""Bridges of a real-world region (flightsim/world/scenery_bridges.py): OSM bridge ways
joined where they continue one another, and the deck profile: ends on the ground roads,
the clearance over water with ramps no steeper than the grade, 1 m over land."""

import numpy as np
import pytest

from flightsim.world.scenery_bridges import DECK_DEPTH_M, GRADE, build_bridges, join, profile


def way(i, nodes, pts, kind="roads", name=None):
    return {"id": i, "nodes": nodes, "pts": pts, "kind": kind, "cls": "trunk", "lanes": 2, "layer": 1, "name": name}


def test_join_orients_and_stops_at_branches():
    ways = [way(1, [10, 11], [[0, 0], [50, 0]]), way(2, [12, 11], [[100, 0], [50, 0]]),  # 2 runs backward
            way(3, [12, 13], [[100, 0], [150, 0]]), way(4, [12, 14], [[100, 0], [100, 50]])]  # 3 and 4 branch at node 12
    chains = sorted([[w["id"] for w in c] for c in join(ways)])
    assert chains == [[1, 2], [3], [4]] or chains == [[2, 1], [3], [4]]
    c = next(c for c in join(ways) if len(c) == 2)
    assert c[0]["pts"][-1] == c[1]["pts"][0]  # continuous


def test_profile_over_water():
    line = np.array([[0.0, 0.0], [1000.0, 0.0]])
    ground = lambda x, z: 0.0 if 300 <= x <= 700 else 3.0  # noqa: E731
    water = lambda x, z: 300 <= x <= 700  # noqa: E731
    pts = profile(line, ground, water, clearance_m=16)["pts"]
    y = {p[0]: p[2] for p in pts}
    assert y[0] == 3.0 and y[1000] == 3.0  # ends on the roads
    assert min(y[x] for x in y if 300 <= x <= 700) >= 16 + DECK_DEPTH_M - 1e-6
    s = sorted(y)
    assert max(abs(y[b] - y[a]) / (b - a) for a, b in zip(s, s[1:]) if 0 < a and b < 1000) <= GRADE + 1e-6


def test_overland_follows_the_ends_and_clears_the_ground():
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    pts = profile(line, lambda x, z: 8.0 if x in (0.0, 100.0) else 2.0, lambda x, z: False)["pts"]  # dry land 2 m up
    assert all(p[2] == pytest.approx(8.0) for p in pts)  # embankment to embankment


def test_landmark_matched_by_way_or_name():
    ways = [way(1, [1, 2], [[0, 0], [100, 0]]), way(2, [3, 4], [[0, 50], [100, 50]], name="Big Bridge")]
    out = build_bridges(ways, lambda x, z: 0.0, lambda x, z: True, [{"name": "A", "ways": [1], "clearance_m": 20}, {"name": "B", "osm_name": "Big Bridge"}])
    assert [b.get("landmark") for b in out] == ["A", "B"]
    assert max(p[2] for p in out[0]["pts"]) > max(p[2] for p in out[1]["pts"])  # its own clearance


def test_water_from_low_ground_and_measured_from_sea_level():
    line = np.array([[0.0, 0.0], [1000.0, 0.0]])
    ground = lambda x, z: 5.9 if x < 200 or x > 800 else 0.2  # noqa: E731
    # WorldCover: water only at the shore strip (5.9 m up), the deck itself "built-up".
    pts = profile(line, ground, lambda x, z: 190 <= x <= 200, clearance_m=31.5, depth_m=3.5)["pts"]
    assert max(p[2] for p in pts) == pytest.approx(35.0)  # from sea level, not from 5.9 m
    assert all(p[4] for p in pts if 300 <= p[0] <= 700)  # low ground counts as water
