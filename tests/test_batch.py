import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from flightsim.batch import run_batch, summarize
from flightsim.config import load_raw
from flightsim.control.autopilot import load_autopilot_gains
from flightsim.datalog import read_log
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import PIDPolicy

ROOT = Path(__file__).parent.parent
WINDY = ROOT / "configs" / "envs" / "altitude_heading_hold_wind.yaml"
AUTOPILOT = ROOT / "configs" / "autopilot.yaml"
SHORT = {"episode_s": 8.0}
SEEDS = [5, 1, 3, 2, 4, 0]  # deliberately unordered


@pytest.fixture(scope="module")
def serial(tmp_path_factory):
    out = tmp_path_factory.mktemp("serial")
    return run_batch(load_raw(WINDY, SHORT), "pid", load_raw(AUTOPILOT), SEEDS, out, workers=1, logs=True)


def test_results_do_not_depend_on_worker_count(serial, tmp_path):
    batch_dir, manifest, table = serial
    par_dir, par_manifest, par_table = run_batch(load_raw(WINDY, SHORT), "pid", load_raw(AUTOPILOT), SEEDS, tmp_path, workers=3, logs=True)
    assert par_manifest == manifest
    assert (par_dir / "episodes.parquet").read_bytes() == (batch_dir / "episodes.parquet").read_bytes()
    for log in sorted((batch_dir / "logs").iterdir()):
        assert (par_dir / "logs" / log.name).read_bytes() == log.read_bytes()


def test_summary_is_sorted_by_seed_and_matches_single_episodes(serial):
    _, _, table = serial
    assert table.column("seed").to_pylist() == sorted(SEEDS)
    env_cfg = load_env_config(WINDY, SHORT)
    env = AltitudeHeadingHoldEnv(env_cfg)
    m = run_episode(env, PIDPolicy(load_autopilot_gains(AUTOPILOT), env_cfg.control_rate_hz), seed=3)
    row = table.to_pylist()[3]
    assert row["seed"] == 3 and row["episode_return"] == m.episode_return and row["alt_rms_m"] == m.alt_rms_m
    assert row["turbulence"] == env.wind["turbulence"]


def test_manifest_describes_the_batch(serial):
    batch_dir, manifest, table = serial
    on_disk = json.loads((batch_dir / "manifest.json").read_text())
    assert on_disk == manifest
    assert batch_dir.name == manifest["batch_id"]
    assert manifest["env_config_hash"] == load_env_config(WINDY, SHORT).config_hash
    assert manifest["env_config"]["episode_s"] == 8.0
    assert manifest["seeds"] == SEEDS and manifest["policy"] == "pid"
    assert json.loads(pq.read_schema(batch_dir / "episodes.parquet").metadata[b"flightsim.batch_manifest"]) == manifest


def test_episode_logs_carry_policy_and_config(serial):
    batch_dir, manifest, table = serial
    run_id = table.column("run_id")[0].as_py()
    log, meta = read_log(batch_dir / "logs" / f"{run_id}.parquet")
    assert meta["flightsim.pilot"] == "pid"
    assert json.loads(meta["flightsim.config_json"]) == manifest["env_config"]


def test_batch_id_changes_with_any_input(tmp_path):
    def batch_id(**kw):
        args = {"env_raw": load_raw(WINDY, SHORT), "policy": "pid", "policy_raw": load_raw(AUTOPILOT), "seeds": [0]}
        args.update(kw)
        from flightsim.batch import make_manifest

        return make_manifest(**args, logs=False)["batch_id"]

    base = batch_id()
    assert batch_id() == base
    assert batch_id(env_raw=load_raw(WINDY, {**SHORT, "wind.steady_speed_mps": [0, 3]})) != base
    assert batch_id(policy_raw=load_raw(AUTOPILOT, {"k_altitude": 0.2})) != base
    assert batch_id(seeds=[1]) != base
    assert batch_id(policy="trim_hold", policy_raw=None) != base


def test_summary_groups_by_turbulence(serial):
    _, _, table = serial
    groups = {g["group"]: g for g in summarize(table)}
    assert groups["all"]["episodes"] == len(SEEDS)
    assert sum(g["episodes"] for name, g in groups.items() if name != "all") == len(SEEDS)


def test_rejects_duplicate_seeds_and_unknown_policy(tmp_path):
    with pytest.raises(ValueError, match="unique"):
        run_batch(load_raw(WINDY, SHORT), "pid", load_raw(AUTOPILOT), [1, 1], tmp_path)
    with pytest.raises(ValueError, match="unknown policy"):
        run_batch(load_raw(WINDY, SHORT), "rl", None, [1], tmp_path)
