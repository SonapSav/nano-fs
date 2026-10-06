"""Run many seeded episodes of the altitude/heading-hold task in parallel.

Example:
  uv run python scripts/batch_run.py --seeds 0:1000 --workers 12
  uv run python scripts/batch_run.py --set wind.steady_speed_mps=[5,15] --policy trim_hold --logs
"""

import argparse
import os
import sys
import time

import yaml

from flightsim.batch import POLICIES, run_batch, summarize
from flightsim.config import load_raw


def parse_seeds(text: str) -> list[int]:
    if ":" in text:
        start, stop = (int(x) for x in text.split(":"))
        return list(range(start, stop))
    return [int(x) for x in text.split(",")]


def parse_overrides(items: list[str]) -> dict:
    overrides = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--set expects key=value, got {item!r}")
        overrides[key] = yaml.safe_load(value)
    return overrides


def fmt(v: float) -> str:
    return "inf" if v == float("inf") else f"{v:.1f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-config", default="configs/envs/altitude_heading_hold_wind.yaml")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override an env config value (dotted key, YAML value); part of the config hash")
    parser.add_argument("--policy", choices=POLICIES, default="pid")
    parser.add_argument("--autopilot", default="configs/autopilot.yaml", help="PID gains (policy pid)")
    parser.add_argument("--lqr", default="configs/lqr.yaml", help="LQR config (policy lqr)")
    parser.add_argument("--approach-autopilot", default="configs/approach_autopilot.yaml", help="approach autopilot gains (policy approach)")
    parser.add_argument("--takeoff-autopilot", default="configs/takeoff_autopilot.yaml", help="takeoff autopilot gains (policy takeoff)")
    parser.add_argument("--rl-model", help="trained model directory, e.g. data/rl/<run_id>/best (policy rl)")
    parser.add_argument("--policy-set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a policy config value; part of the batch id")
    parser.add_argument("--seeds", default="0:100", help="start:stop or a comma-separated list")
    # Default: physical cores. Measured on a 6-core/12-thread Ryzen 5 5500U: 6 workers run
    # 4.6x faster than 1, and 12 are no faster than 6 (SMT does not help this workload).
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--logs", action="store_true", help="also write each episode's full log")
    parser.add_argument("--out-dir", default="data/batch")
    args = parser.parse_args()

    env_raw = load_raw(args.env_config, parse_overrides(args.set))
    if args.policy == "rl":
        if not args.rl_model:
            raise SystemExit("--policy rl needs --rl-model <model directory>")
        from flightsim.rl.policy import model_identity

        policy_raw = model_identity(args.rl_model)
    else:
        policy_path = {"pid": args.autopilot, "lqr": args.lqr, "approach": args.approach_autopilot, "takeoff": args.takeoff_autopilot}.get(args.policy)
        policy_raw = load_raw(policy_path, parse_overrides(args.policy_set)) if policy_path else None
    seeds = parse_seeds(args.seeds)

    def progress(done: int, total: int) -> None:
        if done == total or done % max(1, total // 20) == 0:
            print(f"\r  {done}/{total} episodes", end="", file=sys.stderr, flush=True)

    t0 = time.perf_counter()
    batch_dir, manifest, table = run_batch(env_raw, args.policy, policy_raw, seeds, args.out_dir, args.workers, args.logs, progress)
    wall = time.perf_counter() - t0
    sim_s = sum(table.column("duration_s").to_pylist())
    print(file=sys.stderr)
    print(f"batch {manifest['batch_id']}: {len(seeds)} episodes, policy {args.policy}, env config {manifest['env_config_hash'][:12]}")
    print(f"{wall:.1f} s wall with {args.workers} workers, {sim_s / wall:.0f}x real time overall")
    print(f"wrote {batch_dir}/episodes.parquet and manifest.json" + (" plus logs/" if args.logs else ""))
    print()
    print(f"{'group':10s}{'episodes':>9s}{'ended':>7s}{'unsettled':>10s}   {'return mean/median/worst':>30s}   {'alt RMS m':>17s}   {'hdg RMS deg':>17s}")
    for g in summarize(table):
        r, a, h = g["episode_return"], g["alt_rms_m"], g["heading_rms_deg"]
        print(f"{g['group']:10s}{g['episodes']:9d}{g['terminated']:7d}{g['never_settled']:10d}   "
              f"{fmt(r[0]):>10s}{fmt(r[1]):>10s}{fmt(r[2]):>10s}   {fmt(a[0]):>5s}{fmt(a[1]):>6s}{fmt(a[2]):>6s}   "
              f"{fmt(h[0]):>5s}{fmt(h[1]):>6s}{fmt(h[2]):>6s}")
    rows = table.to_pylist()
    if rows and rows[0]["landed"] is not None:  # approach task: the landings
        landed = [r for r in rows if r["landed"]]
        fails: dict[str, int] = {}
        for r in rows:
            if not r["landed"]:
                why = r["landing_failure"] or r["termination_reason"] or "timeout"
                fails[why] = fails.get(why, 0) + 1
        print(f"\nlandings: {len(landed)}/{len(rows)}" + (f"; failures {fails}" if fails else ""))
        if landed:
            def span(key, scale=1.0):
                v = [r[key] * scale for r in landed]
                return f"mean {sum(v) / len(v):.1f}, range {min(v):.1f} .. {max(v):.1f}"
            print(f"  touchdown past threshold m: {span('td_along_m')}")
            print(f"  centreline offset m:        {span('td_cross_m')}")
            print(f"  sink rate ft/min:           {span('td_sink_mps', 196.850394)}")
            print(f"  airspeed KCAS:              {span('td_cas_mps', 1.943844)}")
            print(f"  pitch deg:                  {span('td_pitch_deg')}")
            print(f"  crab at touchdown deg:      {span('td_drift_deg')}")
            print(f"  bounces: {sum(r['bounces'] for r in landed)}")
            if all(r["ground_roll_m"] is not None for r in landed):
                print(f"  ground roll m:              {span('ground_roll_m')}")
                print(f"  stopped past threshold m:   {span('stop_along_m')}")
                print(f"  rollout max offset m:       {span('rollout_max_cross_m')}")

    if rows and rows[0]["climbed"] is not None:  # takeoff task: the climb-outs
        climbed = [r for r in rows if r["climbed"]]
        fails = {}
        for r in rows:
            if not r["climbed"]:
                why = r["takeoff_failure"] or r["termination_reason"] or "timeout"
                fails[why] = fails.get(why, 0) + 1
        print(f"\ntakeoffs: {len(climbed)}/{len(rows)} climbed" + (f"; failures {fails}" if fails else ""))
        if climbed:
            def span(key, scale=1.0):
                v = [r[key] * scale for r in climbed]
                return f"mean {sum(v) / len(v):.1f}, range {min(v):.1f} .. {max(v):.1f}"
            print(f"  ground roll m:              {span('liftoff_ground_roll_m')}")
            print(f"  lift-off KCAS:              {span('liftoff_cas_mps', 1.943844)}")
            print(f"  lift-off pitch deg:         {span('liftoff_pitch_deg')}")
            print(f"  to 50 ft m:                 {span('fifty_ft_distance_m')}")
            print(f"  max centreline offset m:    {span('ground_max_cross_m')}")
            print(f"  skips: {sum(r['skips'] for r in rows)}")

if __name__ == "__main__":
    main()
