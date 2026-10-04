"""Compare policies on the same seeded episodes of the altitude/heading-hold task."""

import argparse
import math
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from flightsim.control.autopilot import load_autopilot_gains
from flightsim.datalog import make_run_id, write_log
from flightsim.envs import AltitudeHeadingHoldEnv, load_env_config
from flightsim.envs.evaluate import run_episode
from flightsim.config import load_raw
from flightsim.control.lqr import GainSchedule
from flightsim.envs.policies import LQRPolicy, PIDPolicy, TrimHoldPolicy

METRICS = [
    ("episode_return", "return", "{:9.1f}"),
    ("alt_rms_m", "alt RMS m", "{:8.1f}"),
    ("heading_rms_deg", "hdg RMS deg", "{:8.1f}"),
    ("tas_rms_mps", "TAS RMS m/s", "{:8.2f}"),
    ("alt_settle_s", "alt settle s", "{:8.1f}"),
    ("heading_settle_s", "hdg settle s", "{:8.1f}"),
    ("alt_final_abs_m", "alt final m", "{:8.2f}"),
    ("heading_final_abs_deg", "hdg final deg", "{:8.2f}"),
    ("action_rate", "action rate/s", "{:8.3f}"),
    ("max_bank_deg", "max bank deg", "{:8.1f}"),
    ("max_load_factor", "max g", "{:8.2f}"),
    ("max_abs_climb_mps", "max climb m/s", "{:8.1f}"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-config", default="configs/envs/altitude_heading_hold.yaml")
    parser.add_argument("--autopilot", default="configs/autopilot.yaml")
    parser.add_argument("--lqr", default="configs/lqr.yaml")
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--log-dir", help="write each episode as <log-dir>/<policy>/<run_id>.parquet")
    args = parser.parse_args()

    cfg = load_env_config(args.env_config)
    env = AltitudeHeadingHoldEnv(cfg, record=bool(args.log_dir))
    schedule = GainSchedule.cached(cfg.aircraft, cfg.loading, load_raw(args.lqr), 1.0 / cfg.control_rate_hz)
    policies = [
        TrimHoldPolicy(),
        PIDPolicy(load_autopilot_gains(args.autopilot), cfg.control_rate_hz),
        LQRPolicy(schedule, cfg.control_rate_hz),
    ]
    seeds = range(args.first_seed, args.first_seed + args.episodes)
    print(f"env config hash {cfg.config_hash[:12]}, {args.episodes} episodes (seeds {seeds.start}..{seeds.stop - 1}), "
          f"{cfg.episode_s:.0f} s each at {cfg.control_rate_hz:.0f} Hz decisions")

    results = {}
    for policy in policies:
        t0 = time.perf_counter()
        episodes = []
        for seed in seeds:
            episodes.append(run_episode(env, policy, seed))
            if args.log_dir:
                path = Path(args.log_dir) / policy.name / f"{make_run_id(cfg.config_hash, seed)}.parquet"
                write_log(path, env.episode_result(), env.provenance())
        results[policy.name] = episodes
        print(f"  {policy.name}: {time.perf_counter() - t0:.1f} s wall")

    print(f"\n{'metric':15s}" + "".join(f"{p.name + ' mean':>16s}{p.name + ' worst':>16s}" for p in policies))
    for key, label, fmt in METRICS:
        row = f"{label:15s}"
        for p in policies:
            values = np.array([asdict(m)[key] for m in results[p.name]])
            worst = values.min() if key == "episode_return" else values.max()
            mean = "inf" if np.isinf(values).any() else fmt.format(values.mean())
            row += f"{mean:>16s}{('inf' if math.isinf(worst) else fmt.format(worst)):>16s}"
        print(row)
    for p in policies:
        ended = [m for m in results[p.name] if m.termination_reason]
        never = sum(math.isinf(m.alt_settle_s) or math.isinf(m.heading_settle_s) for m in results[p.name])
        print(f"{p.name}: {len(ended)} terminated early {[(m.seed, m.termination_reason) for m in ended]}, "
              f"{never} never settled (alt within 10 m and heading within 3 deg)")


if __name__ == "__main__":
    main()
