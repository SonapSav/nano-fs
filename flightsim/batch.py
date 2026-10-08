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

POLICIES = ("pid", "lqr", "rl", "approach", "takeoff", "circuit", "route", "trim_hold")
BATCH_FORMAT = 8  # 2: lqr policy, envelope metrics; 3: comfort_cost; 4: code_version in manifest; 5: landing columns; 6: drift, rollout; 7: takeoff columns; 8: route columns


def make_manifest(env_raw: dict, policy: str, policy_raw: dict | None, seeds: list[int], logs: bool) -> dict:
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; choose from {POLICIES}")
    if policy in ("pid", "lqr", "rl", "approach", "takeoff", "circuit", "route") and policy_raw is None:
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
    if policy == "takeoff":
        from flightsim.control.takeoff import takeoff_gains_from_raw
        from flightsim.envs.policies import TakeoffPolicy

        return TakeoffPolicy(takeoff_gains_from_raw(policy_raw), cfg.control_rate_hz)
    if policy == "circuit":
        from flightsim.control.circuit import circuit_gains_from_raw
        from flightsim.envs.policies import CircuitPolicy

        return CircuitPolicy(circuit_gains_from_raw(policy_raw), cfg.control_rate_hz)
    if policy == "route":
        from flightsim.control.route import route_gains_from_raw
        from flightsim.envs.policies import RoutePolicy

        return RoutePolicy(route_gains_from_raw(policy_raw), cfg.control_rate_hz)
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
    row.update(_takeoff_columns(env))
    row.update(_route_columns(env))
    return row


LANDING_COLUMNS = (
    "landed", "landing_failure", "td_along_m", "td_cross_m", "td_sink_mps", "td_cas_mps", "td_pitch_deg", "td_bank_deg",
    "td_drift_deg", "bounces", "stop_along_m", "ground_roll_m", "rollout_max_cross_m",
)  # fmt: skip


def _landing_columns(env) -> dict:
    """The approach task's touchdown, or nulls for other tasks."""
    if not hasattr(env, "landing_summary"):
        return dict.fromkeys(LANDING_COLUMNS)
    s = env.landing_summary()
    td, ro = s["touchdown"] or {}, s.get("rollout") or {}
    return {
        "landed": s["landed"], "landing_failure": s["failure"], "td_along_m": td.get("along_m"), "td_cross_m": td.get("cross_m"),
        "td_sink_mps": td.get("sink_mps"), "td_cas_mps": td.get("cas_mps"), "td_pitch_deg": td.get("pitch_deg"),
        "td_bank_deg": td.get("bank_deg"), "td_drift_deg": td.get("drift_deg"), "bounces": s["bounces"],
        "stop_along_m": ro.get("stop_along_m"), "ground_roll_m": ro.get("ground_roll_m"), "rollout_max_cross_m": ro.get("max_cross_m"),
    }  # fmt: skip


TAKEOFF_COLUMNS = (
    "climbed", "takeoff_failure", "liftoff_ground_roll_m", "liftoff_cas_mps", "liftoff_pitch_deg", "fifty_ft_distance_m",
    "ground_max_cross_m", "skips",
)  # fmt: skip


def _takeoff_columns(env) -> dict:
    """The takeoff task's lift-off and climb-out, or nulls for other tasks."""
    if not hasattr(env, "takeoff_summary"):
        return dict.fromkeys(TAKEOFF_COLUMNS)
    s = env.takeoff_summary()
    lo, ff = s["liftoff"] or {}, s["fifty_ft"] or {}
    return {
        "climbed": s["climbed"], "takeoff_failure": s["failure"], "liftoff_ground_roll_m": lo.get("ground_roll_m"),
        "liftoff_cas_mps": lo.get("cas_mps"), "liftoff_pitch_deg": lo.get("pitch_deg"), "fifty_ft_distance_m": ff.get("distance_m"),
        "ground_max_cross_m": s["ground_max_cross_m"], "skips": s["skips"],
    }  # fmt: skip


ROUTE_COLUMNS = ("route_completed", "route_legs", "route_legs_done", "route_length_m", "route_time_s", "xtk_rms_m", "xtk_max_m")


def _route_columns(env) -> dict:
    """The navigation task's route, or nulls for other tasks."""
    if not hasattr(env, "route_summary"):
        return dict.fromkeys(ROUTE_COLUMNS)
    s = env.route_summary()
    return {
        "route_completed": s["completed"], "route_legs": s["legs"], "route_legs_done": s["legs_done"], "route_length_m": s["length_m"],
        "route_time_s": s["time_s"], "xtk_rms_m": s["xtk_rms_m"], "xtk_max_m": s["xtk_max_m"],
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
        ("td_drift_deg", pa.float64()), ("bounces", pa.int64()),
        # Full-stop approach tasks (`rollout` configured): where the aircraft stopped.
        ("stop_along_m", pa.float64()), ("ground_roll_m", pa.float64()), ("rollout_max_cross_m", pa.float64()),
        # Takeoff task only (null otherwise): lift-off and climb-out as in envs/takeoff.py.
        ("climbed", pa.bool_()), ("takeoff_failure", pa.string()), ("liftoff_ground_roll_m", pa.float64()),
        ("liftoff_cas_mps", pa.float64()), ("liftoff_pitch_deg", pa.float64()), ("fifty_ft_distance_m", pa.float64()),
        ("ground_max_cross_m", pa.float64()), ("skips", pa.int64()),
        # Navigation task only (null otherwise): the route as in envs/navigation.py.
        ("route_completed", pa.bool_()), ("route_legs", pa.int64()), ("route_legs_done", pa.int64()),
        ("route_length_m", pa.float64()), ("route_time_s", pa.float64()), ("xtk_rms_m", pa.float64()), ("xtk_max_m", pa.float64()),
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
