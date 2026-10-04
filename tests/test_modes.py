import math

import numpy as np
import pytest

from flightsim.analysis.modes import Oscillation, lateral_modes, longitudinal_modes, oscillation_from_response
from flightsim.core import JSBSimCore


def test_oscillation_from_eigenvalue():
    osc = Oscillation.from_eigenvalue(complex(-0.3, 4.0))
    assert osc.wn_radps == pytest.approx(math.hypot(0.3, 4.0))
    assert osc.zeta == pytest.approx(0.3 / math.hypot(0.3, 4.0))
    assert osc.period_s == pytest.approx(2 * math.pi / 4.0)


def test_oscillation_from_response_recovers_a_damped_sinusoid():
    wn, zeta = 0.25, 0.1
    wd = wn * math.sqrt(1 - zeta**2)
    t = np.arange(0, 120, 1 / 120)
    y = 50.0 + 5.0 * np.exp(-zeta * wn * t) * np.cos(wd * t + 0.3)
    osc = oscillation_from_response(t, y, 50.0)
    assert osc.period_s == pytest.approx(2 * math.pi / wd, rel=1e-3)
    assert osc.zeta == pytest.approx(zeta, rel=1e-2)


@pytest.fixture
def trimmed(cruise):
    core = JSBSimCore(cruise.aircraft, cruise.dt_s)
    core.reset(cruise.initial_conditions, cruise.loading)
    return core, core.trim()


def test_linear_modes_are_classified(trimmed):
    core, _ = trimmed
    lm = core.linearize()
    lon, lat = longitudinal_modes(lm), lateral_modes(lm)
    assert lon.short_period.wn_radps > 10 * lon.phugoid.wn_radps
    assert 0 < lon.phugoid.zeta < lon.short_period.zeta < 1
    assert 0 < lat.dutch_roll.zeta < 1
    assert 0 < lat.roll_time_constant_s < 1


def test_linearize_is_in_si_units(trimmed):
    """Speed state is m/s: d(speed)/dt per unit throttle should be order 1 m/s^2, and the
    gravity term in d(speed)/d(theta) should be about -g."""
    core, _ = trimmed
    lm = core.linearize()
    i_v, i_theta = lm.state_names.index("tas_mps"), lm.state_names.index("theta_rad")
    assert lm.a[i_v, i_theta] == pytest.approx(-9.81, rel=0.02)


def test_stepping_continues_after_linearize(trimmed):
    """Regression: FGLinearization suspends JSBSim integration and resume does not undo it."""
    core, trim = trimmed
    core.linearize()
    before = core.state()
    for _ in range(120):
        after = core.step(trim)
    assert after.t_s == pytest.approx(before.t_s + 1.0)
    assert after.lat_rad != before.lat_rad or after.lon_rad != before.lon_rad
