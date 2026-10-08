"""Run the stream server: python -m flightsim.stream [--port 8686]"""

import argparse
import asyncio
from pathlib import Path

from flightsim.config import load_raw
from flightsim.control.approach import load_approach_gains
from flightsim.control.circuit import circuit_gains_from_raw, load_circuit_raw
from flightsim.control.takeoff import load_takeoff_gains
from flightsim.control.autopilot import load_autopilot_gains
from flightsim.control.route import load_route_raw, route_gains_from_raw
from flightsim.envs import load_env_config
from flightsim.stream.server import ServerConfig, run_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 inside Docker")
    parser.add_argument("--port", type=int, default=8686)
    parser.add_argument("--data-dir", default="data", help="logs available for replay")
    parser.add_argument("--no-flight-results", action="store_true", help="do not re-fly past flights for their results")
    parser.add_argument("--env-config", default="configs/envs/altitude_heading_hold.yaml")
    parser.add_argument("--autopilot", default="configs/autopilot.yaml")
    parser.add_argument("--lqr", default="configs/lqr.yaml", help="LQR autopilot config")
    parser.add_argument("--approach-config", default="configs/envs/approach_landing.yaml", help="approach task for the approach autopilot")
    parser.add_argument("--approach-autopilot", default="configs/approach_autopilot.yaml", help="approach autopilot gains")
    parser.add_argument("--takeoff-config", default="configs/envs/takeoff_crosswind.yaml", help="takeoff task for the takeoff autopilot")
    parser.add_argument("--takeoff-autopilot", default="configs/takeoff_autopilot.yaml", help="takeoff autopilot gains")
    parser.add_argument("--circuit-config", default="configs/envs/circuit_crosswind.yaml", help="circuit task for the circuit autopilot")
    parser.add_argument("--circuit-autopilot", default="configs/circuit_autopilot.yaml", help="circuit autopilot")
    parser.add_argument("--manual-circuit-config", default="configs/envs/circuit.yaml", help="manual circuit")
    parser.add_argument("--manual-circuit-crosswind-config", default="configs/envs/circuit_crosswind.yaml", help="manual circuit, wind")
    parser.add_argument("--route-config", default="configs/envs/navigation_wind.yaml", help="navigation task for the route autopilot")
    parser.add_argument("--route-autopilot", default="configs/route_autopilot.yaml", help="route autopilot")
    parser.add_argument("--manual-route-config", default="configs/envs/manual_route.yaml", help="manual route")
    parser.add_argument("--manual-route-wind-config", default="configs/envs/manual_route_wind.yaml", help="manual route, wind")
    parser.add_argument("--manual-config", default="configs/envs/manual.yaml", help="manual flight, calm air")
    parser.add_argument("--manual-wind-config", default="configs/envs/manual_wind.yaml", help="manual flight, wind and turbulence")
    parser.add_argument("--manual-approach-config", default="configs/envs/manual_approach.yaml", help="manual approach and landing")
    parser.add_argument("--manual-crosswind-config", default="configs/envs/manual_approach_crosswind.yaml", help="manual approach, wind")
    parser.add_argument("--manual-takeoff-config", default="configs/envs/manual_takeoff.yaml", help="manual takeoff")
    parser.add_argument("--manual-takeoff-crosswind-config", default="configs/envs/manual_takeoff_crosswind.yaml", help="manual takeoff, wind")
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
            "approach_crosswind": load_env_config(args.manual_crosswind_config),
            "takeoff": load_env_config(args.manual_takeoff_config),
            "takeoff_crosswind": load_env_config(args.manual_takeoff_crosswind_config),
            "circuit": load_env_config(args.manual_circuit_config),
            "circuit_crosswind": load_env_config(args.manual_circuit_crosswind_config),
            "route": load_env_config(args.manual_route_config),
            "route_wind": load_env_config(args.manual_route_wind_config),
        },
        lqr_raw=load_raw(args.lqr),
        approach_env_cfg=load_env_config(args.approach_config),
        approach_gains=load_approach_gains(args.approach_autopilot),
        takeoff_env_cfg=load_env_config(args.takeoff_config),
        takeoff_gains=load_takeoff_gains(args.takeoff_autopilot),
        circuit_env_cfg=load_env_config(args.circuit_config),
        circuit_gains=circuit_gains_from_raw(load_circuit_raw(args.circuit_autopilot)),
        route_env_cfg=load_env_config(args.route_config),
        route_gains=route_gains_from_raw(load_route_raw(args.route_autopilot)),
        flight_results=not args.no_flight_results,
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
