"""Run the stream server: python -m flightsim.stream [--port 8686]"""

import argparse
import asyncio
from pathlib import Path

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
    parser.add_argument("--frame-rate", type=float, default=30.0)
    args = parser.parse_args()

    cfg = ServerConfig(
        data_dir=Path(args.data_dir),
        env_cfg=load_env_config(args.env_config),
        gains=load_autopilot_gains(args.autopilot),
        frame_rate_hz=args.frame_rate,
    )
    print(f"viewer: http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}/")
    try:
        asyncio.run(run_server(cfg, args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
