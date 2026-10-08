"""Results of recorded flights, for the viewer's Past flights list.

A log keeps the states and commands, not what the task decided (landed, nose wheel first,
climbed out...). To get that, the log's episode is flown again from its config and seed
with the recorded commands (open loop, `env.refly`) and the task's own checks are read.
The re-flight must reproduce the log (same physics and code); a log that does not (an
older version) gets outcome "unknown".

Outcomes: "landed" (with touchdown and rollout numbers), "climbed" (takeoff), "completed"
(free flight to the end; a route to its last waypoint, with legs and cross-track error),
"failed" (with the task's reason), "stopped" (ended early by the pilot; a touchdown or the
legs flown, if any, are included), "unknown".

Results are cached on disk (data/cache/flight_results.json) by path, size and modification
time, plus RESULTS_VERSION: bump it when the tasks' rules change.
"""

import json
import math
from pathlib import Path

from flightsim.core import Controls
from flightsim.datalog import read_log
from flightsim.envs import make_env
from flightsim.envs.config import env_config_from_raw

RESULTS_VERSION = 2  # 2: navigation task (routes)
CACHE_NAME = "flight_results.json"
MPS_TO_FPM = 196.850394
MPS_TO_KT = 1.943844


def _touchdown(td: dict | None) -> dict | None:
    if not td:
        return None
    return {
        "along_m": round(td["along_m"], 1), "cross_m": round(td["cross_m"], 1), "sink_fpm": round(td["sink_mps"] * MPS_TO_FPM),
        "kcas": round(td["cas_mps"] * MPS_TO_KT, 1), "pitch_deg": round(td["pitch_deg"], 1), "in_zone": bool(td["in_zone"]),
    }  # fmt: skip


def flight_result(path: str | Path) -> dict:
    """The result of one log (see the module docstring); never raises."""
    try:
        table, meta = read_log(path)
        raw = json.loads(meta["flightsim.config_json"])
        env = make_env(env_config_from_raw(raw))
        cols = {c: table.column(f"cmd_{c}_norm").to_pylist() for c in ("elevator", "aileron", "rudder", "throttle", "mixture", "flaps", "pitch_trim", "brake")}
        n = table.num_rows
        controls = [Controls(**{k: cols[k][i] for k in cols}) for i in range(n - 1)]
        states = env.refly(int(table.column("seed")[0].as_py()), controls)
    except Exception as e:  # unreadable, older schema or config, aircraft missing...
        return {"outcome": "unknown", "why": f"could not re-fly: {type(e).__name__}"}
    last = {k: table.column(k)[n - 1].as_py() for k in ("lat_rad", "lon_rad", "alt_msl_m")}
    s = states[-1]
    if not all(math.isclose(getattr(s, k), v, rel_tol=0, abs_tol=1e-9 if k != "alt_msl_m" else 1e-6) for k, v in last.items()):
        return {"outcome": "unknown", "why": "recorded with another version of the simulator"}

    reason = getattr(env, "failure", None) or env._termination_reason()
    landing = env.landing_summary() if hasattr(env, "landing_summary") else None
    td = _touchdown(landing.get("touchdown")) if landing else None
    route = None
    if hasattr(env, "route_summary"):
        rs = env.route_summary()
        route = {"legs": rs["legs"], "legs_done": rs["legs_done"], "xtk_max_m": round(rs["xtk_max_m"], 1)}
    if reason:
        out = {"outcome": "failed", "reason": reason}
        if td:
            out["touchdown"] = td
        if route:
            out["route"] = route
        return out
    if landing is not None:
        if landing["landed"]:
            r = landing.get("rollout") or {}
            return {"outcome": "landed", "touchdown": td, "bounces": landing.get("bounces", 0),
                    "stop_along_m": round(r["stop_along_m"]) if "stop_along_m" in r else None}  # fmt: skip
        return {"outcome": "stopped", **({"touchdown": td} if td else {})}
    if route is not None:
        return {"outcome": "completed" if env.route_done else "stopped", "route": route}
    if hasattr(env, "takeoff_summary"):
        return {"outcome": "climbed"} if env.takeoff_summary()["climbed"] else {"outcome": "stopped"}
    episode_s = float(raw.get("episode_s", 0))
    return {"outcome": "completed"} if s.t_s >= episode_s - 1e-6 else {"outcome": "stopped"}


class ResultCache:
    """Results by log, kept in data/cache/flight_results.json."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.file = self.data_dir / "cache" / CACHE_NAME
        try:
            self.results = json.loads(self.file.read_text())
        except (OSError, ValueError):
            self.results = {}

    def key(self, rel_path: str) -> str | None:
        try:
            st = (self.data_dir / rel_path).stat()
        except OSError:
            return None
        return f"{rel_path}|{st.st_size}|{st.st_mtime_ns}|v{RESULTS_VERSION}"

    def get(self, rel_path: str) -> dict | None:
        k = self.key(rel_path)
        return self.results.get(k) if k else None

    def put(self, rel_path: str, result: dict) -> None:
        k = self.key(rel_path)
        if not k:
            return
        # Drop older entries of the same log (re-recorded, or older RESULTS_VERSION).
        for old in [o for o in self.results if o.startswith(rel_path + "|")]:
            del self.results[old]
        self.results[k] = result
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.results, sort_keys=True))
            tmp.replace(self.file)
        except OSError:
            pass  # not persisted; still served from memory
