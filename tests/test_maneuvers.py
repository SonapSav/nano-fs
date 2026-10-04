import pytest

from flightsim.analysis.maneuvers import loading_for
from flightsim.core import Controls, InitialConditions, JSBSimCore


def test_loading_for_hits_mass_and_cg():
    loading = loading_for("c172p", 1088.6, 1.10, 100.0)
    core = JSBSimCore("c172p", 1 / 120)
    core.reset(InitialConditions(1524.0, 50.0, 0.0), loading)
    mp = core.mass_properties()
    assert mp.mass_kg == pytest.approx(1088.6, abs=0.01)
    assert mp.cg_x_m == pytest.approx(1.10, abs=1e-6)


def test_loading_for_rejects_unreachable_cg():
    with pytest.raises(ValueError, match="not reachable"):
        loading_for("c172p", 1088.6, 0.5, 100.0)


def test_reset_applies_controls_so_runs_do_not_leak(cruise):
    """Regression: JSBSim keeps commands across run_ic, so a reused core could trim from
    the previous run's controls."""
    fresh = JSBSimCore(cruise.aircraft, cruise.dt_s)
    fresh.reset(cruise.initial_conditions, cruise.loading)
    expected = fresh.trim()

    reused = JSBSimCore(cruise.aircraft, cruise.dt_s)
    reused.reset(cruise.initial_conditions, cruise.loading, Controls(flaps=1.0, throttle=0.3))
    reused.trim()
    reused.reset(cruise.initial_conditions, cruise.loading)
    assert reused.trim() == expected
