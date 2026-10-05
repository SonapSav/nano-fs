"""Run the stream server: python -m flightsim.stream [--port 8686]"""

import argparse
import asyncio
from pathlib import Path

from flightsim.config import load_raw
from flightsim.control.approach import load_approach_gains
from flightsim.control.autopilot import load_autopilot_gains
from flightsim.envs import load_env_config
from flightsim.stream.server import ServerConfig, run_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 inside Docker")
    parser.add_argument("--port", type=int, default=8686)
    parser.add_argument("--data-dir", default="data", help="logs available for replay")
    parser.add_argument("--env-config", default="configs/envs/altitude_heading_hold.yaml")
    parser.add_argument("--autopilot", default="configs/autopilot.yaml")
    parser.add_argument("--lqr", default="configs/lqr.yaml", help="LQR autopilot config")
    parser.add_argument("--approach-config", default="configs/envs/approach_landing.yaml", help="approach task for the approach autopilot")
    parser.add_argument("--approach-autopilot", default="configs/approach_autopilot.yaml", help="approach autopilot gains")
    parser.add_argument("--manual-config", default="configs/envs/manual.yaml", help="manual flight, calm air")
    parser.add_argument("--manual-wind-config", default="configs/envs/manual_wind.yaml", help="manual flight, wind and turbulence")
    parser.add_argument("--manual-approach-config", default="configs/envs/manual_approach.yaml", help="manual approach and landing")
    parser.add_argument("--frame-rate", type=float, default=30.0)
    args = parser.parse_args()

    cfg = ServerConfig(
        data_dir=Path(args.data_dir),
        env_cfg=load_env_config(args.env_config),
        gains=load_autopilot_gains(args.autopilot),
        frame_rate_hz=args.frame_rate,
        manual_env_cfgs={
            "calm": load_env_config(args.manual_config),
            "windy": load_env_config(args.manual_wind_config),
            "approach": load_env_config(args.manual_approach_config),
        },
        lqr_raw=load_raw(args.lqr),
        approach_env_cfg=load_env_config(args.approach_config),
        approach_gains=load_approach_gains(args.approach_autopilot),
    )
    # Design the LQR gain schedule now (or load it from data/cache/lqr), so the first
    # "watch the LQR" flight starts at once; designing takes ~15 s.
    print("LQR gain schedule: loading or designing...", flush=True)
    cfg.autopilot("lqr")
    print(f"viewer: http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}/")
    try:
        asyncio.run(run_server(cfg, args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
