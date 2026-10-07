"""Past flight results (flightsim/stream/results.py): a log re-flown with its recorded
commands gives the task's verdict; logs it cannot reproduce are "unknown"; results are
cached; the server sends them with the log list."""

import asyncio
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from websockets.asyncio.client import connect

from flightsim.control.autopilot import load_autopilot_gains
from flightsim.datalog import write_log
from flightsim.envs import load_env_config, make_env
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import TrimHoldPolicy
from flightsim.stream.results import ResultCache, flight_result
from flightsim.stream.server import ServerConfig, run_server

ROOT = Path(__file__).parent.parent


def _log(path: Path, config: str, episode_s: float, seed: int = 2, policy=None) -> Path:
    cfg = load_env_config(ROOT / "configs" / "envs" / config, {"episode_s": episode_s})
    env = make_env(cfg, record=True)
    run_episode(env, policy or TrimHoldPolicy(), seed=seed)
    return write_log(path, env.episode_result(), env.provenance(pilot="human"))


@pytest.fixture(scope="module")
def logs(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    free = _log(d / "demos" / "free.parquet", "altitude_heading_hold.yaml", 3.0)
    approach = _log(d / "demos" / "approach.parquet", "approach_landing.yaml", 3.0)  # stopped long before the runway
    # A log the simulator cannot reproduce: one state changed.
    table = pq.read_table(free)
    alt = table.column("alt_msl_m").to_pylist()
    alt[-1] += 1.0
    forged = table.set_column(table.schema.get_field_index("alt_msl_m"), "alt_msl_m", pa.array(alt))
    pq.write_table(forged, d / "demos" / "forged.parquet")
    return d, free, approach


def test_results_from_re_flying(logs):
    d, free, approach = logs
    assert flight_result(free) == {"outcome": "completed"}  # the free flight ran its whole episode
    assert flight_result(approach) == {"outcome": "stopped"}  # no touchdown, no failure: ended early
    assert flight_result(d / "demos" / "forged.parquet")["outcome"] == "unknown"
    assert flight_result(d / "nothing.parquet")["outcome"] == "unknown"


def test_result_cache_keys_on_the_file(logs, tmp_path):
    d, free, _ = logs
    cache = ResultCache(d)
    cache.put("demos/free.parquet", {"outcome": "completed"})
    assert ResultCache(d).get("demos/free.parquet") == {"outcome": "completed"}  # persisted
    assert (d / "cache" / "flight_results.json").exists()
    assert cache.get("demos/approach.parquet") is None


def test_server_sends_results_with_the_log_list(logs):
    d, _, _ = logs
    (d / "cache" / "flight_results.json").unlink(missing_ok=True)
    env_cfg = load_env_config(ROOT / "configs" / "envs" / "altitude_heading_hold.yaml", {"episode_s": 3.0})
    gains = load_autopilot_gains(ROOT / "configs" / "autopilot.yaml")

    async def main():
        cfg = ServerConfig(d, env_cfg, gains, flight_results=True)
        ready = asyncio.get_running_loop().create_future()
        server = asyncio.create_task(run_server(cfg, "127.0.0.1", 0, ready.set_result))
        port = await asyncio.wait_for(ready, 5)
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                await ws.send(json.dumps({"type": "list"}))
                first = json.loads(await ws.recv())
                later = first
                while not all("result" in log for log in later["logs"]):
                    later = json.loads(await asyncio.wait_for(ws.recv(), 60))
                return first, later
        finally:
            server.cancel()
            with pytest.raises(asyncio.CancelledError):
                await server

    first, later = asyncio.run(main())
    assert not any("result" in log for log in first["logs"])  # not computed yet
    results = {log["path"]: log["result"]["outcome"] for log in later["logs"]}
    assert results == {"demos/free.parquet": "completed", "demos/approach.parquet": "stopped", "demos/forged.parquet": "unknown"}
    assert {log["path"]: log["task"] for log in later["logs"]}["demos/approach.parquet"] == "approach"
