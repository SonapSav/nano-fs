import math
from pathlib import Path

import numpy as np
import pytest

from flightsim.atmosphere.turbulence import MEDIUM_HIGH_ALTITUDE_SCALE_LENGTH_M as L
from flightsim.atmosphere.turbulence import DrydenTurbulence, to_ned
from flightsim.config import load_raw
from flightsim.core import Controls, InitialConditions, JSBSimCore
from flightsim.datalog import schema as S
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config

ROOT = Path(__file__).parent.parent
CALM = ROOT / "configs" / "envs" / "altitude_heading_hold.yaml"
WINDY = ROOT / "configs" / "envs" / "altitude_heading_hold_wind.yaml"


def test_scale_length_is_mil_f_8785c_dryden_value():
    assert L == pytest.approx(1750 * 0.3048)


def test_dryden_variance_and_autocorrelation():
    """MIL-F-8785C 3.7.1.2: R_u = s^2 exp(-x/L); R_v = R_w = s^2 (1 - x/2L) exp(-x/L)."""
    v_air, dt, sigma = 51.44, 1 / 120, 3.048
    turb = DrydenTurbulence(sigma, L, v_air, dt, np.random.default_rng(0))
    x = np.array([turb.step() for _ in range(120 * 3600 * 2)])
    assert x.std(axis=0) == pytest.approx([sigma] * 3, rel=0.08)
    for frac in (0.5, 1.0):
        k = round(frac * L / v_air / dt)
        r = [float(np.mean(x[:-k, i] * x[k:, i]) / np.mean(x[:, i] ** 2)) for i in range(3)]
        assert r[0] == pytest.approx(math.exp(-frac), abs=0.06)
        assert r[1] == pytest.approx((1 - frac / 2) * math.exp(-frac), abs=0.06)
        assert r[2] == pytest.approx((1 - frac / 2) * math.exp(-frac), abs=0.06)


def test_gust_rotation_to_ned():
    assert to_ned(1.0, 0.0, 0.0, math.pi / 2) == pytest.approx((0.0, 1.0, 0.0))
    assert to_ned(0.0, 1.0, 0.5, 0.0) == pytest.approx((0.0, 1.0, 0.5))


@pytest.mark.parametrize("wind_n, wind_e", [(-7.07, 7.07), (5.0, -3.0), (0.0, -8.0)])
def test_core_trims_crabbed_into_steady_wind(wind_n, wind_e):
    core = JSBSimCore("c172p", 1 / 120)
    ic = InitialConditions(1524.0, 51.44, math.radians(90), wind_north_mps=wind_n, wind_east_mps=wind_e)
    core.reset(ic)
    trim = core.trim()
    s = core.state()
    assert s.tas_mps == pytest.approx(51.44, abs=1e-6)
    assert abs(s.beta_rad) < math.radians(0.01)  # flying the air mass, not slipping
    assert (s.wind_north_mps, s.wind_east_mps) == pytest.approx((wind_n, wind_e), abs=1e-6)
    assert s.v_north_mps == pytest.approx(wind_n, abs=0.05)  # heading east: ground north velocity is the wind's
    for _ in range(1200):
        s2 = core.step(trim)
    assert abs(s2.tas_mps - s.tas_mps) < 0.05 and abs(s2.alt_msl_m - s.alt_msl_m) < 1.0


def test_gusts_do_not_survive_reset():
    core = JSBSimCore("c172p", 1 / 120)
    core.reset(InitialConditions(1524.0, 51.44, 0.0))
    core.set_gust_ned_mps(3.0, -2.0, 1.0)
    s = core.step(core.trim())
    assert s.wind_north_mps == pytest.approx(3.0)
    s = core.reset(InitialConditions(1524.0, 51.44, 0.0))
    assert (s.wind_north_mps, s.wind_east_mps, s.wind_down_mps) == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)


def test_windy_config_extends_calm_config():
    calm, windy = load_raw(CALM), load_raw(WINDY)
    assert "base" not in windy and "wind" in windy
    assert {k: v for k, v in windy.items() if k != "wind"} == calm


def test_overrides_are_part_of_the_hash():
    a = load_env_config(WINDY)
    b = load_env_config(WINDY, {"wind.steady_speed_mps": [0, 15]})
    assert b.wind.steady_speed_mps == (0.0, 15.0)
    assert a.config_hash != b.config_hash
    assert '"steady_speed_mps":[0,15]' in b.config_json


def test_calm_episodes_unchanged_by_wind_support():
    """The calm config draws no wind numbers, so its episodes are what they were in step 3."""
    env = AltitudeHeadingHoldEnv(load_env_config(CALM))
    _, info = env.reset(seed=3)
    assert env.wind["speed_mps"] == 0.0 and env._turbulence is None
    # Values the step 3/4 code produced for seed 3 (recorded in the step 4 stream smoke test).
    assert info["targets"].alt_msl_m == 1300.0381111054849
    assert info["targets"].heading_rad == 1.763649652221349


def test_windy_episode_refly_reproduces_turbulent_states_exactly():
    cfg = load_env_config(WINDY, {"episode_s": 10.0, "wind.turbulence.probability": {"none": 0.0, "light": 0.0, "moderate": 1.0}})
    env = AltitudeHeadingHoldEnv(cfg, record=True)
    env.reset(seed=4)
    assert env.wind["turbulence"] == "moderate"
    rng = np.random.default_rng(1)
    for _ in range(cfg.max_decisions):
        env.step(rng.uniform(-0.1, 0.1, 4).astype(np.float32) + np.array([0, 0.04, 0, 0.4], dtype=np.float32))
    states, controls = (list(x) for x in env.recorded)
    assert len({round(s.wind_down_mps, 6) for s in states}) > 100  # turbulence is acting
    replayed = AltitudeHeadingHoldEnv(cfg).refly(4, controls)
    assert replayed == states


def test_wind_is_recorded_in_logs_as_total_wind():
    assert {"wind_north_mps", "wind_east_mps", "wind_down_mps"} <= set(S.STATE_COLUMNS)
