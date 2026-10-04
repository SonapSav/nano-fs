"""Run a headless flight from a config file, write its Parquet log, and print a physical-sanity summary."""

import argparse
import math
import time
from pathlib import Path

from flightsim.config import load_config
from flightsim.datalog import make_run_id, write_log
from flightsim.runner import run

MPS_TO_KTS = 3600.0 / 1852.0
M_TO_FT = 1.0 / 0.3048
W_TO_HP = 1.0 / 745.69987158227


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default="configs/cruise.yaml")
    parser.add_argument("--out-dir", default="data", help="log is written to <out-dir>/<run_id>.parquet")
    args = parser.parse_args()

    cfg = load_config(args.config)
    t0 = time.perf_counter()
    result = run(cfg)
    wall_s = time.perf_counter() - t0
    log_path = write_log(Path(args.out_dir) / f"{make_run_id(cfg.config_hash, cfg.seed)}.parquet", result, cfg)

    s0, s1 = result.states[0], result.states[-1]
    trim = result.trim
    states = result.states
    print(f"config {args.config}  hash {cfg.config_hash[:12]}  seed {cfg.seed}  jsbsim {result.jsbsim_version}")
    print(f"simulated {s1.t_s:.1f} s in {wall_s:.2f} s wall ({s1.t_s / wall_s:.0f}x real time), {len(states)} states")
    print(f"log {log_path} ({log_path.stat().st_size / 1e6:.1f} MB)")
    print()
    print("Trim:")
    print(f"  throttle {trim.throttle:.3f}  pitch_trim {trim.pitch_trim:+.3f}  aileron {trim.aileron:+.4f}  rudder {trim.rudder:+.4f}")
    print(f"  alpha {math.degrees(s0.alpha_rad):.2f} deg  pitch {math.degrees(s0.theta_rad):.2f} deg  "
          f"elevator {math.degrees(s0.elevator_pos_rad):+.2f} deg")
    print(f"  TAS {s0.tas_mps * MPS_TO_KTS:.1f} kt  CAS {s0.cas_mps * MPS_TO_KTS:.1f} kt  "
          f"power {s0.engine_power_w * W_TO_HP:.1f} hp  rpm {s0.engine_rpm:.0f}  mass {s0.mass_kg:.1f} kg")
    print()
    print(f"Over the run ({'start':>9} -> {'end':>9}, min .. max):")
    rows = [
        ("altitude ft", lambda s: s.alt_msl_m * M_TO_FT),
        ("TAS kt", lambda s: s.tas_mps * MPS_TO_KTS),
        ("heading deg", lambda s: math.degrees(s.psi_rad)),
        ("bank deg", lambda s: math.degrees(s.phi_rad)),
        ("pitch deg", lambda s: math.degrees(s.theta_rad)),
        ("vert speed fpm", lambda s: -s.v_down_mps * M_TO_FT * 60),
        ("load factor g", lambda s: -s.az_mps2 / 9.80665),
        ("fuel kg", lambda s: s.fuel_mass_kg),
    ]
    for name, f in rows:
        values = [f(s) for s in states]
        print(f"  {name:15s} {values[0]:9.3f} -> {values[-1]:9.3f}   {min(values):9.3f} .. {max(values):9.3f}")


if __name__ == "__main__":
    main()
