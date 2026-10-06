import json

import pyarrow.parquet as pq
import pytest

from flightsim.config import config_hash, load_config
from flightsim.core import CONTROL_FIELDS, STATE_FIELDS, Controls
from flightsim.datalog import SCHEMA, SCHEMA_VERSION, read_log, write_log
from flightsim.datalog import schema as S
from flightsim.runner import run

from conftest import CRUISE, shortened


def test_schema_covers_exactly_the_core_state_and_controls():
    # If State or Controls change, the schema must be updated deliberately (and versioned).
    assert set(S.STATE_COLUMNS) == set(STATE_FIELDS)
    assert set(S.COMMAND_COLUMNS) == set(CONTROL_FIELDS)
    assert len(SCHEMA.names) == len(set(SCHEMA.names))


@pytest.fixture
def short_run():
    cfg = shortened(5.0)
    return cfg, run(cfg)


def test_log_round_trip(tmp_path, short_run):
    cfg, result = short_run
    table, meta = read_log(write_log(tmp_path / "run.parquet", result, cfg.provenance))

    assert table.schema.remove_metadata() == SCHEMA
    assert table.num_rows == len(result.states) == cfg.n_steps + 1
    assert table.column("step").to_pylist() == list(range(table.num_rows))
    assert table.column("alt_msl_m").to_pylist() == [s.alt_msl_m for s in result.states]
    assert table.column("cmd_aileron_norm").to_pylist() == [u.aileron for u in result.controls] + [None]

    assert meta[S.META_SCHEMA_VERSION] == str(SCHEMA_VERSION)
    assert meta[S.META_RUN_ID] == S.make_run_id(cfg.config_hash, cfg.seed)
    assert meta[S.META_JSBSIM_VERSION] == result.jsbsim_version
    assert len(meta[S.META_AIRCRAFT_HASH]) == 64
    assert Controls(**json.loads(meta[S.META_TRIM_JSON])) == result.trim


def test_logged_config_reproduces_the_hash(tmp_path, short_run):
    cfg, result = short_run
    _, meta = read_log(write_log(tmp_path / "run.parquet", result, cfg.provenance))
    assert meta[S.META_CONFIG_JSON] == cfg.config_json
    assert cfg.config_hash != load_config(CRUISE).config_hash  # the override is part of the hash
    assert config_hash(json.loads(meta[S.META_CONFIG_JSON])) == cfg.config_hash


def test_same_config_gives_byte_identical_logs(tmp_path):
    cfg = shortened(5.0)
    a = write_log(tmp_path / "a.parquet", run(cfg), cfg.provenance)
    b = write_log(tmp_path / "b.parquet", run(cfg), cfg.provenance)
    assert a.read_bytes() == b.read_bytes()


def test_reader_refuses_other_schema_versions(tmp_path, short_run):
    cfg, result = short_run
    path = write_log(tmp_path / "run.parquet", result, cfg.provenance)
    table = pq.read_table(path)
    meta = dict(table.schema.metadata)
    meta[S.META_SCHEMA_VERSION.encode()] = b"999"
    pq.write_table(table.replace_schema_metadata(meta), path)
    with pytest.raises(ValueError, match="schema version 999"):
        read_log(path)


def test_float_columns_use_byte_stream_split_and_read_back_exactly(short_run, tmp_path):
    cfg, result = short_run
    path = write_log(tmp_path / "run.parquet", result, cfg.provenance)
    md = pq.read_metadata(path)
    names = md.schema.names
    enc = {names[i]: md.row_group(0).column(i).encodings for i in range(md.num_columns)}
    assert "BYTE_STREAM_SPLIT" in enc["alt_msl_m"] and "BYTE_STREAM_SPLIT" in enc["t_s"]
    assert "BYTE_STREAM_SPLIT" not in enc["run_id"]
    table, _ = read_log(path)
    assert table.column("alt_msl_m").to_pylist() == [s.alt_msl_m for s in result.states]


def test_version_1_logs_read_with_the_brake_released(tmp_path, short_run):
    """Schema 2 added cmd_brake_norm; version 1 logs (no brakes) read as brake 0."""
    cfg, result = short_run
    path = write_log(tmp_path / "run.parquet", result, cfg.provenance)
    table = pq.read_table(path)
    v1 = table.drop_columns(["cmd_brake_norm"])
    meta = dict(v1.schema.metadata)
    meta[S.META_SCHEMA_VERSION.encode()] = b"1"
    pq.write_table(v1.replace_schema_metadata(meta), path)
    read, _ = read_log(path)
    assert read.schema.remove_metadata() == SCHEMA
    assert read.column("cmd_brake_norm").to_pylist() == [0.0] * (read.num_rows - 1) + [None]
    assert read.column("alt_msl_m").to_pylist() == table.column("alt_msl_m").to_pylist()
