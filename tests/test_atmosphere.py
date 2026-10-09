"""Non-standard days (flightsim/core Atmosphere, JSBSim's atmosphere/delta-T and
atmosphere/P-sl-psf): the density helper matches JSBSim; a config's `atmosphere` reaches
the core; a hot day lengthens the takeoff and cuts the climb about as the C172P POH says
(Figure 5-5 takeoff distance and Figure 5-6 maximum rate of climb, 2400 lb, sea level;
docs/REFERENCES.md), compared as ratios to the standard day so the model's known
standard-day deviations (docs/VALIDATION_c172p_tuned.md) cancel; and prevailing winds."""

import math

import numpy as np
import pytest

from flightsim.analysis.maneuvers import max_climb_rate_mps, takeoff_roll
from flightsim.analysis.validation import _loading
from flightsim.core import Atmosphere, InitialConditions, JSBSimCore

KT = 1852.0 / 3600.0
AIRCRAFT = "c172p_tuned"
SUMMER = Atmosphere(temperature_offset_k=27.5, sea_level_pressure_pa=99600.0)


@pytest.mark.parametrize("alt_m", [5.0, 500.0, 1500.0, 3000.0])
@pytest.mark.parametrize("atm", [Atmosphere(), SUMMER, Atmosphere(-20.0, 102500.0)])
def test_density_ratio_matches_jsbsim(alt_m, atm):
    core = JSBSimCore(AIRCRAFT, 1 / 120)
    core.set_atmosphere(atm)
    s = core.reset(InitialConditions(alt_msl_m=alt_m, tas_mps=50.0, heading_rad=0.0))
    assert s.air_density_kgpm3 / 1.225 == pytest.approx(atm.density_ratio(alt_m), rel=2e-3)


def test_config_atmosphere_reaches_the_core():
    from flightsim.envs import make_env
    from flightsim.envs.config import load_env_config

    hot = load_env_config("configs/envs/altitude_heading_hold.yaml", {"atmosphere.sea_level_temperature_c": 42.5, "atmosphere.sea_level_pressure_hpa": 996})
    std = load_env_config("configs/envs/altitude_heading_hold.yaml")
    assert hot.atmosphere == SUMMER and std.atmosphere is None and hot.config_hash != std.config_hash
    e_hot, e_std = make_env(hot), make_env(std)
    e_hot.reset(seed=1)
    e_std.reset(seed=1)
    a = e_hot._state.alt_msl_m
    assert e_hot._state.air_density_kgpm3 == pytest.approx(1.225 * SUMMER.density_ratio(a), rel=2e-3)
    assert e_hot._state.air_density_kgpm3 < 0.92 * e_std._state.air_density_kgpm3


# POH Figure 5-5 (2400 lb, short field, sea level): ground roll 795, 860, 925, 995, 1065 ft
# at 0, 10, 20, 30, 40 C; Figure 5-6: maximum rate of climb 745, 685, 625 ft/min at 0, 20,
# 40 C (76 KIAS). Standard day (15 C) by linear interpolation: 892.5 ft, 700 ft/min.
POH_ROLL_RATIO = 1065 / 892.5  # 1.193
POH_CLIMB_RATIO = 625 / 700  # 0.893
HOT = Atmosphere(temperature_offset_k=25.0)  # 40 C at sea level, standard pressure


@pytest.fixture(scope="module")
def loading():
    return _loading(AIRCRAFT, 2400, 43.4, 370)  # as configs/validation/c172p.yaml ground checks


def test_hot_day_takeoff_roll(loading):
    run = lambda atm: takeoff_roll(AIRCRAFT, loading, 10 / 30, -0.3, math.radians(9), 10, 51 * KT, atmosphere=atm)  # noqa: E731
    ratio = run(HOT).ground_roll_m / run(None).ground_roll_m
    # Known deviation (2026-10-09): the model is more sensitive to heat than the POH (+27.5 %
    # against +19.3 %); tolerance 0.10 (project choice).
    assert ratio == pytest.approx(POH_ROLL_RATIO, abs=0.10)
    assert ratio > 1.1


def test_hot_day_climb(loading):
    ratio = max_climb_rate_mps(AIRCRAFT, loading, 76 * KT, atmosphere=HOT) / max_climb_rate_mps(AIRCRAFT, loading, 76 * KT)
    # Model -13.2 % against the POH's -10.7 % (2026-10-09); tolerance 0.05 (project choice).
    assert ratio == pytest.approx(POH_CLIMB_RATIO, abs=0.05)


def test_prevailing_wind_range():
    from flightsim.envs.config import load_env_config
    from flightsim.envs.runway import draw_low_altitude_wind

    cfg = {"u20_kt": [5, 20], "max_crosswind_kt": 15, "max_tailwind_kt": 0, "turbulence": False, "from_deg": [290, 340]}
    rng = np.random.default_rng(3)
    dirs = [draw_low_altitude_wind(cfg, math.radians(307.95), rng)["from_deg"] for _ in range(300)]
    assert 290 <= min(dirs) and max(dirs) <= 340
    from flightsim.envs import make_env

    env = make_env(load_env_config("configs/envs/abu_dhabi_manual_wind.yaml"))
    seen = []
    for seed in range(20):
        env.reset(seed=seed)
        seen.append(env.wind["from_deg"])
    assert all(290 <= d <= 340 for d in seen)
