"""Flight menu grouping and filtering (flightsim/viewer/flightlist.js), run with Node when
available, plus the server-side log summaries it is built from."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from flightsim.datalog import make_run_id, write_log
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import TrimHoldPolicy
from flightsim.stream import sources
from flightsim.stream.sources import list_logs

ROOT = Path(__file__).parent.parent
VIEWER = ROOT / "flightsim" / "viewer"
NODE = shutil.which("node")


def _logs():
    demos = [{"path": f"demos/x-s0-m{i}.parquet", "group": "demos", "seed": 0, "duration_s": 120.0, "pilot": "human", "mtime": 1000.0 + i}
             for i in range(3)]  # fmt: skip
    batch = [{"path": f"batch/abc/logs/x-s{s}.parquet", "group": "batch/abc", "seed": s, "duration_s": 120.0, "pilot": "lqr", "mtime": 5.0}
             for s in range(300, 0, -1)]  # fmt: skip
    local = [{"path": "local/x-s7.parquet", "group": "local", "seed": 7, "duration_s": 300.0, "pilot": None, "mtime": 1.0}]
    return batch + local + demos


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_flight_menu_groups_caps_and_filters():
    script = f"""
    const f = await import({json.dumps((VIEWER / "flightlist.js").as_uri())});
    const logs = {json.dumps(_logs())};
    const out = {{
      all: f.groupLogs(logs),
      seed12: f.groupLogs(logs, "12"),
      s7: f.groupLogs(logs, "s7"),
      text: f.groupLogs(logs, "local"),
    }};
    console.log(JSON.stringify(out));
    """
    out = json.loads(subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True).stdout)
    groups = out["all"]
    assert [g["label"] for g in groups] == ["Your flights", "Recorded flights: local", "Batch abc (300 flights)"]
    demos, local, batch = groups
    assert [o["value"] for o in demos["options"]] == [f"demos/x-s0-m{i}.parquet" for i in (2, 1, 0)]  # newest first
    assert demos["options"][0]["label"].startswith("Seed 0 · 2:00 · ")
    assert local["options"][0]["label"] == "local/x-s7.parquet · 5:00"
    assert len(batch["options"]) == 50 and batch["more"] == 250  # capped, sorted by seed
    assert batch["options"][0] == {"value": "batch/abc/logs/x-s1.parquet", "label": "Seed 1 · LQR · 2:00"}
    # A number filters by exact seed, across groups; text filters by path or label.
    assert [(g["label"], [o["value"] for o in g["options"]]) for g in out["seed12"]] == [
        ("Batch abc (1 flight)", ["batch/abc/logs/x-s12.parquet"])
    ]
    assert [g["label"] for g in out["s7"]] == ["Recorded flights: local", "Batch abc (1 flight)"]
    assert [g["label"] for g in out["text"]] == ["Recorded flights: local"]


def test_list_logs_summarizes_and_caches(tmp_path, monkeypatch):
    cfg = load_env_config(ROOT / "configs" / "envs" / "altitude_heading_hold.yaml", {"episode_s": 2.0})
    env = AltitudeHeadingHoldEnv(cfg, record=True)
    run_episode(env, TrimHoldPolicy(), seed=5)
    run_id = make_run_id(cfg.config_hash, 5)
    write_log(tmp_path / "batch" / "b1" / "logs" / f"{run_id}.parquet", env.episode_result(), env.provenance(pilot="trim_hold"))
    write_log(tmp_path / "demos" / f"{run_id}-mdeadbeef.parquet", env.episode_result(), env.provenance(pilot="human"))
    (tmp_path / "batch" / "b1" / "episodes.parquet").write_bytes(b"not a flight log")
    logs = {log["path"]: log for log in list_logs(tmp_path)}
    assert set(logs) == {f"batch/b1/logs/{run_id}.parquet", f"demos/{run_id}-mdeadbeef.parquet"}
    b = logs[f"batch/b1/logs/{run_id}.parquet"]
    assert b["group"] == "batch/b1" and b["seed"] == 5 and b["pilot"] == "trim_hold"
    assert b["duration_s"] == pytest.approx(2.0) and b["rows"] == 241
    assert logs[f"demos/{run_id}-mdeadbeef.parquet"]["group"] == "demos"

    calls = []
    monkeypatch.setattr(sources.pq, "read_metadata", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError))
    assert {log["path"] for log in list_logs(tmp_path)} == set(logs)  # served from the cache
    assert calls == []
