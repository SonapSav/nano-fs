"""State stream protocol, version 1. One format for live flights and replays.

A frame carries exactly the columns of log schema v1 for one row (same names, SI
units), so a replayed log and a live run are indistinguishable to consumers.
Commands are null when no command follows the state (end of a log).

Server -> client messages (JSON):
  {"type": "hello", "protocol": 1, "source": "live" | "replay", "run_id", "aircraft",
   "sim_rate_hz", "frame_rate_hz", "duration_s" (null if unknown), "targets" (or null),
   "meta" (log metadata, replay only)}
  {"type": "frame", "row": {<log column>: value, ...}}
  {"type": "end", "reason": "finished" | "stopped" | "terminated:<why>"}
  {"type": "logs", "logs": [{"path", "run_id", "aircraft", "rows"}, ...]}
  {"type": "error", "message"}

Client -> server messages:
  {"type": "list"}
  {"type": "play", "source": "replay", "path": "<relative to the data dir>", "speed": 1.0}
  {"type": "play", "source": "live", "seed": 0, "speed": 1.0}
  {"type": "pause"} | {"type": "resume"} | {"type": "speed", "value": 2.0} | {"type": "stop"}
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
