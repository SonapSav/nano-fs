"""Gymnasium environment: capture and hold a target altitude and heading.

Action (Box[-1, 1]^4): elevator, aileron, rudder, throttle, as absolute commands.
Throttle maps [-1, 1] -> [0, 1]. Pitch trim, mixture and flaps are held at the
episode's trim values, so an action near the trim point flies straight and level.

Observation: OBS_NAMES, each divided by its scale in OBS_SCALES (float32).

All randomness comes from the generator seeded by reset(seed=...): same seed and
same config give an identical episode.
"""

import math
from dataclasses import replace

import gymnasium as gym
import numpy as np

from flightsim.atmosphere.turbulence import DrydenTurbulence, to_ned
from flightsim.config import Provenance
from flightsim.control.autopilot import Targets
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.core import Controls, InitialConditions, JSBSimCore, State
from flightsim.envs.config import EnvConfig
from flightsim.runner import RunResult

ACTION_NAMES = ("elevator", "aileron", "rudder", "throttle")

# Observation name -> scale it is divided by (keeps typical values within about +/-1).
OBS_SCALES = {
    "alt_error_m": 100.0,  # target - current
    "heading_error_sin": 1.0,  # sin/cos of (target - current) avoids the +/-pi wrap
    "heading_error_cos": 1.0,
    "tas_error_mps": 10.0,
    "climb_rate_mps": 5.0,
    "phi_rad": 1.0,
    "theta_rad": 1.0,
    "alpha_rad": 0.2,
    "beta_rad": 0.2,
    "p_radps": 1.0,
    "q_radps": 1.0,
    "r_radps": 1.0,
    "prev_elevator": 1.0,
    "prev_aileron": 1.0,
    "prev_rudder": 1.0,
    "prev_throttle": 1.0,
}
OBS_NAMES = tuple(OBS_SCALES)


def controls_to_action(u: Controls) -> np.ndarray:
    return np.array([u.elevator, u.aileron, u.rudder, 2.0 * u.throttle - 1.0], dtype=np.float32)


class AltitudeHeadingHoldEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, cfg: EnvConfig, record: bool = False):
        self.cfg = cfg
        self.record = record
        self.action_space = gym.spaces.Box(-1.0, 1.0, shape=(len(ACTION_NAMES),), dtype=np.float32)
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(len(OBS_NAMES),), dtype=np.float32)
        self._scales = np.array([OBS_SCALES[n] for n in OBS_NAMES])

    # --- Gymnasium API ---------------------------------------------------------

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        cfg, rng = self.cfg, self.np_random
        self.episode_seed = seed
        # Draw order is part of reproducibility: initial condition, targets, then wind.
        # Adding draws only at the end keeps episodes of calm configs unchanged.
        ic = InitialConditions(
            alt_msl_m=cfg.nominal.alt_msl_m + rng.uniform(-cfg.randomize_alt_m, cfg.randomize_alt_m),
            tas_mps=cfg.nominal.tas_mps + rng.uniform(-cfg.randomize_tas_mps, cfg.randomize_tas_mps),
            heading_rad=wrap_angle_rad(
                cfg.nominal.heading_rad + rng.uniform(-cfg.randomize_heading_rad, cfg.randomize_heading_rad)
            )
            % (2 * math.pi),
        )
        target_alt = ic.alt_msl_m + rng.uniform(-cfg.target_alt_offset_m, cfg.target_alt_offset_m)
        target_heading = wrap_angle_rad(
            ic.heading_rad + rng.uniform(-cfg.target_heading_offset_rad, cfg.target_heading_offset_rad)
        ) % (2 * math.pi)
        self.wind = self._draw_wind(rng)
        ic = replace(ic, wind_north_mps=self.wind["north_mps"], wind_east_mps=self.wind["east_mps"])

        # A fresh core per episode: a reused JSBSim instance is not bit-reproducible
        # (state survives run_ic), and construction costs only a few milliseconds.
        self._core = JSBSimCore(cfg.aircraft, 1.0 / cfg.sim_rate_hz)
        self._core.reset(ic, cfg.loading)
        self.trim = self._core.trim()
        self.trim_state = self._core.state()
        self.targets = Targets(alt_msl_m=target_alt, heading_rad=target_heading, tas_mps=self.trim_state.tas_mps)
        self._turbulence = None
        if self.wind["turbulence_sigma_mps"] > 0:
            self._turbulence = DrydenTurbulence(
                self.wind["turbulence_sigma_mps"], cfg.wind.scale_length_m, self.trim_state.tas_mps,
                1.0 / cfg.sim_rate_hz, np.random.default_rng(self.wind["turbulence_seed"]),
            )  # fmt: skip
        self.initial_conditions = ic
        self._state = self.trim_state
        self._prev_action = controls_to_action(self.trim)
        self._decisions = 0
        self._states, self._controls = [self._state], []
        return self._observation(), self._info()

    def step(self, action):
        action = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
        u = replace(
            self.trim,
            elevator=float(action[0]),
            aileron=float(action[1]),
            rudder=float(action[2]),
            throttle=float(action[3] + 1.0) / 2.0,
        )
        for _ in range(self.cfg.sim_steps_per_action):
            self._state = self._sim_step(u)
            if self.record:
                self._controls.append(u)
                self._states.append(self._state)
        self._decisions += 1

        reason = self._termination_reason()
        reward = self._reward(action)
        if reason:
            reward -= self.cfg.reward.termination_penalty
        self._prev_action = action
        truncated = not reason and self._decisions >= self.cfg.max_decisions
        info = self._info()
        info["termination_reason"] = reason
        return self._observation(), reward, bool(reason), truncated, info

    def _sim_step(self, u: Controls) -> State:
        if self._turbulence is not None:
            self._core.set_gust_ned_mps(*to_ned(*self._turbulence.step(), self._state.psi_rad))
        self._state = self._core.step(u)
        return self._state

    def _draw_wind(self, rng: np.random.Generator) -> dict:
        w = self.cfg.wind
        if w is None:
            return {"north_mps": 0.0, "east_mps": 0.0, "speed_mps": 0.0, "from_deg": 0.0,
                    "turbulence": "none", "turbulence_sigma_mps": 0.0, "turbulence_seed": 0}  # fmt: skip
        speed = rng.uniform(*w.steady_speed_mps)
        from_rad = rng.uniform(0.0, 2.0 * math.pi)
        levels = sorted(w.turbulence_probability)
        level = levels[int(rng.choice(len(levels), p=[w.turbulence_probability[k] for k in levels]))]
        return {
            # Wind FROM direction from_rad blows TOWARD from_rad + pi.
            "north_mps": -speed * math.cos(from_rad),
            "east_mps": -speed * math.sin(from_rad),
            "speed_mps": speed,
            "from_deg": math.degrees(from_rad),
            "turbulence": level,
            "turbulence_sigma_mps": w.turbulence_sigma_mps[level],
            "turbulence_seed": int(rng.integers(2**63)),
        }

    def conditions(self) -> dict:
        """This episode's randomized conditions, in SI units (degrees for readability)."""
        ic, t = self.initial_conditions, self.targets
        return {
            "initial_alt_msl_m": ic.alt_msl_m,
            "initial_tas_mps": ic.tas_mps,
            "initial_heading_deg": math.degrees(ic.heading_rad),
            "target_alt_msl_m": t.alt_msl_m,
            "target_heading_deg": math.degrees(t.heading_rad),
            "wind_speed_mps": self.wind["speed_mps"],
            "wind_from_deg": self.wind["from_deg"],
            "turbulence": self.wind["turbulence"],
            "turbulence_sigma_mps": self.wind["turbulence_sigma_mps"],
        }

    # --- Task definition -------------------------------------------------------

    def errors(self, s: State | None = None) -> tuple[float, float, float]:
        """(altitude m, heading rad, airspeed m/s), each as target - current."""
        s = s or self._state
        t = self.targets
        return t.alt_msl_m - s.alt_msl_m, wrap_angle_rad(t.heading_rad - s.psi_rad), t.tas_mps - s.tas_mps

    def _observation(self) -> np.ndarray:
        s = self._state
        e_alt, e_hdg, e_tas = self.errors()
        raw = np.array(
            [
                e_alt, math.sin(e_hdg), math.cos(e_hdg), e_tas, -s.v_down_mps,
                s.phi_rad, s.theta_rad, s.alpha_rad, s.beta_rad, s.p_radps, s.q_radps, s.r_radps,
                *self._prev_action,
            ]
        )  # fmt: skip
        return (raw / self._scales).astype(np.float32)

    def _reward(self, action: np.ndarray) -> float:
        r = self.cfg.reward
        e_alt, e_hdg, e_tas = self.errors()

        def term(e: float, scale: float) -> float:
            return min((e / scale) ** 2, r.clip)

        cost = (
            r.w_alt * term(e_alt, r.alt_scale_m)
            + r.w_heading * term(e_hdg, r.heading_scale_rad)
            + r.w_tas * term(e_tas, r.tas_scale_mps)
            + r.w_action_rate * float(np.sum((action - self._prev_action) ** 2))
        )
        return -cost

    def _termination_reason(self) -> str | None:
        s, t = self._state, self.cfg.termination
        if abs(self.errors()[0]) > t.max_alt_error_m:
            return "altitude_error"
        if abs(s.phi_rad) > t.max_bank_rad:
            return "bank"
        if s.alpha_rad > t.max_alpha_rad:
            return "alpha"
        if s.alt_agl_m < t.min_alt_agl_m:
            return "ground"
        return None

    def _info(self) -> dict:
        return {"state": self._state, "targets": self.targets, "trim": self.trim, "trim_state": self.trim_state}

    # --- Re-flying recorded commands ---------------------------------------------

    def refly(self, seed: int, controls: list[Controls]) -> list[State]:
        """Reset with `seed` and apply recorded commands open loop, one per simulation step.
        For a log of this task, the returned states equal the logged ones exactly."""
        self.reset(seed=seed)
        states = [self._state]
        for u in controls:
            states.append(self._sim_step(u))
        return states

    # --- Logging ---------------------------------------------------------------

    def provenance(self, run_id: str | None = None, pilot: str | None = None) -> Provenance:
        if self.episode_seed is None:
            raise RuntimeError("reset(seed=...) with an explicit seed to log a reproducible episode")
        return Provenance(
            self.cfg.aircraft, self.episode_seed, self.cfg.config_hash, self.cfg.config_json, run_id=run_id, pilot=pilot
        )

    @property
    def recorded(self) -> tuple[list[State], list[Controls]]:
        """States (at the simulation rate, first is the trim state) and the controls applied
        between them, so far this episode. Requires record=True. Do not modify."""
        if not self.record:
            raise RuntimeError("create the environment with record=True")
        return self._states, self._controls

    def episode_result(self) -> RunResult:
        """The episode so far at the simulation rate, for `flightsim.datalog.write_log`.
        Requires record=True."""
        if not self.record:
            raise RuntimeError("create the environment with record=True to log episodes")
        return RunResult(
            trim=self.trim, states=list(self._states), controls=list(self._controls),
            jsbsim_version=self._core.jsbsim_version,
        )  # fmt: skip
