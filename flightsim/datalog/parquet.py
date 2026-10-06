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
    table = to_table(result, cfg)
    # Float columns: BYTE_STREAM_SPLIT before zstd (lossless; logs ~40-50% smaller and
    # faster to write than zstd alone, measured 2026-10-05). Other columns: dictionary.
    floats = [f.name for f in table.schema if pa.types.is_floating(f.type)]
    pq.write_table(
        table, path, compression="zstd",
        use_dictionary=[c for c in table.column_names if c not in floats],
        column_encoding={c: "BYTE_STREAM_SPLIT" for c in floats},
    )  # fmt: skip
    return path


def read_log(path: str | Path) -> tuple[pa.Table, dict[str, str]]:
    """Return the table (in the current schema) and its flightsim metadata; refuses logs
    from unknown schema versions. Version 1 logs had no brakes, so they are read with
    brake command 0 (null on the last row, like every command)."""
    table = pq.read_table(path)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items() if k.startswith(b"flightsim.")}
    version = int(meta.get(S.META_SCHEMA_VERSION, "0"))
    if version not in S.READABLE_VERSIONS:
        raise ValueError(f"{path}: log schema version {version}, expected one of {S.READABLE_VERSIONS}")
    if version == 1:
        n = table.num_rows
        brake = pa.array([0.0] * (n - 1) + [None] if n else [], pa.float64())
        table = table.append_column(S.SCHEMA.field("cmd_brake_norm"), brake)
    return table, meta
