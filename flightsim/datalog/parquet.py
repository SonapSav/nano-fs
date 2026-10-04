"""Write and read flight logs as Parquet files following the v1 schema."""

import json
from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from flightsim.config import Provenance
from flightsim.core import aircraft_hash
from flightsim.datalog import schema as S
from flightsim.provenance import code_version
from flightsim.runner import RunResult


def to_table(result: RunResult, cfg: Provenance) -> pa.Table:
    n = len(result.states)
    run_id = cfg.run_id or S.make_run_id(cfg.config_hash, cfg.seed)
    columns = {
        "step": list(range(n)),
        "run_id": [run_id] * n,
        "seed": [cfg.seed] * n,
        "config_hash": [cfg.config_hash] * n,
    }
    for name in S.STATE_COLUMNS:
        columns[name] = [getattr(s, name) for s in result.states]
    for field, name in S.COMMAND_COLUMNS.items():
        columns[name] = [getattr(u, field) for u in result.controls] + [None]

    metadata = {
        S.META_SCHEMA_VERSION: str(S.SCHEMA_VERSION),
        S.META_RUN_ID: run_id,
        S.META_AIRCRAFT: cfg.aircraft,
        S.META_AIRCRAFT_HASH: aircraft_hash(cfg.aircraft),
        S.META_JSBSIM_VERSION: result.jsbsim_version,
        S.META_CONFIG_JSON: cfg.config_json,
        S.META_TRIM_JSON: json.dumps(asdict(result.trim), sort_keys=True),
        S.META_CODE_VERSION: json.dumps(code_version(), sort_keys=True),
    }
    if cfg.pilot:
        metadata[S.META_PILOT] = cfg.pilot
    return pa.Table.from_pydict(columns, schema=S.SCHEMA.with_metadata(metadata))


def write_log(path: str | Path, result: RunResult, cfg: Provenance) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(to_table(result, cfg), path, compression="zstd")
    return path


def read_log(path: str | Path) -> tuple[pa.Table, dict[str, str]]:
    """Return the table and its flightsim metadata; refuses logs from another schema version."""
    table = pq.read_table(path)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items() if k.startswith(b"flightsim.")}
    version = int(meta.get(S.META_SCHEMA_VERSION, "0"))
    if version != S.SCHEMA_VERSION:
        raise ValueError(f"{path}: log schema version {version}, expected {S.SCHEMA_VERSION}")
    return table, meta
