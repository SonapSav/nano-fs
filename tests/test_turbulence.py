"""Low-altitude wind model (MIL-F-8785C 3.7.3): wind shear and height-dependent Dryden
turbulence for approach and landing. The medium/high altitude model is tested in test_wind.py."""

import math

import numpy as np
import pytest

from flightsim.atmosphere.turbulence import LowAltitudeTurbulence, low_altitude_parameters, wind_at_height_mps

FT = 0.3048


def test_wind_shear_profile():
    u20 = 10.0
    assert wind_at_height_mps(u20, 20 * FT) == pytest.approx(u20)
    assert wind_at_height_mps(u20, 0.1 * FT) == 0.0
    # ln(200 / 0.15) / ln(20 / 0.15) = 1.470: the wind at 200 ft is ~47% stronger than at 20 ft.
    assert wind_at_height_mps(u20, 200 * FT) == pytest.approx(u20 * 1.4704, rel=1e-3)
    assert wind_at_height_mps(u20, 5 * FT) < u20


def test_low_altitude_parameters():
    u20 = 15 * 1852 / 3600  # light turbulence
    (su, sv, sw), (lu, lv, lw) = low_altitude_parameters(u20, 1000 * FT)
    assert sw == pytest.approx(0.1 * u20) and su == pytest.approx(sw) and lw == pytest.approx(1000 * FT) and lu == pytest.approx(1000 * FT)
    (su, sv, sw), (lu, lv, lw) = low_altitude_parameters(u20, 100 * FT)
    assert su / sw == pytest.approx(1 / (0.177 + 0.0823) ** 0.4) and lw == pytest.approx(100 * FT)
    assert lu == pytest.approx(100 * FT / (0.177 + 0.0823) ** 1.2) and su == sv and lu == lv
    # Blends into the isotropic medium/high altitude model (sigma_high, 1750 ft) by 2000 ft.
    (su, sv, sw), (lu, lv, lw) = low_altitude_parameters(u20, 2000 * FT, sigma_high_mps=1.5)
    assert su == pytest.approx(1.5) and sw == pytest.approx(1.5) and lu == pytest.approx(1750 * FT)
    # Clamped below 10 ft.
    assert low_altitude_parameters(u20, 1 * FT) == low_altitude_parameters(u20, 10 * FT)


def test_low_altitude_turbulence_statistics():
    u20, height = 15 * 1852 / 3600, 300 * FT
    (su, sv, sw), (lu, lv, lw) = low_altitude_parameters(u20, height)
    turb = LowAltitudeTurbulence(u20, 33.0, 1 / 120, np.random.default_rng(3))
    x = np.array([turb.step(height) for _ in range(400_000)])[2000:]
    for i, sigma in enumerate((su, sv, sw)):
        assert x[:, i].std() == pytest.approx(sigma, rel=0.06)
    # Along-wind component: first-order, correlation exp(-1) at a lag of L / V.
    lag = round(lu / 33.0 * 120)
    u = x[:, 0] - x[:, 0].mean()
    assert np.mean(u[:-lag] * u[lag:]) / u.var() == pytest.approx(math.exp(-1), abs=0.06)


def test_low_altitude_turbulence_is_seeded():
    a = LowAltitudeTurbulence(8.0, 33.0, 1 / 120, np.random.default_rng(7))
    b = LowAltitudeTurbulence(8.0, 33.0, 1 / 120, np.random.default_rng(7))
    assert [a.step(50.0) for _ in range(50)] == [b.step(50.0) for _ in range(50)]
