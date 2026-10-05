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


def test_reset_keeps_the_mixture_command(cruise):
    """Starting the engine in reset resets JSBSim's mixture to full rich; the core re-applies it."""
    from flightsim.core import Controls

    core = JSBSimCore(cruise.aircraft, cruise.dt_s)
    core.reset(cruise.initial_conditions, cruise.loading, Controls(mixture=0.8))
    assert core.controls().mixture == pytest.approx(0.8)
    lean = core.trim()
    assert lean.mixture == pytest.approx(0.8)
    core.step(lean)
    lean_flow = core.engine().fuel_flow_kgps
    rich = JSBSimCore(cruise.aircraft, cruise.dt_s)
    rich.reset(cruise.initial_conditions, cruise.loading)
    rich.step(rich.trim())
    assert 0 < lean_flow < rich.engine().fuel_flow_kgps
    assert 600 < rich.engine().egt_k < 1200


def test_contacts_parked_and_tail_strike_geometry(cruise):
    """Parked: only the three wheels touch. The tail skid touches at a nose-up attitude
    of roughly 10-15 deg from the parked CG height (a geometric check, as JSBSim reports
    no contact for structural points)."""
    import math

    from flightsim.core import Controls
    from flightsim.core.jsbsim_core import _contact_points, point_height_in

    core = JSBSimCore("c172p", cruise.dt_s)
    core.reset(InitialConditions(1.4, 0.0, 0.0))
    for _ in range(240):
        core.step(Controls(throttle=0.0))
    assert [k for k, v in core.contacts().items() if v] == ["NOSE", "LEFT_MAIN", "RIGHT_MAIN"]
    tail = next(p for name, _, p in _contact_points("c172p") if name == "TAIL_SKID")
    f = core._fdm
    cg, agl = (f["inertia/cg-x-in"], f["inertia/cg-y-in"], f["inertia/cg-z-in"]), f["position/h-agl-ft"] * 12
    strike = next(d for d in range(0, 30) if point_height_in(tail, cg, 0.0, math.radians(d), agl) <= 0)
    assert 8 <= strike <= 15
