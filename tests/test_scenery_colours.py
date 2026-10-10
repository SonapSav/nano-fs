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
