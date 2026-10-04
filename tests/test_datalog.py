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
def short_run(cruise):
    cfg = shortened(cruise, 5.0)
    return cfg, run(cfg)


def test_log_round_trip(tmp_path, short_run):
    cfg, result = short_run
    table, meta = read_log(write_log(tmp_path / "run.parquet", result, cfg))

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
    _, meta = read_log(write_log(tmp_path / "run.parquet", result, cfg))
    assert meta[S.META_CONFIG_JSON] == load_config(CRUISE).config_json
    assert config_hash(json.loads(meta[S.META_CONFIG_JSON])) == cfg.config_hash


def test_same_config_gives_byte_identical_logs(tmp_path, cruise):
    cfg = shortened(cruise, 5.0)
    a = write_log(tmp_path / "a.parquet", run(cfg), cfg)
    b = write_log(tmp_path / "b.parquet", run(cfg), cfg)
    assert a.read_bytes() == b.read_bytes()


def test_reader_refuses_other_schema_versions(tmp_path, short_run):
    cfg, result = short_run
    path = write_log(tmp_path / "run.parquet", result, cfg)
    table = pq.read_table(path)
    meta = dict(table.schema.metadata)
    meta[S.META_SCHEMA_VERSION.encode()] = b"999"
    pq.write_table(table.replace_schema_metadata(meta), path)
    with pytest.raises(ValueError, match="schema version 999"):
        read_log(path)
