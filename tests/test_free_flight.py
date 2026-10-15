"""Free flight (envs/free.py): from rest on a stand, no task, ends only on a crash, in water
or at the time cap; never saved, and a live free flight forgets what it has streamed."""

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from flightsim.envs import load_env_config, make_env
from flightsim.envs.free import FreeFlightEnv
from flightsim.stream.sources import FORGET_AFTER, ManualSource

ROOT = Path(__file__).parent.parent
CONFIGS = ROOT / "configs" / "envs"
SCENERY = ROOT / "data" / "scenery" / "abu_dhabi" / "manifest.json"


def _act(env, throttle=0.0, brake=0.0, elevator=0.0):
    a = {"elevator": elevator, "aileron": 0.0, "rudder": 0.0, "throttle": 2 * throttle - 1, "flaps": -1.0, "pitch_trim": 0.0, "brake": 2 * brake - 1}
    return np.array([a[n] for n in env.action_names], dtype=np.float32)


def _map(env, s):
    return env.cfg.geodesy.to_map(s.lat_rad, s.lon_rad)


@pytest.mark.parametrize("name", ["free", "free_wind"])
def test_starts_parked_on_its_stand_with_the_parking_brake_set(name):
    env = make_env(load_env_config(CONFIGS / f"{name}.yaml"))
    assert isinstance(env, FreeFlightEnv)
    _, info = env.reset(seed=4)
    s0 = info["state"]
    assert _map(env, s0) == pytest.approx((185.0, 35.0), abs=0.5)  # the procedural apron's empty stand
    assert math.degrees(s0.psi_rad) % 360 == pytest.approx(180.0, abs=1.0)
    assert math.hypot(s0.v_north_mps, s0.v_east_mps) < 0.1 and info["free"]["parking_brake"]
    for _ in range(20 * 30):  # 30 s at 20 % power, B not pressed: held
        _, reward, term, trunc, info = env.step(_act(env, throttle=0.2))
        assert reward == 0.0 and not term and not trunc
    assert math.dist(_map(env, info["state"]), _map(env, s0)) < 3.0  # gusts may shift it a little
    env.step(_act(env, brake=1.0))  # B: released
    for _ in range(20 * 20):
        _, _, term, _, info = env.step(_act(env, throttle=0.3))
    assert not info["free"]["parking_brake"] and not term
    assert math.dist(_map(env, info["state"]), _map(env, s0)) > 30  # taxiing


def test_ends_only_at_the_time_cap_when_nothing_happens():
    cfg = load_env_config(CONFIGS / "free.yaml", {"episode_s": 3.0})
    assert load_env_config(CONFIGS / "free.yaml").episode_s == 3600  # 60 minutes
    env = make_env(cfg)
    env.reset(seed=0)
    for k in range(60):
        _, _, term, trunc, info = env.step(_act(env))
        if term or trunc:
            break
    assert (k, term, trunc, info["termination_reason"]) == (59, False, True, None)


class _Contacts:
    def __init__(self, points):
        self.points = points

    def contacts(self):
        return {p: p in self.points for p in ("NOSE", "LEFT_MAIN", "RIGHT_MAIN", "TAIL_SKID", "LEFT_TIP", "RIGHT_TIP", "NOSE_SKID")}


def test_crashes_are_strikes_and_hard_landings_but_a_landing_is_not():
    env = make_env(load_env_config(CONFIGS / "free.yaml"))
    _, info = env.reset(seed=0)
    s = info["state"]
    air = replace(s, alt_agl_m=50.0, v_down_mps=2.0)
    env._core = _Contacts(set())
    env._track(s, air)  # flying
    env._core = _Contacts({"LEFT_MAIN", "RIGHT_MAIN"})
    env._track(air, replace(s, v_down_mps=2.0))  # 394 ft/min: a landing
    assert (env.crash, env.landings, env._termination_reason()) == (None, 1, None)
    env._core = _Contacts(set())
    env._track(s, air)
    env._core = _Contacts({"LEFT_MAIN", "RIGHT_MAIN"})
    env._track(air, replace(s, v_down_mps=4.0))  # 787 ft/min
    assert env._termination_reason() == "hard_landing"
    env._on_reset()
    env._core = _Contacts({"LEFT_MAIN", "RIGHT_MAIN", "TAIL_SKID"})
    env._track(s, s)
    assert env._termination_reason() == "tail_strike"


def test_live_free_flights_forget_what_they_sent_and_are_never_saved(tmp_path):
    source = ManualSource(load_env_config(CONFIGS / "free.yaml", {"episode_s": 40.0}), seed=1)
    assert source.targets is None and source.free["start_heading_deg"] == pytest.approx(180.0) and source.free["wind"] is None
    steps, longest = [], 0
    for _, row in source.frames():
        steps.append(row["step"])
        longest = max(longest, len(source._env.recorded[0]))
    assert steps == list(range(len(steps))) and len(steps) == 40 * 120 + 1  # every step once, in order
    assert longest <= FORGET_AFTER + 2 * 6  # not the whole flight
    assert source.save(tmp_path) is None and not any(tmp_path.iterdir())


@pytest.mark.skipif(not SCENERY.exists(), reason="Abu Dhabi scenery not built (scripts/build_scenery.py)")
@pytest.mark.parametrize("name", ["abu_dhabi_free", "abu_dhabi_free_wind"])
def test_abu_dhabi_starts_on_apron_a(name):
    env = make_env(load_env_config(CONFIGS / f"{name}.yaml"))
    _, info = env.reset(seed=2)
    n, e = _map(env, info["state"])
    assert (n, e) == pytest.approx((797.8, -1468.7), abs=0.5)  # the owner's cross hairs, N 24 26.140 E 054 26.613
    assert 0.5 < env.stand_elevation_m < 6.0  # Al Bateen, a few metres above the sea
    for _ in range(20 * 10):
        _, _, term, _, info = env.step(_act(env))
    assert not term and info["state"].alt_agl_m == pytest.approx(1.33, abs=0.1)  # resting on its wheels
