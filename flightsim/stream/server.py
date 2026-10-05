"""HTTP + WebSocket server on one port: serves the viewer and streams frames at /ws.

Each WebSocket connection is an independent playback session. Clients can only pick a
source and control playback (pause, speed); nothing they send reaches the physics.
Pacing to wall-clock time happens here, never in the core.
"""

import asyncio
import json
import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from flightsim.control.autopilot import AutopilotGains
from flightsim.envs import EnvConfig
from flightsim.control.approach import ApproachGains
from flightsim.envs.policies import ApproachPolicy, LQRPolicy, PIDPolicy
from flightsim.stream.protocol import PROTOCOL_VERSION, encode
from flightsim.stream.sources import LiveSource, ManualSource, ReplaySource, Source, list_logs

VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"
MAX_SPEED = 64.0


@dataclass
class ServerConfig:
    data_dir: Path
    env_cfg: EnvConfig
    gains: AutopilotGains
    frame_rate_hz: float = 30.0
    # Manual-flight tasks by conditions name ("calm", "windy"); default: the autopilot task.
    manual_env_cfgs: dict[str, EnvConfig] | None = None
    lqr_raw: dict | None = None  # LQR config; None = only the PID can be watched
    approach_env_cfg: EnvConfig | None = None  # approach task flown by the approach autopilot
    approach_gains: ApproachGains | None = None

    def autopilot(self, name: str):
        if name == "pid":
            return PIDPolicy(self.gains, self.env_cfg.control_rate_hz)
        if name == "lqr" and self.lqr_raw is not None:
            return LQRPolicy.designed(self.env_cfg, self.lqr_raw)
        if name == "approach" and self.approach_gains is not None:
            return ApproachPolicy(self.approach_gains, self.approach_env_cfg.control_rate_hz)
        names = ["pid"] + (["lqr"] if self.lqr_raw else []) + (["approach"] if self.approach_gains else [])
        raise ValueError(f"unknown autopilot {name!r}; choose from {names}")


def _static_response(path: str) -> Response:
    rel = path.split("?", 1)[0].lstrip("/") or "index.html"
    file = (VIEWER_DIR / rel).resolve()
    if not file.is_relative_to(VIEWER_DIR) or not file.is_file():
        return Response(404, "Not Found", Headers([("Content-Type", "text/plain")]), b"not found\n")
    ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    if file.suffix == ".js":
        ctype = "text/javascript"
    body = file.read_bytes()
    headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-cache")])
    return Response(200, "OK", headers, body)


class Session:
    def __init__(self, ws: ServerConnection, cfg: ServerConfig):
        self.ws, self.cfg = ws, cfg
        self.task: asyncio.Task | None = None
        self.source: Source | None = None
        self.record = True
        self.speed = 1.0
        self.paused = asyncio.Event()  # set = paused
        self._rebase = True
        self._seeked = False

    def _open(self, msg: dict) -> Source:
        if msg.get("source") == "replay":
            path = (self.cfg.data_dir / str(msg["path"])).resolve()
            if not path.is_relative_to(self.cfg.data_dir.resolve()) or path.suffix != ".parquet" or not path.is_file():
                raise ValueError(f"no such log: {msg['path']}")
            source = ReplaySource(path)
            if "start_s" in msg:
                source.seek(float(msg["start_s"]))
            return source
        if msg.get("source") == "live":
            name = str(msg.get("autopilot", "pid"))
            policy = self.cfg.autopilot(name)
            env_cfg = self.cfg.approach_env_cfg if name == "approach" else self.cfg.env_cfg
            return LiveSource(env_cfg, None, int(msg.get("seed", 0)), policy=policy)
        if msg.get("source") == "manual":
            tasks = self.cfg.manual_env_cfgs or {"calm": self.cfg.env_cfg}
            conditions = str(msg.get("conditions", next(iter(tasks))))
            if conditions not in tasks:
                raise ValueError(f"unknown conditions {conditions!r}; choose from {list(tasks)}")
            return ManualSource(tasks[conditions], int(msg.get("seed", 0)))
        raise ValueError(f"unknown source {msg.get('source')!r}")

    async def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "list":
            logs = await asyncio.to_thread(list_logs, self.cfg.data_dir)  # can take a while for big batches
            await self.ws.send(encode({"type": "logs", "logs": logs}))
        elif kind == "play":
            await self.stop()
            source = await asyncio.to_thread(self._open, msg)
            self.source = source
            self.record = bool(msg.get("record", True))
            self.speed = self._clamp(msg.get("speed", 1.0))
            self.paused.clear()
            self._rebase = True  # pace the new flight from its first frame
            self.task = asyncio.create_task(self._stream(source))
        elif kind == "input":
            # Pilot input: only meaningful during a manual flight, where it becomes the
            # policy's action at the next decision step.
            if isinstance(self.source, ManualSource) and self.task and not self.task.done():
                self.source.pilot.set_input(
                    msg["elevator"], msg["aileron"], msg["rudder"], msg["throttle"],
                    msg.get("flaps"), msg.get("pitch_trim"),
                )  # fmt: skip
        elif kind == "pause":
            self.paused.set()
        elif kind == "resume":
            self.paused.clear()
            self._rebase = True
        elif kind == "speed":
            self.speed = self._clamp(msg.get("value", 1.0))
            self._rebase = True
        elif kind == "seek":
            # Replays only: playback continues (or, when paused, shows one frame) from t_s.
            if not isinstance(self.source, ReplaySource) or not self.task or self.task.done():
                raise ValueError("seeking is only possible during a replay")
            self.source.seek(float(msg["t_s"]))
            self._seeked = True
        elif kind == "stop":
            await self.stop()
        else:
            raise ValueError(f"unknown message type {kind!r}")

    def _clamp(self, speed) -> float:
        limit = min(MAX_SPEED, self.source.max_speed if isinstance(self.source, LiveSource) else MAX_SPEED)
        return min(limit, max(0.05, float(speed)))

    async def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            await self.ws.send(encode({"type": "end", "reason": "stopped"}))
        self.task = None

    async def _stream(self, source: Source) -> None:
        try:
            await self._play(source)
        finally:
            if isinstance(source, ManualSource) and self.record:
                await self._save(source)

    async def _save(self, source: ManualSource) -> None:
        path = await asyncio.to_thread(source.save, self.cfg.data_dir)
        if path is not None:
            try:
                await self.ws.send(encode({"type": "saved", "path": path.relative_to(self.cfg.data_dir).as_posix()}))
            except ConnectionClosed:
                pass

    async def _play(self, source: Source) -> None:
        await self.ws.send(encode({
            "type": "hello", "protocol": PROTOCOL_VERSION, "source": source.source, "run_id": source.run_id,
            "aircraft": source.aircraft, "sim_rate_hz": source.sim_rate_hz, "frame_rate_hz": self.cfg.frame_rate_hz,
            "duration_s": source.duration_s, "targets": source.targets, "meta": source.meta,
            "pilot": source.pilot_name, "approach": source.approach,
        }))  # fmt: skip
        frame_dt = 1.0 / self.cfg.frame_rate_hz
        next_t = None
        wall0 = sim0 = 0.0
        last_row = None
        self._seeked = show_one = False
        for t, row in source.frames():
            last_row = row
            if self._seeked:  # first row after a seek: send it now and restart pacing here
                self._seeked, next_t, self._rebase = False, None, True
                show_one = self.paused.is_set()
            if next_t is not None and t < next_t - 1e-9:
                continue
            next_t = t + frame_dt
            if self.paused.is_set() and not show_one:
                while self.paused.is_set() and not self._seeked:
                    await asyncio.sleep(0.05)
                if self._seeked:
                    continue  # the next row comes from the new position
                self._rebase = True
            show_one = False
            if self._rebase:
                wall0, sim0, self._rebase = time.monotonic(), t, False
            delay = wall0 + (t - sim0) / self.speed - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            await self.ws.send(encode({"type": "frame", "row": row}))
            last_row = None
        if last_row is not None:  # always deliver the final state
            await self.ws.send(encode({"type": "frame", "row": last_row}))
        end = {"type": "end", "reason": source.end_reason}
        if source.landing is not None:
            end["landing"] = source.landing
        await self.ws.send(encode(end))


def make_handler(cfg: ServerConfig):
    async def handler(ws: ServerConnection) -> None:
        session = Session(ws, cfg)
        try:
            async for text in ws:
                try:
                    await session.handle(json.loads(text))
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
                    await ws.send(encode({"type": "error", "message": str(e)}))
        except ConnectionClosed:
            pass
        finally:
            if session.task:
                session.task.cancel()

    return handler


def process_request(connection: ServerConnection, request: Request) -> Response | None:
    if request.path.split("?", 1)[0] == "/ws":
        return None  # continue with the WebSocket handshake
    return _static_response(request.path)


async def run_server(
    cfg: ServerConfig, host: str, port: int, on_ready: Callable[[int], None] | None = None
) -> None:
    """Serve until cancelled. `on_ready` receives the bound port (useful with port=0)."""
    async with serve(make_handler(cfg), host, port, process_request=process_request) as server:
        if on_ready is not None:
            on_ready(server.sockets[0].getsockname()[1])
        await server.serve_forever()
