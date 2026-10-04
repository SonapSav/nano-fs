"""Environment configuration, loaded from YAML and hashed like run configs."""

import math
from dataclasses import dataclass
from pathlib import Path

from flightsim.config import canonical_json, config_hash, load_raw, parse_loading
from flightsim.core import InitialConditions, Loading


# Controls a task may give its pilot, in action-vector order. Every task has the first four;
# flaps and pitch trim are optional (held at trim otherwise).
BASE_ACTIONS = ("elevator", "aileron", "rudder", "throttle")
OPTIONAL_ACTIONS = ("flaps", "pitch_trim")


@dataclass(frozen=True)
class ComfortConfig:
    """Soft envelope: only the excess beyond each threshold is penalized,
    as weight * min((excess / scale)^2, clip)."""

    bank_threshold_rad: float
    bank_scale_rad: float
    load_factor_dev_threshold: float  # |n - 1| in g
    load_factor_dev_scale: float
    climb_threshold_mps: float  # |vertical speed|
    climb_scale_mps: float
    w_bank: float
    w_load_factor: float
    w_climb: float
    # Flap overspeed: airspeed (CAS) above the flap-extended limit for the current flap
    # position. None = not scored.
    flap_vfe_10_mps: float | None = None  # limit with flaps extended up to 10 deg
    flap_vfe_full_mps: float | None = None  # limit with flaps beyond 10 deg
    flap_overspeed_scale_mps: float = 1.0
    w_flap_overspeed: float = 0.0


@dataclass(frozen=True)
class RewardConfig:
    alt_scale_m: float
    heading_scale_rad: float
    tas_scale_mps: float
    w_alt: float
    w_heading: float
    w_tas: float
    w_action_rate: float
    clip: float
    termination_penalty: float
    comfort: ComfortConfig | None = None
    # Charge each decision step left after a termination at the largest possible per-step
    # cost, so ending an episode early never scores better than flying on badly.
    charge_remaining_steps: bool = False


@dataclass(frozen=True)
class TerminationConfig:
    max_alt_error_m: float
    max_bank_rad: float
    max_alpha_rad: float
    min_alt_agl_m: float
    min_load_factor: float | None = None  # g; structural limits, None = not checked
    max_load_factor: float | None = None
    flap_overspeed_margin_mps: float | None = None  # end the flight this far above the flap limit


@dataclass(frozen=True)
class WindConfig:
    """Per-episode wind: steady speed uniform in a range, direction uniform over 360 deg,
    turbulence level drawn with the given probabilities (Dryden, MIL-F-8785C)."""

    steady_speed_mps: tuple[float, float]
    turbulence_sigma_mps: dict[str, float]  # level name -> RMS intensity
    turbulence_probability: dict[str, float]  # level name -> probability (sums to 1)
    scale_length_m: float


@dataclass(frozen=True)
class EnvConfig:
    aircraft: str
    sim_rate_hz: float
    control_rate_hz: float
    episode_s: float
    nominal: InitialConditions
    randomize_alt_m: float
    randomize_tas_mps: float
    randomize_heading_rad: float
    target_alt_offset_m: float
    target_heading_offset_rad: float
    loading: Loading
    reward: RewardConfig
    termination: TerminationConfig
    wind: WindConfig | None
    actions: tuple[str, ...]
    config_hash: str
    config_json: str

    @property
    def sim_steps_per_action(self) -> int:
        n = self.sim_rate_hz / self.control_rate_hz
        if abs(n - round(n)) > 1e-9:
            raise ValueError("sim_rate_hz must be a whole multiple of control_rate_hz")
        return round(n)

    @property
    def max_decisions(self) -> int:
        return round(self.episode_s * self.control_rate_hz)


def load_env_config(path: str | Path, overrides: dict | None = None) -> EnvConfig:
    return env_config_from_raw(load_raw(path, overrides))


def env_config_from_raw(raw: dict) -> EnvConfig:
    ic, rnd, tg, rw, term = (raw[k] for k in ("initial_conditions", "randomize", "targets", "reward", "termination"))
    return EnvConfig(
        aircraft=raw["aircraft"],
        sim_rate_hz=float(raw["sim_rate_hz"]),
        control_rate_hz=float(raw["control_rate_hz"]),
        episode_s=float(raw["episode_s"]),
        nominal=InitialConditions(
            alt_msl_m=float(ic["alt_msl_m"]), tas_mps=float(ic["tas_mps"]), heading_rad=math.radians(ic["heading_deg"])
        ),
        randomize_alt_m=float(rnd["alt_msl_m"]),
        randomize_tas_mps=float(rnd["tas_mps"]),
        randomize_heading_rad=math.radians(rnd["heading_deg"]),
        target_alt_offset_m=float(tg["alt_offset_m"]),
        target_heading_offset_rad=math.radians(tg["heading_offset_deg"]),
        loading=parse_loading(raw.get("loading")),
        reward=RewardConfig(
            alt_scale_m=float(rw["alt_scale_m"]),
            heading_scale_rad=math.radians(rw["heading_scale_deg"]),
            tas_scale_mps=float(rw["tas_scale_mps"]),
            w_alt=float(rw["w_alt"]),
            w_heading=float(rw["w_heading"]),
            w_tas=float(rw["w_tas"]),
            w_action_rate=float(rw["w_action_rate"]),
            clip=float(rw["clip"]),
            termination_penalty=float(rw["termination_penalty"]),
            comfort=_parse_comfort(rw.get("comfort")),
            charge_remaining_steps=bool(rw.get("charge_remaining_steps", False)),
        ),
        termination=TerminationConfig(
            max_alt_error_m=float(term["max_alt_error_m"]),
            max_bank_rad=math.radians(term["max_bank_deg"]),
            max_alpha_rad=math.radians(term["max_alpha_deg"]),
            min_alt_agl_m=float(term["min_alt_agl_m"]),
            min_load_factor=float(term["min_load_factor"]) if "min_load_factor" in term else None,
            max_load_factor=float(term["max_load_factor"]) if "max_load_factor" in term else None,
            flap_overspeed_margin_mps=(
                float(term["flap_overspeed_margin_mps"]) if "flap_overspeed_margin_mps" in term else None
            ),
        ),
        wind=_parse_wind(raw.get("wind")),
        actions=_parse_actions(raw.get("actions")),
        config_hash=config_hash(raw),
        config_json=canonical_json(raw),
    )


def _parse_comfort(c: dict | None) -> ComfortConfig | None:
    if not c:
        return None
    return ComfortConfig(
        bank_threshold_rad=math.radians(c["bank_threshold_deg"]),
        bank_scale_rad=math.radians(c["bank_scale_deg"]),
        load_factor_dev_threshold=float(c["load_factor_dev_threshold"]),
        load_factor_dev_scale=float(c["load_factor_dev_scale"]),
        climb_threshold_mps=float(c["climb_threshold_mps"]),
        climb_scale_mps=float(c["climb_scale_mps"]),
        w_bank=float(c["w_bank"]),
        w_load_factor=float(c["w_load_factor"]),
        w_climb=float(c["w_climb"]),
        flap_vfe_10_mps=float(c["flap_vfe_10_mps"]) if "flap_vfe_10_mps" in c else None,
        flap_vfe_full_mps=float(c["flap_vfe_full_mps"]) if "flap_vfe_full_mps" in c else None,
        flap_overspeed_scale_mps=float(c.get("flap_overspeed_scale_mps", 1.0)),
        w_flap_overspeed=float(c.get("w_flap_overspeed", 0.0)),
    )


def _parse_actions(actions: list | None) -> tuple[str, ...]:
    if actions is None:
        return BASE_ACTIONS
    actions = tuple(actions)
    if actions[:4] != BASE_ACTIONS or any(a not in OPTIONAL_ACTIONS for a in actions[4:]) or len(set(actions)) != len(actions):
        raise ValueError(f"actions must be {list(BASE_ACTIONS)} followed by any of {list(OPTIONAL_ACTIONS)}, got {list(actions)}")
    return actions


def _parse_wind(w: dict | None) -> WindConfig | None:
    if not w:
        return None
    turb = w["turbulence"]
    sigma = {k: float(v) for k, v in turb["sigma_mps"].items()}
    prob = {k: float(v) for k, v in turb["probability"].items()}
    if set(prob) - set(sigma):
        raise ValueError(f"turbulence levels without an intensity: {set(prob) - set(sigma)}")
    if abs(sum(prob.values()) - 1.0) > 1e-9:
        raise ValueError("turbulence probabilities must sum to 1")
    lo, hi = (float(x) for x in w["steady_speed_mps"])
    return WindConfig((lo, hi), sigma, prob, float(turb["scale_length_m"]))
