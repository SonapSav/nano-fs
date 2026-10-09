"""Run configuration: loaded from YAML, hashed for provenance.

Config files use SI units; angles may be given in degrees with a `_deg` suffix
for readability and are converted to radians here.

A config file may name a `base:` file (path relative to itself) that it extends.
Overrides (nested dict, or dotted keys like {"wind.steady_speed_mps": [0, 5]}) are merged
into the raw data before hashing, so the recorded hash always describes what ran. Do not
`dataclasses.replace()` a loaded config to change behaviour: the hash would not follow.
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

    @property
    def provenance(self) -> "Provenance":
        return Provenance(self.aircraft, self.seed, self.config_hash, self.config_json)


@dataclass(frozen=True)
class Provenance:
    """What a log records about where it came from."""

    aircraft: str
    seed: int
    config_hash: str
    config_json: str
    run_id: str | None = None  # default: derived from config hash and seed
    pilot: str | None = None  # who flew it, e.g. "human" or "pid"
    pilot_aids: dict | None = None  # demonstrations: viewer aids in use, e.g. {"hud": [[t_on, t_off]]}
    camera: dict | None = None  # demonstrations: the belly camera's pointing (datalog/schema.py META_CAMERA)


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _undot(overrides: dict) -> dict:
    nested: dict = {}
    for key, value in overrides.items():
        *parents, leaf = key.split(".")
        node = nested
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = _undot(value) if isinstance(value, dict) else value
    return nested


def load_raw(path: str | Path, overrides: dict | None = None) -> dict:
    """YAML config with its `base:` chain resolved and overrides merged in."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    base = raw.pop("base", None)
    if base is not None:
        raw = _deep_merge(load_raw(path.parent / base), raw)
    return _deep_merge(raw, _undot(overrides or {}))


def canonical_json(raw: dict) -> str:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))


def config_hash(raw: dict) -> str:
    """SHA-256 of the canonical JSON form, so formatting and key order don't matter."""
    return hashlib.sha256(canonical_json(raw).encode()).hexdigest()


def parse_loading(raw: dict | None) -> Loading:
    raw = raw or {}
    pointmasses, fuel = raw.get("pointmasses_kg"), raw.get("fuel_tanks_kg")
    return Loading(
        pointmasses_kg=tuple(pointmasses) if pointmasses is not None else None,
        fuel_tanks_kg=tuple(fuel) if fuel is not None else None,
    )


def parse_heading_hold(hh: dict) -> HeadingHoldGains:
    return HeadingHoldGains(
        k_heading=float(hh["k_heading"]),
        max_bank_rad=math.radians(hh["max_bank_deg"]),
        k_bank=float(hh["k_bank"]),
        k_roll_rate=float(hh["k_roll_rate"]),
    )


def load_config(path: str | Path, overrides: dict | None = None) -> RunConfig:
    raw = load_raw(path, overrides)
    ic = raw["initial_conditions"]
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
        loading=parse_loading(raw.get("loading")),
        target_heading_rad=math.radians(raw["target_heading_deg"]),
        heading_hold=parse_heading_hold(raw["heading_hold"]),
        config_hash=config_hash(raw),
        config_json=canonical_json(raw),
    )
