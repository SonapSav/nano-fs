import math
from pathlib import Path

import numpy as np
import pytest

from flightsim.config import load_raw
from flightsim.control.lqr import GainSchedule, LQRAutopilot, lqr_config_from_raw
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import LQRPolicy

ROOT = Path(__file__).parent.parent
CALM = ROOT / "configs" / "envs" / "altitude_heading_hold.yaml"
LQR = ROOT / "configs" / "lqr.yaml"


@pytest.fixture(scope="module")
def env_cfg():
    return load_env_config(CALM)


@pytest.fixture(scope="module")
def schedule(env_cfg, tmp_path_factory):
    # The real design (16 trims + linearizations, ~14 s), cached in a temporary directory.
    return GainSchedule.cached(env_cfg.aircraft, env_cfg.loading, load_raw(LQR), 1 / env_cfg.control_rate_hz, tmp_path_factory.mktemp("cache"))


def test_every_design_point_is_closed_loop_stable(schedule):
    for row in schedule.points:
        for p in row:
            assert np.abs(p.closed_loop_eigs).max() < 1.0


def test_cached_schedule_round_trips(env_cfg, schedule, tmp_path_factory):
    cache = tmp_path_factory.mktemp("cache2")
    raw = load_raw(LQR)
    first = GainSchedule.cached(env_cfg.aircraft, env_cfg.loading, raw, 1 / env_cfg.control_rate_hz, cache)
    assert len(list(cache.iterdir())) == 1
    second = GainSchedule.cached(env_cfg.aircraft, env_cfg.loading, raw, 1 / env_cfg.control_rate_hz, cache)
    for r1, r2 in zip(first.points, second.points):
        for a, b in zip(r1, r2):
            assert np.array_equal(a.k, b.k) and np.array_equal(a.u_trim, b.u_trim)
    np.testing.assert_allclose(first.points[1][1].k, schedule.points[1][1].k)


def test_lookup_interpolates_and_clamps(schedule):
    cfg = schedule.cfg
    k_grid = schedule.points[1][2].k
    k, _, _ = schedule.lookup(cfg.alt_grid_m[1], cfg.tas_grid_mps[2])
    np.testing.assert_allclose(k, k_grid)
    k_mid, _, _ = schedule.lookup((cfg.alt_grid_m[1] + cfg.alt_grid_m[2]) / 2, cfg.tas_grid_mps[2])
    np.testing.assert_allclose(k_mid, (schedule.points[1][2].k + schedule.points[2][2].k) / 2)
    k_far, _, _ = schedule.lookup(1e6, 1e6)
    np.testing.assert_allclose(k_far, schedule.points[-1][-1].k)


def test_reference_profile_respects_limits_and_arrives_exactly(schedule):
    cfg = schedule.cfg
    ap = LQRAutopilot.__new__(LQRAutopilot)
    ap.dt_s = 0.05
    rate, pos, target, rates = 0.0, 0.0, 100.0, []
    for _ in range(2000):
        step, rate = ap._profile(target - pos, rate, cfg.max_climb_mps, cfg.max_climb_accel_mps2)
        pos += step
        rates.append(rate)
        if pos == target:
            break
    assert pos == target  # exact arrival, never past the target
    assert max(rates) <= cfg.max_climb_mps + 1e-12
    adt = cfg.max_climb_accel_mps2 * ap.dt_s
    jumps = np.abs(np.diff([0.0, *rates]))
    # Rate changes stay within the acceleration limit while moving; the final stop is a
    # partial step (the distance is rarely an exact sum of braking steps), bounded by 2 a dt.
    assert jumps[:-1].max() <= adt * 1.0001
    assert jumps[-1] <= 2 * adt


def test_lqr_captures_targets_within_the_bank_limit(env_cfg, schedule):
    env = AltitudeHeadingHoldEnv(env_cfg)
    policy = LQRPolicy(schedule, env_cfg.control_rate_hz)
    for seed in range(3):
        m = run_episode(env, policy, seed)
        assert m.termination_reason is None
        assert m.alt_final_abs_m < 1.0 and m.heading_final_abs_deg < 0.5
        assert m.max_bank_deg < 25.0  # PID autopilot's limit
        assert 0.8 < m.min_load_factor and m.max_load_factor < 1.3


def test_lqr_episodes_are_deterministic(env_cfg, schedule):
    env = AltitudeHeadingHoldEnv(env_cfg)
    policy = LQRPolicy(schedule, env_cfg.control_rate_hz)
    assert run_episode(env, policy, 7) == run_episode(env, policy, 7)


def test_config_rejects_missing_weights():
    raw = load_raw(LQR)
    del raw["weights"]["states"]["phi_rad"]
    with pytest.raises(KeyError):
        lqr_config_from_raw(raw)


def test_inertial_alpha_beta_equal_aero_values_without_wind():
    from dataclasses import replace as dc_replace

    from flightsim.control.lqr import inertial_alpha_beta
    from flightsim.core import InitialConditions, JSBSimCore

    core = JSBSimCore("c172p", 1 / 120)
    core.reset(InitialConditions(1500.0, 52.0, 1.2))
    trim = core.trim()
    for _ in range(600):  # rolling, yawing, slipping
        s = core.step(dc_replace(trim, aileron=trim.aileron + 0.05, rudder=0.05))
    alpha, beta = inertial_alpha_beta(s)
    assert alpha == pytest.approx(s.alpha_rad, abs=1e-9) and beta == pytest.approx(s.beta_rad, abs=1e-9)


def _autopilot(schedule, env_cfg, seed=0):
    env = AltitudeHeadingHoldEnv(env_cfg)
    _, info = env.reset(seed=seed)
    return LQRAutopilot(schedule, info["trim"], info["trim_state"], info["targets"], 0.05), info


def test_protection_has_hysteresis(env_cfg, schedule):
    from dataclasses import replace as dc_replace

    ap, info = _autopilot(schedule, env_cfg)
    s, target = info["trim_state"], info["targets"].tas_mps
    margin = schedule.cfg.protection_speed_margin_mps
    states = [dc_replace(s, tas_mps=target - d) for d in (margin + 0.5, margin * 0.75, margin * 0.4)]
    # engage below target - margin; stay engaged at 0.75 margin; release only above target - margin/2
    assert [ap._update_protection(x) for x in states] == [True, True, False]
    assert ap._update_protection(states[1]) is False  # not re-engaged inside the hysteresis band


def test_protection_never_demands_climb_and_releases_bumplessly(env_cfg, schedule):
    from dataclasses import replace as dc_replace

    ap, info = _autopilot(schedule, env_cfg)
    s = info["trim_state"]
    ap.targets = dc_replace(ap.targets, alt_msl_m=s.alt_msl_m + 100.0)  # target far above
    slow = dc_replace(s, tas_mps=ap.targets.tas_mps - 10.0, alt_msl_m=s.alt_msl_m - 20.0)
    for _ in range(20):
        ap(slow)
    assert ap.ref_alt_m <= slow.alt_msl_m and ap.ref_climb_mps <= 0.0  # follows the aircraft down
    recovered = dc_replace(slow, tas_mps=ap.targets.tas_mps)
    ap(recovered)
    assert ap.ref_alt_m - slow.alt_msl_m <= schedule.cfg.max_climb_mps * 0.05 + 1e-9  # climbs on from here
