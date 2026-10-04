"""Gymnasium environment: capture and hold a target altitude and heading.

Action (Box[-1, 1]^n): absolute commands for the task's controls (`actions` in the env
config): elevator, aileron, rudder, throttle, optionally flaps and pitch trim.
Throttle and flaps map [-1, 1] -> [0, 1]. Controls not in the action set (pitch trim,
mixture, flaps by default) are held at the episode's trim values, so an action near the
trim point flies straight and level.

Observation: the base quantities of OBS_SCALES plus the previous action of each control
(`env.obs_names`), each divided by its scale (float32). With the default controls this
is exactly OBS_NAMES.

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
from flightsim.envs.config import BASE_ACTIONS, EnvConfig
from flightsim.runner import RunResult

ACTION_NAMES = BASE_ACTIONS  # default controls
G0 = 9.80665
_UNIT_RANGE = ("throttle", "flaps")  # commands in [0, 1], actions in [-1, 1]

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
}
OBS_NAMES = (*OBS_SCALES, *(f"prev_{a}" for a in ACTION_NAMES))


def load_factor(s: State) -> float:
    """Normal load factor in g (1 in level flight), from the body z specific force."""
    return -s.az_mps2 / G0


def controls_to_action(u: Controls, names: tuple[str, ...] = ACTION_NAMES) -> np.ndarray:
    return np.array(
        [2.0 * getattr(u, n) - 1.0 if n in _UNIT_RANGE else getattr(u, n) for n in names], dtype=np.float32
    )


def action_to_controls(action: np.ndarray, names: tuple[str, ...], base: Controls) -> Controls:
    """Controls with the named fields taken from the action, the rest from `base`."""
    return replace(base, **{n: (float(a) + 1.0) / 2.0 if n in _UNIT_RANGE else float(a) for n, a in zip(names, action)})


def flap_limit_mps(flap_deg: float, vfe_10_mps: float, vfe_full_mps: float) -> float:
    """Maximum flap-extended airspeed for a flap position (inf when retracted)."""
    if flap_deg <= 0.5:
        return math.inf
    return vfe_10_mps if flap_deg <= 10.5 else vfe_full_mps


class AltitudeHeadingHoldEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, cfg: EnvConfig, record: bool = False):
        self.cfg = cfg
        self.record = record
        self.action_names = cfg.actions
        self.obs_names = (*OBS_SCALES, *(f"prev_{a}" for a in self.action_names))
        self.action_space = gym.spaces.Box(-1.0, 1.0, shape=(len(self.action_names),), dtype=np.float32)
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(len(self.obs_names),), dtype=np.float32)
        self._scales = np.array([OBS_SCALES.get(n, 1.0) for n in self.obs_names])

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
        self._prev_action = controls_to_action(self.trim, self.action_names)
        self.last_comfort_cost = 0.0
        self.last_comfort_terms = {}
        self._decisions = 0
        self._states, self._controls = [self._state], []
        return self._observation(), self._info()

    def step(self, action):
        action = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
        u = action_to_controls(action, self.action_names, self.trim)
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
            if self.cfg.reward.charge_remaining_steps:
                reward -= (self.cfg.max_decisions - self._decisions) * self.max_step_cost()
        self._prev_action = action
        truncated = not reason and self._decisions >= self.cfg.max_decisions
        info = self._info()
        info["termination_reason"] = reason
        info["comfort_cost"] = self.last_comfort_cost
        info["comfort_terms"] = dict(self.last_comfort_terms)
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
        self.last_comfort_cost = self._comfort_cost(term)
        return -(cost + self.last_comfort_cost)

    def max_step_cost(self) -> float:
        """Upper bound of the per-decision cost: every clipped term at its clip, and the
        action-rate term at its largest possible value (each action moves across [-1, 1])."""
        r = self.cfg.reward
        bound = (r.w_alt + r.w_heading + r.w_tas) * r.clip + r.w_action_rate * 4.0 * len(self.action_names)
        if r.comfort is not None:
            c = r.comfort
            bound += (c.w_bank + c.w_load_factor + c.w_climb + c.w_flap_overspeed) * r.clip
        return bound

    def _comfort_cost(self, term) -> float:
        """Penalty for flying outside the comfort envelope (0 if the task has none)."""
        c = self.cfg.reward.comfort
        if c is None:
            self.last_comfort_terms = {}
            return 0.0
        s = self._state
        excess = lambda value, threshold: max(0.0, abs(value) - threshold)  # noqa: E731
        self.last_comfort_terms = {
            "bank": c.w_bank * term(excess(s.phi_rad, c.bank_threshold_rad), c.bank_scale_rad),
            "load_factor": c.w_load_factor
            * term(excess(load_factor(s) - 1.0, c.load_factor_dev_threshold), c.load_factor_dev_scale),
            "climb": c.w_climb * term(excess(s.v_down_mps, c.climb_threshold_mps), c.climb_scale_mps),
        }
        if c.flap_vfe_10_mps is not None and c.w_flap_overspeed:
            over = s.cas_mps - self._flap_limit(s)
            self.last_comfort_terms["flap_overspeed"] = (
                c.w_flap_overspeed * term(over, c.flap_overspeed_scale_mps) if over > 0 else 0.0
            )
        return sum(self.last_comfort_terms.values())

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
        if t.max_load_factor is not None and load_factor(s) > t.max_load_factor:
            return "load_factor"
        if t.min_load_factor is not None and load_factor(s) < t.min_load_factor:
            return "load_factor"
        if t.flap_overspeed_margin_mps is not None and s.cas_mps > self._flap_limit(s) + t.flap_overspeed_margin_mps:
            return "flap_overspeed"
        return None

    def _flap_limit(self, s: State) -> float:
        c = self.cfg.reward.comfort
        if c is None or c.flap_vfe_10_mps is None:
            return math.inf
        return flap_limit_mps(math.degrees(s.flap_pos_rad), c.flap_vfe_10_mps, c.flap_vfe_full_mps)

    def _info(self) -> dict:
        return {
            "state": self._state, "targets": self.targets, "trim": self.trim, "trim_state": self.trim_state,
            "action_names": self.action_names,
        }  # fmt: skip

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
