"""Building colours from the imagery (flightsim/world/scenery_colours.py), on a synthetic
picture: the median inside each footprint (a few odd pixels do not move it), towers left
out, tiny footprints take the pixel under their centre."""

import numpy as np
import pytest

pytest.importorskip("rasterio")

from flightsim.world.scenery_colours import TOWER_M, roof_colours  # noqa: E402


def test_roof_colours():
    img = np.zeros((3, 200, 200), np.uint8)
    img[:, 20:60, 20:60] = np.array([200, 100, 50], np.uint8)[:, None, None]  # a roof
    img[:, 20:24, 20:24] = 255  # a few odd pixels on it (an air conditioner)
    img[:, 100:140, 100:140] = 90  # a tower's footprint
    img[:, 170, 170] = np.array([10, 20, 30], np.uint8)
    x0, z0 = 1000.0, 2000.0
    sq = lambda a, b: [x0 + a, z0 + a, x0 + b, z0 + a, x0 + b, z0 + b, x0 + a, z0 + b]  # noqa: E731
    buildings = [[8.0, "osm", sq(20, 60)], [TOWER_M + 10, "osm", sq(100, 140)], [3.0, "osm", [x0 + 170, z0 + 170, x0 + 171, z0 + 170, x0 + 170.5, z0 + 171]]]
    c = roof_colours(buildings, img, x0, z0)
    assert c[0] == (200 << 16) | (100 << 8) | 50
    assert c[1] is None
    assert c[2] == (10 << 16) | (20 << 8) | 30


def test_road_samples_and_water():
    from flightsim.world.scenery_colours import _along, _samples

    pts = _along([1000.0, 2000.0, 1010.0, 2000.0])  # 10 m east, every 2 m
    assert len(pts) == 6 and pts[-1].tolist() == [1010.0, 2000.0]
    img = np.zeros((3, 50, 50), np.uint8)
    img[:, :, :] = np.array([70, 66, 58], np.uint8)[:, None, None]  # asphalt
    mask = np.zeros((50, 50), bool)
    mask[:, :8] = True  # only the first 8 m are 1 m imagery
    rows = _samples(img, mask, pts, 1000.0, 2000.0)
    assert len(rows) == 4 and (rows == [70, 66, 58]).all()


def test_apron_points_inside_only():
    from flightsim.world.scenery_colours import _apron_points

    L = [0, 0, 40, 0, 40, 20, 20, 20, 20, 40, 0, 40]  # an L shape
    p = _apron_points(L, 4.0)
    assert len(p) and not ((p[:, 0] > 20) & (p[:, 1] > 20)).any()  # nothing in the missing corner
    assert ((p[:, 0] > 20) & (p[:, 1] < 20)).any() and ((p[:, 0] < 20) & (p[:, 1] > 20)).any()
