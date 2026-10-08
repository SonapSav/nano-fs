"""State stream protocol, version 1. One format for live flights and replays.

A frame carries exactly the columns of the log schema (v2) for one row (same names, SI
units), so a replayed log and a live run are indistinguishable to consumers.
Commands are null when no command follows the state (end of a log).

Server -> client messages (JSON):
  {"type": "hello", "protocol": 1, "source": "live" | "manual" | "replay", "run_id", "aircraft",
   "sim_rate_hz", "frame_rate_hz", "duration_s" (null if unknown), "targets" (or null),
   "meta" (log metadata, replay only), "pilot" ("pid" | "lqr" | "human"; null for replay),
   "approach" (approach task: runway and glide path, see envs.approach.approach_geometry; else null),
   "takeoff" (takeoff task: runway and wind, see envs.takeoff.TakeoffEnv.runway_info; else null),
   "pattern" (circuit task: the circuit autopilot's pattern height, crosswind turn margin and
              downwind offset, for drawing; else null),
   "visual" ({"time_of_day", "visibility", "clouds", "cloud_seed"}: the flight's viewer
             conditions, envs visual_conditions; null when unknown: viewer defaults),
   "world" ({"model": "wgs84" | "sphere", "origin_lat_deg", "origin_lon_deg"}: how the
            flight's latitude/longitude map to metres, world/geo.py and viewer/geo.js;
            null when unknown: the original sphere),
   "route" (navigation task: {"name", "start": {north_m, east_m}, "waypoints": [{name, north_m,
            east_m, fly_over}], "turn_bank_deg", "geodesy"}, envs.navigation; the viewer
            sequences it with nav.js; else null)}
  {"type": "frame", "row": {<log column>: value, ...}}
  {"type": "preview", "id", "hello": {<hello message>}, "row": {<log column>: value, ...}}
      Answer to a client "preview": the flight's hello and its starting state (live and
      manual: the reset state, commands null; replay: the first row), nothing streamed.
  {"type": "end", "reason": "finished" | "landed" | "climbed" | "stopped" | "terminated:<why>",
   "landing"?: {...}, "takeoff"?: {...}}
      landing: the approach task's result (touchdown point, sink rate, ...), approach only
      takeoff: the takeoff task's result (lift-off, 50 ft point, ...), takeoff only
  {"type": "logs", "logs": [{"path", "group", "run_id", "aircraft", "rows", "duration_s", "seed",
                             "pilot", "mtime", "task", "windy", "hud", "result"?}, ...]}
      group: "demos", "batch/<id>" or the top directory under the data dir.
      task: "free" | "approach" | "takeoff" | "circuit" (null if unknown); windy: flew in
      wind; hud: the HUD was in view at some time (null: not recorded).
      result (when the server computes results and has it; never for batches): what the
      task decided, from re-flying the log (stream/results.py). Sent again, with more
      results, while they are being computed.
  {"type": "error", "message"}
  {"type": "saved", "path"}   (a manual flight was written as a demonstration log)

Client -> server messages:
  {"type": "list"}
  {"type": "play", "source": "replay", "path": "<relative to the data dir>", "speed": 1.0, "start_s"?: 0.0}
  {"type": "play", "source": "live", "autopilot": "pid" | "lqr" | "approach" | "takeoff" | "circuit" | "route" (default "pid"), "seed": 0, "speed": 1.0}
  {"type": "play", "source": "manual", "conditions": "calm" | "windy" | "approach" | "approach_crosswind" | "takeoff"
   | "takeoff_crosswind" | "circuit" | "circuit_crosswind" | "route" | "route_wind", "seed": 0, "record": true, "aids"?: {"hud": false}}
      (speed is capped at 1)
  {"type": "preview", "id"?, <the fields of a "play" message>}
      The starting state of that flight without starting it (shown when a flight is
      selected). Ignored while a flight streams; a newer preview or a play supersedes one
      still being prepared. "id" is echoed back.
  {"type": "input", "elevator", "aileron", "rudder", "throttle", "flaps"?, "pitch_trim"?, "brake"?}
      Manual flights only. Stick and pedals in [-1, 1] relative to trim (elevator +
      = push, nose down; rudder + = nose left, the JSBSim convention), throttle and
      flaps in [0, 1], pitch trim in [-1, 1] (+ = nose down), brake in [0, 1] (both
      main wheels; omitted = released). Flaps, pitch trim and brake only act if the
      manual task's action set includes them. Sampled and held at the
      environment's decision rate. This is the only message that reaches the physics,
      and only as a policy action.
  {"type": "aids", "hud": true | false}
      Manual flights only: the HUD came into or left the pilot's view (and "aids" in the
      play message: in view at the start). Recorded in the demonstration's metadata
      (flightsim.pilot_aids); never reaches the physics.
  {"type": "pause"} | {"type": "resume"} | {"type": "speed", "value": 2.0} | {"type": "stop"}
  {"type": "seek", "t_s": 42.0}
      Replays only: continue from the first row at or after t_s; while paused, the
      frame at the new position is sent and playback stays paused.
"""

import json
import math

from flightsim.core import Controls, State
from flightsim.datalog import schema as S

PROTOCOL_VERSION = 1


def frame_row(step: int, run_id: str, seed: int, config_hash: str, state: State, controls: Controls | None) -> dict:
    """A frame row with the same columns, order and units as log schema v1."""
    row = {"step": step, "run_id": run_id, "seed": seed, "config_hash": config_hash}
    for name in S.STATE_COLUMNS:
        row[name] = getattr(state, name)
    for field, name in S.COMMAND_COLUMNS.items():
        row[name] = None if controls is None else getattr(controls, field)
    return row


def _finite(v):
    # JSON has no NaN/inf; send null rather than invalid JSON.
    return None if isinstance(v, float) and not math.isfinite(v) else v


def encode(message: dict) -> str:
    if message.get("type") == "frame":
        message = {**message, "row": {k: _finite(v) for k, v in message["row"].items()}}
    return json.dumps(message, allow_nan=False, separators=(",", ":"))
