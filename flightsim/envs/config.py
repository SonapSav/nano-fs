"""Environment configuration, loaded from YAML and hashed like run configs."""

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from flightsim.config import canonical_json, config_hash, parse_loading
from flightsim.core import InitialConditions, Loading


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


@dataclass(frozen=True)
class TerminationConfig:
    max_alt_error_m: float
    max_bank_rad: float
    max_alpha_rad: float
    min_alt_agl_m: float


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


def load_env_config(path: str | Path) -> EnvConfig:
    raw = yaml.safe_load(Path(path).read_text())
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
        ),
        termination=TerminationConfig(
            max_alt_error_m=float(term["max_alt_error_m"]),
            max_bank_rad=math.radians(term["max_bank_deg"]),
            max_alpha_rad=math.radians(term["max_alpha_deg"]),
            min_alt_agl_m=float(term["min_alt_agl_m"]),
        ),
        config_hash=config_hash(raw),
        config_json=canonical_json(raw),
    )
