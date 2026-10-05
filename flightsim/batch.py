"""Batch runner: many seeded episodes in parallel, with a summary table and a manifest.

Every episode is independent (fresh JSBSim core, seeded by its episode seed), so results
do not depend on the number of workers or the order episodes finish in. Output, under
<out_dir>/<batch_id>/:

  manifest.json       what ran: configs (canonical JSON + hashes), policy, seeds, versions
  episodes.parquet    one row per episode: seed, run id, conditions, metrics (sorted by seed)
  logs/<run_id>.parquet  optional per-episode logs (log schema v1)

The batch id is a hash of the manifest content, so the same batch always gets the same id.
"""

import hashlib
import json
import math
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import jsbsim
import pyarrow as pa
import pyarrow.parquet as pq

from flightsim.config import canonical_json, config_hash
from flightsim.control.autopilot import autopilot_gains_from_raw
from flightsim.core import aircraft_hash
from flightsim.datalog import SCHEMA_VERSION, make_run_id, write_log
from flightsim.provenance import code_version
from flightsim.envs import make_env
from flightsim.envs.config import env_config_from_raw
from flightsim.envs.evaluate import run_episode
from flightsim.envs.policies import LQRPolicy, PIDPolicy, TrimHoldPolicy

POLICIES = ("pid", "lqr", "rl", "approach", "trim_hold")
BATCH_FORMAT = 5  # 2: lqr policy, envelope metrics; 3: comfort_cost; 4: code_version in manifest; 5: landing columns


def make_manifest(env_raw: dict, policy: str, policy_raw: dict | None, seeds: list[int], logs: bool) -> dict:
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; choose from {POLICIES}")
    if policy in ("pid", "lqr", "rl", "approach") and policy_raw is None:
        raise ValueError(f"the {policy} policy needs its config")
    env_cfg = env_config_from_raw(env_raw)
    manifest = {
        "batch_format": BATCH_FORMAT,
        "log_schema_version": SCHEMA_VERSION,
        "aircraft": env_cfg.aircraft,
        "aircraft_hash": aircraft_hash(env_cfg.aircraft),
        "jsbsim_version": jsbsim.__version__,
        "env_config_hash": env_cfg.config_hash,
        "env_config": json.loads(env_cfg.config_json),
        "policy": policy,
        "policy_config_hash": config_hash(policy_raw) if policy_raw is not None else None,
        "policy_config": policy_raw,
        "seeds": seeds,
        "episode_logs": logs,
        "code_version": code_version(),
    }
    # The id covers everything that determines the results, including the exact source
    # (source_sha256), but not the git fields: the same code committed or not is the same batch.
    identity = {**manifest, "code_version": {"source_sha256": manifest["code_version"]["source_sha256"]}}
    manifest["batch_id"] = hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:12]
    return manifest


def make_policy(policy: str, policy_raw: dict | None, cfg):
    if policy == "pid":
        return PIDPolicy(autopilot_gains_from_raw(policy_raw), cfg.control_rate_hz)
    if policy == "lqr":
        return LQRPolicy.designed(cfg, policy_raw)
    if policy == "approach":
        from flightsim.control.approach import approach_gains_from_raw
        from flightsim.envs.policies import ApproachPolicy

        return ApproachPolicy(approach_gains_from_raw(policy_raw), cfg.control_rate_hz)
    if policy == "rl":
        from flightsim.rl.policy import load_policy, model_identity  # torch only when needed

        if model_identity(policy_raw["model_dir"]) != policy_raw:
            raise ValueError(f"model files in {policy_raw['model_dir']} do not match the recorded hashes")
        return load_policy(policy_raw["model_dir"], cfg)
    return TrimHoldPolicy()


# --- Worker side --------------------------------------------------------------------
# Each process builds its environment and policy once, then runs episodes by seed.

_worker: dict = {}


def _init_worker(env_raw: dict, policy: str, policy_raw: dict | None, logs_dir: str | None) -> None:
    cfg = env_config_from_raw(env_raw)
    _worker["env"] = make_env(cfg, record=logs_dir is not None)
    _worker["policy"] = make_policy(policy, policy_raw, cfg)
    _worker["logs_dir"] = Path(logs_dir) if logs_dir else None


def _run_seed(seed: int) -> dict:
    env, policy = _worker["env"], _worker["policy"]
    metrics = run_episode(env, policy, seed)
    run_id = make_run_id(env.cfg.config_hash, seed)
    if _worker["logs_dir"] is not None:
        write_log(_worker["logs_dir"] / f"{run_id}.parquet", env.episode_result(), env.provenance(pilot=policy.name))
    row = {"seed": seed, "run_id": run_id}
    row.update(env.conditions())
    row.update({k: v for k, v in asdict(metrics).items() if k not in ("seed", "policy")})
    row.update(_landing_columns(env))
    return row


LANDING_COLUMNS = ("landed", "landing_failure", "td_along_m", "td_cross_m", "td_sink_mps", "td_cas_mps", "td_pitch_deg", "td_bank_deg", "bounces")


def _landing_columns(env) -> dict:
    """The approach task's touchdown, or nulls for other tasks."""
    if not hasattr(env, "landing_summary"):
        return dict.fromkeys(LANDING_COLUMNS)
    s = env.landing_summary()
    td = s["touchdown"] or {}
    return {
        "landed": s["landed"], "landing_failure": s["failure"], "td_along_m": td.get("along_m"), "td_cross_m": td.get("cross_m"),
        "td_sink_mps": td.get("sink_mps"), "td_cas_mps": td.get("cas_mps"), "td_pitch_deg": td.get("pitch_deg"),
        "td_bank_deg": td.get("bank_deg"), "bounces": s["bounces"],
    }  # fmt: skip


# --- Driver -------------------------------------------------------------------------

SUMMARY_SCHEMA = pa.schema(
    [
        ("seed", pa.int64()), ("run_id", pa.string()),
        ("initial_alt_msl_m", pa.float64()), ("initial_tas_mps", pa.float64()), ("initial_heading_deg", pa.float64()),
        ("target_alt_msl_m", pa.float64()), ("target_heading_deg", pa.float64()),
        ("wind_speed_mps", pa.float64()), ("wind_from_deg", pa.float64()),
        ("turbulence", pa.string()), ("turbulence_sigma_mps", pa.float64()),
        ("episode_return", pa.float64()), ("duration_s", pa.float64()), ("termination_reason", pa.string()),
        ("alt_rms_m", pa.float64()), ("heading_rms_deg", pa.float64()), ("tas_rms_mps", pa.float64()),
        ("alt_final_abs_m", pa.float64()), ("heading_final_abs_deg", pa.float64()),
        ("alt_settle_s", pa.float64()), ("heading_settle_s", pa.float64()), ("action_rate", pa.float64()),
        ("max_bank_deg", pa.float64()), ("min_load_factor", pa.float64()), ("max_load_factor", pa.float64()),
        ("max_abs_climb_mps", pa.float64()), ("max_tas_dev_mps", pa.float64()), ("comfort_cost", pa.float64()),
        # Approach task only (null otherwise): touchdown judged as in envs/approach.py.
        ("landed", pa.bool_()), ("landing_failure", pa.string()), ("td_along_m", pa.float64()), ("td_cross_m", pa.float64()),
        ("td_sink_mps", pa.float64()), ("td_cas_mps", pa.float64()), ("td_pitch_deg", pa.float64()), ("td_bank_deg", pa.float64()),
        ("bounces", pa.int64()),
    ]
)  # fmt: skip


def run_batch(
    env_raw: dict,
    policy: str,
    policy_raw: dict | None,
    seeds: Iterable[int],
    out_dir: str | Path,
    workers: int = 1,
    logs: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Path, dict, pa.Table]:
    seeds = list(seeds)
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be unique")
    manifest = make_manifest(env_raw, policy, policy_raw, seeds, logs)
    batch_dir = Path(out_dir) / manifest["batch_id"]
    batch_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = str(batch_dir / "logs") if logs else None

    rows = []
    init_args = (env_raw, policy, policy_raw, logs_dir)
    if workers <= 1:
        _init_worker(*init_args)
        for i, seed in enumerate(seeds):
            rows.append(_run_seed(seed))
            if progress:
                progress(i + 1, len(seeds))
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=init_args) as pool:
            chunk = max(1, len(seeds) // (workers * 8))
            for i, row in enumerate(pool.map(_run_seed, seeds, chunksize=chunk)):
                rows.append(row)
                if progress:
                    progress(i + 1, len(seeds))

    rows.sort(key=lambda r: r["seed"])
    table = pa.Table.from_pylist(rows, schema=SUMMARY_SCHEMA.with_metadata({"flightsim.batch_manifest": canonical_json(manifest)}))
    pq.write_table(table, batch_dir / "episodes.parquet", compression="zstd")
    (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return batch_dir, manifest, table


def summarize(table: pa.Table) -> list[dict]:
    """Per turbulence level and overall: episode counts and (mean, median, worst) of key
    metrics. Worst is the lowest return or the highest error/settling time."""
    rows = table.to_pylist()
    groups = {"all": rows}
    for level in sorted({r["turbulence"] for r in rows}):
        groups[level] = [r for r in rows if r["turbulence"] == level]
    out = []
    for name, g in groups.items():

        def stats(key: str) -> tuple[float, float, float]:
            vals = sorted(r[key] for r in g)
            mean = sum(vals) / len(vals) if all(math.isfinite(v) for v in vals) else math.inf
            worst = vals[0] if key == "episode_return" else vals[-1]
            return mean, vals[(len(vals) - 1) // 2], worst

        out.append({
            "group": name,
            "episodes": len(g),
            "terminated": sum(1 for r in g if r["termination_reason"]),
            "never_settled": sum(1 for r in g if math.isinf(r["alt_settle_s"]) or math.isinf(r["heading_settle_s"])),
            **{key: stats(key) for key in ("episode_return", "alt_rms_m", "heading_rms_deg", "alt_settle_s", "heading_settle_s")},
        })  # fmt: skip
    return out
