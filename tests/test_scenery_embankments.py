"""Approach banks at landmark bridges' ends (flightsim/world/scenery_embankments.py): the
bank's top along the road from the surface model, and the height posts raised under it."""

import numpy as np
import pytest

from flightsim.world.scenery_embankments import STEP_M, bank_profile, raise_posts


def test_bank_runs_while_the_surface_stands_above_the_ground_then_comes_down():
    s = np.arange(0.0, 300.0 + 1, STEP_M)
    bare = np.full(len(s), 8.0)
    dsm = np.where(s <= 60, 17.0, 8.5)  # a 17 m bank for 60 m on 8 m ground
    dsm[3] = 25.0  # a sign gantry: one sample, smoothed away
    top = bank_profile(s, dsm, bare, raise_min_m=2, smooth_m=30, grade=0.05)
    assert top[:7] == pytest.approx(17.0)  # the bank's run
    assert top[7] == pytest.approx(17.0 - 0.05 * 10)  # then down at the grade
    assert np.isnan(top[-1])  # until it meets the ground
    assert np.all(np.diff(top[6:][~np.isnan(top[6:])]) < 0)


def test_no_bank_at_the_end_no_change():
    s = np.arange(0.0, 100.0 + 1, STEP_M)
    assert bank_profile(s, np.full(len(s), 9.0), np.full(len(s), 8.0), 2, 30, 0.05) is None


def test_posts_raised_along_the_road_square_at_the_bridge_end():
    post = 31.25
    h = np.zeros((40, 40))
    line = np.column_stack([np.arange(300.0, 801, STEP_M), np.full(51, 600.0)])  # east from x = 300, z = 600
    top = np.full(51, 15.0)
    n = raise_posts(h, 0.0, post, line, top, half_width_m=12, side_slope=0.5)
    assert n > 0
    row = int(round(600 / post))
    on = h[row, int(np.ceil(300 / post)) : int(800 / post)]
    assert on == pytest.approx(15.0)  # the crest on the road
    assert h[row, int(300 / post)] == pytest.approx(15.0)  # one post under the deck's end (the surface reaches the top at the end)
    assert h[row, int(300 / post) - 1] == 0  # further behind the bridge end: untouched (the bridge is there)
    assert h[row + 1, 16] == pytest.approx(15.0)  # within the crest (half width + one post: 43 m)
    assert 0 < h[row + 2, 16] < 15.0  # the side falling (56 m off the road)
    assert h[row + 4, 16] == 0  # beyond the side: untouched
    before = h.copy()
    raise_posts(h, 0.0, post, line, top - 5, 12, 0.5)
    assert np.array_equal(h, before)  # only ever raised
