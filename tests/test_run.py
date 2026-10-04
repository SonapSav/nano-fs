import math
from dataclasses import replace

from flightsim.config import config_hash
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.runner import run

from conftest import shortened

M_TO_FT = 1 / 0.3048


def test_same_config_gives_identical_runs(cruise):
    cfg = shortened(cruise, 20.0)
    a, b = run(cfg), run(cfg)
    assert a.states == b.states
    assert a.controls == b.controls


def test_trimmed_cruise_holds_altitude_speed_heading(cruise):
    cfg = shortened(cruise, 60.0)
    states = run(cfg).states
    s0 = states[0]
    for s in states:
        assert abs(s.alt_msl_m - s0.alt_msl_m) < 3.0
        assert abs(s.tas_mps - s0.tas_mps) < 0.5
        assert abs(math.degrees(wrap_angle_rad(s.psi_rad - s0.psi_rad))) < 0.5


def test_heading_hold_captures_new_heading(cruise):
    target = math.radians(120)
    cfg = replace(shortened(cruise, 40.0), target_heading_rad=target)
    states = run(cfg).states
    errors_deg = [math.degrees(wrap_angle_rad(target - s.psi_rad)) for s in states]
    assert max(-e for e in errors_deg) < 2.0  # overshoot
    assert all(abs(e) < 1.0 for e, s in zip(errors_deg, states) if s.t_s > 25.0)
    assert max(abs(math.degrees(s.phi_rad)) for s in states) <= 21.0


def test_config_hash_ignores_key_order():
    assert config_hash({"a": 1, "b": {"c": 2, "d": 3}}) == config_hash({"b": {"d": 3, "c": 2}, "a": 1})
    assert config_hash({"a": 1}) != config_hash({"a": 2})
