"""Frame sources for the state stream. Each yields (t_s, row) at the simulation rate;
the server decimates and paces them. Sources never see wall-clock time."""

import hashlib
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from flightsim.control.autopilot import AutopilotGains
from flightsim.control.manual import HumanPolicy
from flightsim.datalog import make_run_id, read_log, write_log
from flightsim.envs import AltitudeHeadingHoldEnv, EnvConfig
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
    pilot_name: str | None = None  # who flies a live flight: "pid", "lqr" or "human"

    def frames(self) -> Iterator[tuple[float, dict]]:
        raise NotImplementedError


class ReplaySource(Source):
    def __init__(self, path: Path):
        table, meta = read_log(path)
        self._rows = table.to_pylist()
        t = table.column("t_s")
        duration = t[-1].as_py() if len(t) else 0.0
        rate = (len(t) - 1) / duration if duration > 0 else 0.0
        super().__init__("replay", meta["flightsim.run_id"], meta["flightsim.aircraft"], round(rate, 6), duration, None, meta)

    def frames(self) -> Iterator[tuple[float, dict]]:
        for row in self._rows:
            yield row["t_s"], row


class LiveSource(Source):
    """Flies one episode of the altitude/heading task with a policy (the PID autopilot
    unless given another, e.g. the LQR autopilot or a human pilot)."""

    max_speed = math.inf

    def __init__(self, env_cfg: EnvConfig, gains: AutopilotGains | None, seed: int, policy=None, source: str = "live"):
        self._env = AltitudeHeadingHoldEnv(env_cfg, record=True)
        self._policy = policy or PIDPolicy(gains, env_cfg.control_rate_hz)
        self._seed = seed
        self._obs, self._info = self._env.reset(seed=seed)
        self._policy.reset(self._info)
        t = self._env.targets
        targets = {"alt_msl_m": t.alt_msl_m, "heading_rad": t.heading_rad, "tas_mps": t.tas_mps}
        run_id = make_run_id(env_cfg.config_hash, seed)
        super().__init__(source, run_id, env_cfg.aircraft, env_cfg.sim_rate_hz, env_cfg.episode_s, targets)
        self.pilot_name = "human" if source == "manual" else self._policy.name
        self._config_hash = env_cfg.config_hash

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
    """A human flies the task episode in real time; the flight is kept as a demonstration."""

    max_speed = 1.0
    min_save_s = 5.0

    def __init__(self, env_cfg: EnvConfig, seed: int):
        self.pilot = HumanPolicy()
        super().__init__(env_cfg, None, seed, policy=self.pilot, source="manual")

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
        return write_log(path, self._env.episode_result(), self._env.provenance(run_id=run_id, pilot="human"))


def list_logs(data_dir: Path) -> list[dict]:
    logs = []
    for path in sorted(data_dir.rglob("*.parquet")):
        try:
            meta = {k.decode(): v.decode() for k, v in (pq.read_schema(path).metadata or {}).items()}
            n = pq.read_metadata(path).num_rows
        except Exception:
            continue  # not a flightsim log
        if "flightsim.run_id" not in meta:
            continue
        logs.append({
            "path": path.relative_to(data_dir).as_posix(),
            "run_id": meta["flightsim.run_id"],
            "aircraft": meta.get("flightsim.aircraft"),
            "rows": n,
        })  # fmt: skip
    return logs
