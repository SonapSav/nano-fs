"""Frame sources for the state stream. Each yields (t_s, row) at the simulation rate;
the server decimates and paces them. Sources never see wall-clock time."""

import bisect
import hashlib
import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from flightsim.control.autopilot import AutopilotGains
from flightsim.control.manual import HumanPolicy
from flightsim.datalog import make_run_id, read_log, write_log
from flightsim.envs import EnvConfig, make_env
from flightsim.envs.approach import approach_geometry
from flightsim.envs.config import env_config_from_raw
from flightsim.envs.policies import PIDPolicy
from flightsim.stream.protocol import frame_row


@dataclass
class Source:
    source: str
    run_id: str
    aircraft: str
    sim_rate_hz: float
    duration_s: float | None
    targets: dict | None
    meta: dict = field(default_factory=dict)
    end_reason: str = "finished"
    landing: dict | None = None  # approach task: the landing result, sent with the end message
    approach: dict | None = None  # approach task: runway and glide path (envs.approach.approach_geometry)
    takeoff: dict | None = None  # takeoff task: runway and wind (envs.takeoff.TakeoffEnv.runway_info)
    takeoff_result: dict | None = None  # takeoff task: the result, sent with the end message
    visual: dict | None = None  # viewer conditions (envs: visual_conditions); None: viewer defaults
    pilot_name: str | None = None  # who flies a live flight: "pid", "lqr" or "human"

    def frames(self) -> Iterator[tuple[float, dict]]:
        raise NotImplementedError

    def first_frame(self) -> dict:
        """The state the flight starts from, without running it (previews)."""
        raise NotImplementedError


def _logged_episode(meta: dict, seed: int | None):
    """The task environment of a log, reset with its seed (None if the config cannot be
    read, e.g. logs from older code). Replays use it for what the log does not carry:
    targets, runway and wind."""
    try:
        raw = json.loads(meta.get("flightsim.config_json", "null"))
        if not raw or seed is None:
            return None
        env = make_env(env_config_from_raw(raw))
        env.reset(seed=seed)
        return env
    except (ValueError, KeyError, TypeError):
        return None


def _logged_approach(meta: dict, seed: int | None, env=None) -> dict | None:
    """Approach geometry and wind of an approach (or circuit) log, None for other tasks."""
    try:
        raw = json.loads(meta.get("flightsim.config_json", "null"))
    except ValueError:
        return None
    if not raw or "approach" not in raw:
        return None
    if env is None:
        try:
            return {**approach_geometry(env_config_from_raw(raw)), "wind": None}
        except (ValueError, KeyError, TypeError):
            return None
    return env.approach_info()


class ReplaySource(Source):
    def __init__(self, path: Path):
        table, meta = read_log(path)
        self._rows = table.to_pylist()
        self._times = [row["t_s"] for row in self._rows]
        self._next = 0
        duration = self._times[-1] if self._rows else 0.0
        rate = (len(self._rows) - 1) / duration if duration > 0 else 0.0
        super().__init__("replay", meta["flightsim.run_id"], meta["flightsim.aircraft"], round(rate, 6), duration, None, meta)
        env = _logged_episode(meta, self._rows[0]["seed"] if self._rows else None)
        self.approach = _logged_approach(meta, self._rows[0]["seed"] if self._rows else None, env)
        self.takeoff = env.runway_info() if env is not None and hasattr(env, "runway_info") else None
        if env is not None:
            self.visual = env.visual_conditions()
            t = env.targets
            self.targets = {"alt_msl_m": t.alt_msl_m, "heading_rad": t.heading_rad, "tas_mps": t.tas_mps}

    def seek(self, t_s: float) -> None:
        """Continue from the first row at or after t_s (clamped to the log), also while
        frames() is being iterated."""
        self._next = min(bisect.bisect_left(self._times, float(t_s) - 1e-9), max(0, len(self._rows) - 1))

    def first_frame(self) -> dict:
        return self._rows[min(self._next, len(self._rows) - 1)]

    def frames(self) -> Iterator[tuple[float, dict]]:
        while self._next < len(self._rows):
            row = self._rows[self._next]
            self._next += 1
            yield row["t_s"], row


class LiveSource(Source):
    """Flies one episode of the altitude/heading task with a policy (the PID autopilot
    unless given another, e.g. the LQR autopilot or a human pilot)."""

    max_speed = math.inf

    def __init__(self, env_cfg: EnvConfig, gains: AutopilotGains | None, seed: int, policy=None, source: str = "live"):
        self._env = make_env(env_cfg, record=True)
        self._policy = policy or PIDPolicy(gains, env_cfg.control_rate_hz)
        self._seed = seed
        self._obs, self._info = self._env.reset(seed=seed)
        self._policy.reset(self._info)
        t = self._env.targets
        targets = {"alt_msl_m": t.alt_msl_m, "heading_rad": t.heading_rad, "tas_mps": t.tas_mps}
        run_id = make_run_id(env_cfg.config_hash, seed)
        super().__init__(source, run_id, env_cfg.aircraft, env_cfg.sim_rate_hz, env_cfg.episode_s, targets)
        self.pilot_name = "human" if source == "manual" else self._policy.name
        self.approach = self._env.approach_info() if hasattr(self._env, "approach_info") else None
        self.takeoff = self._env.runway_info() if hasattr(self._env, "runway_info") else None
        self.visual = self._env.visual_conditions()
        self._config_hash = env_cfg.config_hash

    def first_frame(self) -> dict:
        """The reset state; its commands are null (the policy has not chosen any yet)."""
        states, _ = self._env.recorded
        return frame_row(0, self.run_id, self._seed, self._config_hash, states[0], None)

    def frames(self) -> Iterator[tuple[float, dict]]:
        env, emitted = self._env, 0
        while True:
            states, controls = env.recorded
            done = False
            if emitted >= len(states) - 1:  # need the next state before a command is known
                action = self._policy(self._obs, self._info)
                self._obs, _, terminated, truncated, self._info = env.step(action)
                if terminated:
                    self.end_reason = f"terminated:{self._info['termination_reason']}"
                if "landing" in self._info:
                    self.landing = self._info["landing"]
                    if truncated and self.landing["landed"]:
                        self.end_reason = "landed"
                if "takeoff" in self._info:
                    self.takeoff_result = self._info["takeoff"]
                    if truncated and self.takeoff_result["climbed"]:
                        self.end_reason = "climbed"
                done = terminated or truncated
                states, controls = env.recorded
            while emitted < len(states) - 1:
                s = states[emitted]
                yield s.t_s, frame_row(emitted, self.run_id, self._seed, self._config_hash, s, controls[emitted])
                emitted += 1
            if done:
                s = states[-1]
                yield s.t_s, frame_row(emitted, self.run_id, self._seed, self._config_hash, s, None)
                return


class ManualSource(LiveSource):
    """A human flies the task episode in real time; the flight is kept as a demonstration,
    with the pilot aids the viewer reported (when the HUD was in view)."""

    max_speed = 1.0
    min_save_s = 5.0

    def __init__(self, env_cfg: EnvConfig, seed: int, hud: bool = False):
        self.pilot = HumanPolicy()
        super().__init__(env_cfg, None, seed, policy=self.pilot, source="manual")
        self._hud_intervals: list[list[float]] = []
        self._hud_since: float | None = None
        self.set_hud(hud)

    def _now_s(self) -> float:
        states, _ = self._env.recorded
        return states[-1].t_s

    def set_hud(self, on: bool) -> None:
        """The viewer's HUD came into view (on) or left it, at the current simulation time."""
        if on and self._hud_since is None:
            self._hud_since = self._now_s()
        elif not on and self._hud_since is not None:
            self._hud_intervals.append([self._hud_since, self._now_s()])
            self._hud_since = None

    def pilot_aids(self) -> dict:
        """{"hud": [[t_on_s, t_off_s], ...]} up to now (an interval still open ends now)."""
        hud = [*self._hud_intervals] + ([[self._hud_since, self._now_s()]] if self._hud_since is not None else [])
        return {"hud": hud}

    def demo_run_id(self) -> str:
        """Unique per flown input sequence: the same seed flown differently gets a new id."""
        _, controls = self._env.recorded
        h = hashlib.sha256(np.array([[u.elevator, u.aileron, u.rudder, u.throttle] for u in controls]).tobytes())
        return f"{make_run_id(self._env.cfg.config_hash, self._seed)}-m{h.hexdigest()[:8]}"

    def save(self, data_dir: Path) -> Path | None:
        """Write the flight so far to <data_dir>/demos/<run_id>.parquet (if long enough)."""
        states, _ = self._env.recorded
        if states[-1].t_s < self.min_save_s:
            return None
        run_id = self.demo_run_id()
        path = data_dir / "demos" / f"{run_id}.parquet"
        provenance = replace(self._env.provenance(run_id=run_id, pilot="human"), pilot_aids=self.pilot_aids())
        return write_log(path, self._env.episode_result(), provenance)


_log_info_cache: dict[tuple[str, int, int], dict | None] = {}


def _log_info(path: Path) -> dict | None:
    """Summary of one log file, or None if it is not a flightsim log. Cached by path,
    modification time and size, so listing thousands of batch logs stays cheap."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _log_info_cache:
        info = None
        try:
            md = pq.read_metadata(path)  # footer only: key-value metadata, row counts, statistics
            meta = {k.decode(): v.decode() for k, v in (md.metadata or {}).items()}
            if "flightsim.run_id" in meta:
                seed = re.search(r"-s(\d+)", meta["flightsim.run_id"])
                task, windy = _task_of(meta.get("flightsim.config_json"))
                info = {
                    "run_id": meta["flightsim.run_id"],
                    "task": task,
                    "windy": windy,
                    "hud": _hud_used(meta.get("flightsim.pilot_aids")),
                    "aircraft": meta.get("flightsim.aircraft"),
                    "rows": md.num_rows,
                    "duration_s": _last_time(path, md),
                    "seed": int(seed.group(1)) if seed else None,
                    "pilot": meta.get("flightsim.pilot"),
                    "mtime": st.st_mtime,
                }
        except Exception:
            info = None  # unreadable: not a flightsim log
        _log_info_cache[key] = info
    return _log_info_cache[key]


def _task_of(config_json: str | None) -> tuple[str | None, bool | None]:
    """The task of a log ("free", "approach", "takeoff", "circuit") and whether it flew in
    wind, from its config (None, None if unreadable)."""
    try:
        raw = json.loads(config_json or "null")
    except ValueError:
        return None, None
    if not isinstance(raw, dict):
        return None, None
    for task in ("circuit", "takeoff", "approach"):
        if raw.get(task):
            return task, any(isinstance(raw.get(k), dict) and bool(raw[k].get("wind")) for k in ("circuit", "takeoff", "approach"))
    speeds = (raw.get("wind") or {}).get("steady_speed_mps") or [0]
    return "free", max(speeds) > 0


def _hud_used(aids_json: str | None) -> bool | None:
    """Whether the HUD was in view at any time (None: not recorded)."""
    try:
        aids = json.loads(aids_json) if aids_json else None
    except ValueError:
        return None
    return bool(aids.get("hud")) if isinstance(aids, dict) and "hud" in aids else None


def _last_time(path: Path, md) -> float:
    """Largest t_s, from the column statistics when present (no data read)."""
    if md.num_rows == 0:
        return 0.0
    col = md.schema.names.index("t_s")
    stats = [md.row_group(i).column(col).statistics for i in range(md.num_row_groups)]
    if all(s is not None and s.has_min_max for s in stats):
        return float(max(s.max for s in stats))
    return float(pq.read_table(path, columns=["t_s"]).column("t_s")[-1].as_py())


def _log_group(rel: str) -> str:
    """"demos", "batch/<id>" for batch episode logs, else the top directory ("" at the top)."""
    parts = rel.split("/")
    if parts[0] == "batch" and len(parts) > 2:
        return f"batch/{parts[1]}"
    return parts[0] if len(parts) > 1 else ""


def list_logs(data_dir: Path) -> list[dict]:
    logs = []
    for path in sorted(data_dir.rglob("*.parquet")):
        info = _log_info(path)
        if info is None:
            continue
        rel = path.relative_to(data_dir).as_posix()
        logs.append({"path": rel, "group": _log_group(rel), **info})
    return logs
