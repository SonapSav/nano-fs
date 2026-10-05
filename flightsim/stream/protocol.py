"""State stream protocol, version 1. One format for live flights and replays.

A frame carries exactly the columns of log schema v1 for one row (same names, SI
units), so a replayed log and a live run are indistinguishable to consumers.
Commands are null when no command follows the state (end of a log).

Server -> client messages (JSON):
  {"type": "hello", "protocol": 1, "source": "live" | "manual" | "replay", "run_id", "aircraft",
   "sim_rate_hz", "frame_rate_hz", "duration_s" (null if unknown), "targets" (or null),
   "meta" (log metadata, replay only), "pilot" ("pid" | "lqr" | "human"; null for replay),
   "approach" (approach task: runway and glide path, see envs.approach.approach_geometry; else null)}
  {"type": "frame", "row": {<log column>: value, ...}}
  {"type": "end", "reason": "finished" | "landed" | "stopped" | "terminated:<why>", "landing"?: {...}}
      landing: the approach task's result (touchdown point, sink rate, ...), approach only
  {"type": "logs", "logs": [{"path", "group", "run_id", "aircraft", "rows", "duration_s", "seed",
                             "pilot", "mtime"}, ...]}
      group: "demos", "batch/<id>" or the top directory under the data dir.
  {"type": "error", "message"}
  {"type": "saved", "path"}   (a manual flight was written as a demonstration log)

Client -> server messages:
  {"type": "list"}
  {"type": "play", "source": "replay", "path": "<relative to the data dir>", "speed": 1.0, "start_s"?: 0.0}
  {"type": "play", "source": "live", "autopilot": "pid" | "lqr" (default "pid"), "seed": 0, "speed": 1.0}
  {"type": "play", "source": "manual", "conditions": "calm" | "windy" | "approach", "seed": 0, "record": true}
      (speed is capped at 1)
  {"type": "input", "elevator", "aileron", "rudder", "throttle", "flaps"?, "pitch_trim"?}
      Manual flights only. Stick and pedals in [-1, 1] relative to trim (elevator +
      = push, nose down; rudder + = nose left, the JSBSim convention), throttle and
      flaps in [0, 1], pitch trim in [-1, 1] (+ = nose down). Flaps and pitch trim
      only act if the manual task's action set includes them. Sampled and held at the
      environment's decision rate. This is the only message that reaches the physics,
      and only as a policy action.
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
