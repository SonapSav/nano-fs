"""Run configuration: loaded from YAML, hashed for provenance.

Config files use SI units; angles may be given in degrees with a `_deg` suffix
for readability and are converted to radians here.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from flightsim.core import InitialConditions, Loading


@dataclass(frozen=True)
class HeadingHoldGains:
    k_heading: float  # rad bank per rad heading error
    max_bank_rad: float
    k_bank: float  # aileron per rad bank error
    k_roll_rate: float  # aileron per rad/s roll rate


@dataclass(frozen=True)
class RunConfig:
    aircraft: str
    sim_rate_hz: float
    duration_s: float
    seed: int
    initial_conditions: InitialConditions
    loading: Loading
    target_heading_rad: float
    heading_hold: HeadingHoldGains
    config_hash: str
    config_json: str  # canonical JSON the hash was computed over; stored in every log

    @property
    def dt_s(self) -> float:
        return 1.0 / self.sim_rate_hz

    @property
    def n_steps(self) -> int:
        return round(self.duration_s * self.sim_rate_hz)


def canonical_json(raw: dict) -> str:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))


def config_hash(raw: dict) -> str:
    """SHA-256 of the canonical JSON form, so formatting and key order don't matter."""
    return hashlib.sha256(canonical_json(raw).encode()).hexdigest()


def load_config(path: str | Path) -> RunConfig:
    raw = yaml.safe_load(Path(path).read_text())
    ic = raw["initial_conditions"]
    loading = raw.get("loading", {})
    hh = raw["heading_hold"]
    pointmasses = loading.get("pointmasses_kg")
    fuel = loading.get("fuel_tanks_kg")
    return RunConfig(
        aircraft=raw["aircraft"],
        sim_rate_hz=float(raw["sim_rate_hz"]),
        duration_s=float(raw["duration_s"]),
        seed=int(raw["seed"]),
        initial_conditions=InitialConditions(
            alt_msl_m=float(ic["alt_msl_m"]),
            tas_mps=float(ic["tas_mps"]),
            heading_rad=math.radians(ic["heading_deg"]),
        ),
        loading=Loading(
            pointmasses_kg=tuple(pointmasses) if pointmasses is not None else None,
            fuel_tanks_kg=tuple(fuel) if fuel is not None else None,
        ),
        target_heading_rad=math.radians(raw["target_heading_deg"]),
        heading_hold=HeadingHoldGains(
            k_heading=float(hh["k_heading"]),
            max_bank_rad=math.radians(hh["max_bank_deg"]),
            k_bank=float(hh["k_bank"]),
            k_roll_rate=float(hh["k_roll_rate"]),
        ),
        config_hash=config_hash(raw),
        config_json=canonical_json(raw),
    )
