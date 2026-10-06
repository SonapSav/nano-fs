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


# --- On the ground: resting start, brakes, nosewheel steering --------------------------------


def _ground(speed_mps=0.0, **controls):
    from flightsim.core import Controls

    core = JSBSimCore("c172p", 1 / 120)
    u = Controls(**controls)
    return core, u, core.reset_on_ground(math.radians(90), controls=u, speed_mps=speed_mps)


def test_reset_on_ground_rests_on_the_wheels_with_the_engine_idling():
    core, u, s = _ground(throttle=0.0, brake=1.0)
    assert all(core.contacts()[w] for w in ("NOSE", "LEFT_MAIN", "RIGHT_MAIN"))
    assert s.engine_rpm < 900 and abs(s.v_down_mps) < 0.01
    for _ in range(240):
        s = core.step(u)
    assert math.hypot(s.v_north_mps, s.v_east_mps) < 0.05  # held by the brakes


def test_rolling_start_keeps_its_speed_and_heading():
    core, _, s = _ground(speed_mps=25.0)
    assert math.hypot(s.v_north_mps, s.v_east_mps) == pytest.approx(25.0, abs=0.01)
    assert s.psi_rad == pytest.approx(math.radians(90), abs=1e-3)
    assert all(core.contacts()[w] for w in ("NOSE", "LEFT_MAIN", "RIGHT_MAIN"))
    assert s.engine_rpm < 900  # the engine idled while the aircraft settled at rest


def test_brakes_stop_the_roll_and_the_pedals_steer_the_nosewheel():
    """The c172p model does not link the nosewheel to the rudder; the core does (rudder + =
    nose left, so it turns the nose left on the ground)."""

    def roll(**controls):
        core, u, s = _ground(speed_mps=15.0, **controls)
        for _ in range(120 * 3):
            s = core.step(u)
        return s

    free, braked = roll(), roll(brake=1.0)
    assert math.hypot(braked.v_north_mps, braked.v_east_mps) < math.hypot(free.v_north_mps, free.v_east_mps) - 5.0
    left = roll(rudder=0.5)
    assert core_heading_change(left) < -math.radians(10)  # nose left


def core_heading_change(s) -> float:
    return math.atan2(math.sin(s.psi_rad - math.radians(90)), math.cos(s.psi_rad - math.radians(90)))


def test_project_aircraft_load_with_their_own_propeller_and_hash():
    """flightsim/aircraft/c172p_tuned is found before JSBSim's aircraft; its propeller comes
    from its Engines folder. Bundled aircraft hashes are unchanged (logs and batch ids use them)."""
    from flightsim.core import Controls, aircraft_hash

    assert aircraft_hash("c172p") == "8da941af014dd4b4f422303beb3799e199cc2b672d6e8ea451008251270c647c"
    assert aircraft_hash("c172p_tuned") != aircraft_hash("c172p")
    rpm = {}
    for ac in ("c172p", "c172p_tuned"):
        core = JSBSimCore(ac, 1 / 120)
        u = Controls(throttle=1.0, brake=1.0)
        core.reset_on_ground(0.0, controls=u)
        for _ in range(120 * 10):
            s = core.step(u)
        rpm[ac] = s.engine_rpm
    assert rpm["c172p"] > 2500 and 2300 < rpm["c172p_tuned"] < 2420  # static RPM, POH 2300-2420
