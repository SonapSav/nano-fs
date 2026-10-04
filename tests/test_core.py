import math
from dataclasses import replace

import pytest

from flightsim.core import InitialConditions, JSBSimCore, TrimError
from flightsim.core.jsbsim_core import LBM_TO_KG


def test_reset_converts_units_at_boundary(cruise):
    core = JSBSimCore(cruise.aircraft, cruise.dt_s)
    s = core.reset(cruise.initial_conditions, cruise.loading)
    assert s.alt_msl_m == pytest.approx(1524.0, abs=1e-6)
    assert s.tas_mps == pytest.approx(51.44, abs=1e-6)
    assert s.psi_rad == pytest.approx(math.radians(90), abs=1e-9)
    # 1500 lb empty + 180 lb pilot + 2 x 100 lb fuel
    assert s.mass_kg == pytest.approx(1880 * LBM_TO_KG, rel=1e-3)


def test_trim_gives_steady_level_flight(cruise):
    core = JSBSimCore(cruise.aircraft, cruise.dt_s)
    core.reset(cruise.initial_conditions, cruise.loading)
    trim = core.trim()
    s = core.state()
    assert 0.0 < trim.throttle < 1.0
    assert abs(s.p_radps) < 1e-6 and abs(s.q_radps) < 1e-6 and abs(s.r_radps) < 1e-6
    # Accelerometer reads about -1 g along body z in level flight.
    assert s.az_mps2 == pytest.approx(-9.80665 * math.cos(s.theta_rad), rel=0.01)


def test_time_advances_by_fixed_steps(cruise):
    core = JSBSimCore(cruise.aircraft, cruise.dt_s)
    core.reset(cruise.initial_conditions, cruise.loading)
    trim = core.trim()
    for _ in range(240):
        s = core.step(trim)
    assert s.t_s == 240 * cruise.dt_s


def test_untrimmable_condition_raises(cruise):
    core = JSBSimCore(cruise.aircraft, cruise.dt_s)
    core.reset(InitialConditions(alt_msl_m=1524.0, tas_mps=20 * 0.5144, heading_rad=0.0))
    with pytest.raises(TrimError):
        core.trim()


def test_elevator_sign_convention(cruise):
    """Positive elevator command pitches the nose down."""
    core = JSBSimCore(cruise.aircraft, cruise.dt_s)
    core.reset(cruise.initial_conditions, cruise.loading)
    trim = core.trim()
    for _ in range(60):
        s = core.step(replace(trim, elevator=0.2))
    assert s.q_radps < 0
