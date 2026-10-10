"""High-resolution imagery build (flightsim/world/scenery_hires.py), on synthetic tiles:
pass names, chunk ranges, the colour curve, and the compositing (no gap where tiles meet,
the first pass wins where they overlap, no data falls through to the next)."""

from pathlib import Path

import numpy as np
import pytest

from flightsim.world import scenery_hires as h


def _tiles(rows):
    """A _Tiles of (rank, west, north) rows, without files."""
    t = object.__new__(h._Tiles)
    t.rank = np.array([r[0] for r in rows])
    t.e0 = np.array([r[1] for r in rows], float)
    t.n0 = np.array([r[2] for r in rows], float)
    t.path = [Path(f"t{i}") for i in range(len(rows))]
    t.passes = ["a", "b"]
    return t


def test_pass_id():
    key = "data/tif/zone=40N/region=220595_2686506/date=2022-10-21/20221021_110642_SN22_40N_220595_2686506_VISUAL.tif"
    assert h.pass_id(key) == "20221021_SN22"


def test_chunk_range_whole_level1_chunks():
    r = h.chunk_range(25000)
    k = round(h.LEVELS[1] / h.LEVELS[0])
    assert r.start % k == 0 and r.stop % k == 0
    assert r.start * h.chunk_m(0) <= -25000 and r.stop * h.chunk_m(0) >= 25000


def test_quantile_lut_matches_distribution():
    rng = np.random.default_rng(0)
    src = rng.uniform(50, 150, 5000)
    lut = h._quantile_lut(src, 2 * src - 40)
    assert np.all(np.diff(lut) >= 0)  # monotone
    assert lut[100] == pytest.approx(160, abs=3)
    assert lut[0] == 0 and lut[255] == 255  # extrapolated linearly, clipped


def _grid(e0, n0, step, size):
    k = (np.arange(size) + 0.5) * step
    e, n = np.meshgrid(e0 + k, n0 - k)  # rows going south (north decreasing), columns east
    return e, n


def test_composite_tiles_meet_without_gap_and_priority():
    # Two pass-a tiles side by side, a pass-b tile over the middle; the left a tile has no
    # data in its top-right quarter, which the b tile covers.
    tiles = _tiles([(0, 1000, 5000), (0, 1384, 5000), (1, 1192, 5000)])
    data = [np.full((3, 384, 384), v, np.float32) for v in (10, 20, 30)]
    valid = [np.ones((384, 384), bool) for _ in range(3)]
    valid[0][:192, 192:] = False
    e, n = _grid(1000, 5000, 1.0, 768)
    rgb, won = h._composite(tiles, np.arange(3), e, n, lambda i: (data[i], valid[i]))
    assert (won[:384] >= 0).all()  # no gap anywhere, also where the a tiles meet (x = 384)
    assert (won[:188, 196:384] == 2).all() and (rgb[0][:188, 196:384] == 30).all()  # b where a has no data
    assert (won[200:384, :384] == 0).all() and (won[:384, 384:] == 1).all()  # a first where it has data
    assert (won[384:] == -1).all()  # south of every tile: none
    assert np.isin(rgb[0][:384], (10, 20, 30)).all()  # bilinear inside a tile: its own values only


def test_window_holds_every_point_of_a_tile():
    tiles = _tiles([(0, 1100.5, 4900.25)])
    e, n = _grid(1000, 5000, 4.0, 200)
    # Rotate the grid slightly against UTM (grid convergence), as the real map is.
    a = np.radians(0.6)
    ce, cn = e.mean(), n.mean()
    e, n = ce + (e - ce) * np.cos(a) - (n - cn) * np.sin(a), cn + (e - ce) * np.sin(a) + (n - cn) * np.cos(a)
    inside = (e >= 1100.5) & (e < 1484.5) & (n <= 4900.25) & (n > 4516.25)
    rs, cs = h._window(tiles, 0, e, n)
    win = np.zeros_like(inside)
    win[rs, cs] = True
    assert inside.any() and not (inside & ~win).any()
    assert win.sum() < 3 * inside.sum()  # and not much more
