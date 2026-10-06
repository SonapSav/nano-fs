"""Flaps and pitch trim in manual-flight tasks, and the flap speed limits."""

import asyncio
import json
import math
from pathlib import Path

import numpy as np
import pytest
from websockets.asyncio.client import connect

from flightsim.control.autopilot import load_autopilot_gains
from flightsim.control.manual import STALE_AFTER_S, HumanPolicy
from flightsim.core import Controls
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config
from flightsim.envs.altitude_heading import OBS_NAMES, action_to_controls, controls_to_action, flap_limit_mps
from flightsim.envs.config import BASE_ACTIONS
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import PIDPolicy
from flightsim.stream.server import ServerConfig, run_server

ROOT = Path(__file__).parent.parent
ENVS = ROOT / "configs" / "envs"
MANUAL = ENVS / "manual.yaml"
KT = 1852 / 3600


def test_manual_tasks_add_flaps_and_trim_and_other_tasks_keep_four_controls():
    for name in ("manual.yaml", "manual_wind.yaml"):
        env = AltitudeHeadingHoldEnv(load_env_config(ENVS / name))
        assert env.action_names == (*BASE_ACTIONS, "flaps", "pitch_trim")
        assert env.action_space.shape == (6,) and env.observation_space.shape == (18,)
    for name in ("altitude_heading_hold.yaml", "altitude_heading_hold_wind.yaml", "altitude_heading_hold_comfort.yaml"):
        env = AltitudeHeadingHoldEnv(load_env_config(ENVS / name))
        assert env.action_names == BASE_ACTIONS and env.obs_names == OBS_NAMES and env.action_space.shape == (4,)


def test_rejects_malformed_action_lists():
    with pytest.raises(ValueError, match="actions must be"):
        load_env_config(MANUAL, {"actions": ["aileron", "elevator", "rudder", "throttle"]})
    with pytest.raises(ValueError, match="actions must be"):
        load_env_config(MANUAL, {"actions": [*BASE_ACTIONS, "mixture"]})


def test_action_conversion_round_trips():
    names = (*BASE_ACTIONS, "flaps", "pitch_trim")
    u = Controls(elevator=-0.2, aileron=0.1, rudder=-0.05, throttle=0.7, flaps=2 / 3, pitch_trim=0.15)
    back = action_to_controls(controls_to_action(u, names), names, Controls())
    for f in names:
        assert getattr(back, f) == pytest.approx(getattr(u, f), abs=1e-6)


def _calm_manual_env(**overrides):
    return AltitudeHeadingHoldEnv(load_env_config(MANUAL, overrides))


def test_flap_command_reaches_its_detent_and_trim_acts():
    env = _calm_manual_env()
    _, info = env.reset(seed=1)
    action = controls_to_action(info["trim"], env.action_names)
    action[4] = 2 * (1 / 3) - 1  # flaps 10 deg
    for _ in range(20 * 4):  # 0 -> 10 deg takes 2 s in the model
        _, _, _, _, info = env.step(action)
    assert math.degrees(info["state"].flap_pos_rad) == pytest.approx(10.0, abs=0.1)

    env2 = _calm_manual_env()
    _, info2 = env2.reset(seed=1)
    a2 = controls_to_action(info2["trim"], env2.action_names)
    a2[5] = info2["trim"].pitch_trim + 0.2  # trim nose down
    for _ in range(20 * 3):
        _, _, _, _, info2 = env2.step(a2)
    assert info2["state"].q_radps < 0 or info2["state"].theta_rad < info2["trim_state"].theta_rad


def test_flap_limits_follow_the_poh():
    vfe10, vfefull = 110 * KT, 85 * KT
    assert flap_limit_mps(0.0, vfe10, vfefull) == math.inf
    assert flap_limit_mps(10.0, vfe10, vfefull) == vfe10
    assert flap_limit_mps(20.0, vfe10, vfefull) == vfefull
    cfg = load_env_config(MANUAL)
    assert cfg.reward.comfort.flap_vfe_10_mps == pytest.approx(110 * KT, abs=0.01)
    assert cfg.reward.comfort.flap_vfe_full_mps == pytest.approx(85 * KT, abs=0.01)


def test_flap_overspeed_costs_comfort_then_ends_the_flight():
    # Cruise is ~93 KCAS: full flap (85 kt limit) is overspeed but within the 10 kt margin.
    env = _calm_manual_env(**{"initial_conditions.tas_mps": 51.44, "randomize.tas_mps": 0.0})
    _, info = env.reset(seed=2)
    action = controls_to_action(info["trim"], env.action_names)
    action[4] = 1.0  # full flaps
    costs, terminated = [], False
    for _ in range(20 * 5):
        _, _, terminated, _, info = env.step(action)
        costs.append(info["comfort_terms"].get("flap_overspeed", 0.0))
        if terminated:
            break
    assert max(costs) > 0.0
    # A tighter margin turns the same flight into a termination.
    env2 = _calm_manual_env(**{"initial_conditions.tas_mps": 51.44, "randomize.tas_mps": 0.0,
                               "termination.flap_overspeed_margin_mps": 0.5})  # fmt: skip
    _, info = env2.reset(seed=2)
    action = controls_to_action(info["trim"], env2.action_names)
    action[4] = 1.0
    for _ in range(20 * 5):
        _, _, terminated, _, info = env2.step(action)
        if terminated:
            break
    assert terminated and info["termination_reason"] == "flap_overspeed"


class FakeClock:
    t = 0.0

    def __call__(self):
        return self.t


def test_human_pilot_holds_flaps_and_trim_when_input_stops():
    clock = FakeClock()
    pilot = HumanPolicy(clock)
    names = (*BASE_ACTIONS, "flaps", "pitch_trim")
    trim = Controls(throttle=0.7, pitch_trim=0.15)
    pilot.reset({"trim": trim, "action_names": names})
    pilot.set_input(0.3, 0.0, 0.0, 0.6, flaps=2 / 3, pitch_trim=0.25)
    clock.t = STALE_AFTER_S + 1.0
    a = pilot(None, {})
    assert a[0] == pytest.approx(0.0)  # stick centred
    assert a[3] == pytest.approx(2 * 0.6 - 1)  # throttle held
    assert a[4] == pytest.approx(2 * (2 / 3) - 1)  # flaps held
    assert a[5] == pytest.approx(0.25)  # trim held
    with pytest.raises(ValueError):
        pilot.set_input(0, 0, 0, 0.5, flaps=float("inf"))


def test_autopilot_flies_the_manual_task():
    cfg = load_env_config(MANUAL)
    m = run_episode(AltitudeHeadingHoldEnv(cfg), PIDPolicy(load_autopilot_gains(ROOT / "configs" / "autopilot.yaml"), cfg.control_rate_hz), 3)
    assert m.termination_reason is None and m.alt_final_abs_m < 1.0


def test_server_routes_flaps_and_trim_to_the_physics(tmp_path):
    calm = load_env_config(MANUAL, {"episode_s": 6.0})
    gains = load_autopilot_gains(ROOT / "configs" / "autopilot.yaml")

    async def main():
        ready = asyncio.get_running_loop().create_future()
        cfg = ServerConfig(tmp_path, calm, gains, manual_env_cfgs={"calm": calm})
        server = asyncio.create_task(run_server(cfg, "127.0.0.1", 0, ready.set_result))
        port = await ready
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                await ws.send(json.dumps({"type": "play", "source": "manual", "conditions": "calm", "seed": 1, "speed": 1}))
                rows = []
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if msg["type"] == "frame":
                        rows.append(msg["row"])
                        await ws.send(json.dumps({"type": "input", "elevator": 0, "aileron": 0, "rudder": 0,
                                                  "throttle": 0.7, "flaps": 1 / 3, "pitch_trim": 0.2}))  # fmt: skip
                    elif msg["type"] == "end":
                        break
                await ws.send(json.dumps({"type": "play", "source": "manual", "conditions": "stormy"}))
                while True:  # skip messages from the finished flight (e.g. "saved")
                    error = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if error["type"] == "error":
                        return rows, error
        finally:
            server.cancel()
            with pytest.raises(asyncio.CancelledError):
                await server

    rows, error = asyncio.run(main())
    assert rows[-2]["cmd_flaps_norm"] == pytest.approx(1 / 3)
    assert rows[-2]["cmd_pitch_trim_norm"] == pytest.approx(0.2)
    assert math.degrees(rows[-1]["flap_pos_rad"]) == pytest.approx(10.0, abs=0.5)
    assert error["type"] == "error" and "stormy" in error["message"]


def test_human_brakes_act_while_held_and_release_when_input_stops():
    clock = FakeClock()
    pilot = HumanPolicy(clock)
    names = (*BASE_ACTIONS, "brake")
    pilot.reset({"trim": Controls(throttle=0.0), "action_names": names})
    pilot.set_input(0.0, 0.0, 0.0, 0.0, brake=0.8)
    assert pilot(None, {})[4] == pytest.approx(2 * 0.8 - 1)  # brake held
    pilot.set_input(0.0, 0.0, 0.0, 0.0)
    assert pilot(None, {})[4] == pytest.approx(-1.0)  # omitted = released
    pilot.set_input(0.0, 0.0, 0.0, 0.0, brake=1.0)
    clock.t = STALE_AFTER_S + 1.0
    assert pilot(None, {})[4] == pytest.approx(-1.0)  # input stopped: released
