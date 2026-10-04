"""Gain-scheduled LQR autopilot: altitude, heading and airspeed hold.

Design (offline, at construction): trim and linearize the aircraft at a grid of
(altitude, airspeed) points; at each, augment the coupled 11-state model with integrators
on the altitude, airspeed and heading errors (10 states; see STATES), discretize with a zero-order hold at the
controller rate, and solve the discrete LQR. Weights follow Bryson's rule: each state and
input is weighted by 1 / (largest acceptable deviation)^2.

Run time: a reference governor moves the altitude and heading references toward the
targets along smooth profiles: bounded rates (climb rate; turn rate limited so the
steady-turn bank stays within max_bank) that also change at bounded accelerations and
slow down approaching the target, so the LQR tracks a feasible path instead of jumping
at a far target.
While the references move, the steady-state turn (bank, body rates) and climb angle that
go with them are fed forward as reference states, so the LQR does not fight the bank
or pitch it needs. Gains and trim point are interpolated
bilinearly in (altitude, airspeed); the control is u = u_trim - K x on the deviation from
the trim point and the moving reference. Errors are also clipped as a safety net, and
integrators only run while errors are unclipped and controls unsaturated (anti-windup).

Inputs are the effective commands seen by the flight control system: the elevator
channel is elevator command + pitch trim (the FCS sums them), so the elevator command
sent is the total minus the episode's held pitch trim.
"""

import hashlib
import math
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import jsbsim
from scipy.linalg import expm, solve_discrete_are

from flightsim.config import canonical_json, load_raw
from flightsim.control.autopilot import Targets
from flightsim.control.heading_hold import wrap_angle_rad
from flightsim.core import Controls, InitialConditions, JSBSimCore, Loading, State, aircraft_hash

# Engine RPM is deliberately not a controller state: JSBSim's linearization gives its row
# almost no self-damping (time constant ~11 min), while the nonlinear model settles in a few
# seconds. Without it, throttle acts on airspeed directly (thrust at the trim RPM).
G = 9.80665

STATES = (
    "tas_mps", "alpha_rad", "theta_rad", "q_radps",
    "beta_rad", "phi_rad", "p_radps", "psi_rad", "r_radps", "alt_msl_m",
)  # fmt: skip
INTEGRATORS = ("alt_msl_m", "tas_mps", "psi_rad")
INPUTS = ("throttle", "aileron", "elevator", "rudder")


@dataclass(frozen=True)
class LQRConfig:
    alt_grid_m: tuple[float, ...]
    tas_grid_mps: tuple[float, ...]
    max_state_dev: dict[str, float]  # Bryson's rule, per state name
    max_integral_dev: dict[str, float]  # per integrated error
    max_input_dev: dict[str, float]  # per input
    alt_error_clip_m: float
    heading_error_clip_rad: float
    max_climb_mps: float  # reference governor: altitude reference rate limit
    max_turn_rate_radps: float  # reference governor: heading reference rate limit
    max_bank_rad: float  # reference governor: also limit turn rate to g tan(max_bank) / V
    max_climb_accel_mps2: float  # reference governor: how fast the climb rate may change
    max_turn_accel_radps2: float  # reference governor: how fast the turn rate may change


def load_lqr_config(path: str | Path, overrides: dict | None = None) -> LQRConfig:
    return lqr_config_from_raw(load_raw(path, overrides))


def lqr_config_from_raw(raw: dict) -> LQRConfig:
    w = raw["weights"]
    return LQRConfig(
        alt_grid_m=tuple(float(x) for x in raw["schedule"]["alt_msl_m"]),
        tas_grid_mps=tuple(float(x) for x in raw["schedule"]["tas_mps"]),
        max_state_dev={k: float(w["states"][k]) for k in STATES},
        max_integral_dev={k: float(w["integrals"][k]) for k in INTEGRATORS},
        max_input_dev={k: float(w["inputs"][k]) for k in INPUTS},
        alt_error_clip_m=float(raw["alt_error_clip_m"]),
        heading_error_clip_rad=math.radians(raw["heading_error_clip_deg"]),
        max_climb_mps=float(raw["reference"]["max_climb_mps"]),
        max_turn_rate_radps=math.radians(raw["reference"]["max_turn_rate_deg_s"]),
        max_bank_rad=math.radians(raw["reference"]["max_bank_deg"]),
        max_climb_accel_mps2=float(raw["reference"]["max_climb_accel_mps2"]),
        max_turn_accel_radps2=math.radians(raw["reference"]["max_turn_accel_deg_s2"]),
    )


@dataclass(frozen=True)
class DesignPoint:
    k: np.ndarray  # (4, 13) gain on [STATES deviations, INTEGRATORS]
    x_trim: np.ndarray  # (10,) trim state (heading and altitude entries unused)
    u_trim: np.ndarray  # (4,) effective trim inputs (elevator includes pitch trim)
    closed_loop_eigs: np.ndarray  # discrete closed-loop eigenvalues (stability check)


def design_point(aircraft: str, loading: Loading, alt_m: float, tas_mps: float, cfg: LQRConfig, dt_s: float) -> DesignPoint:
    core = JSBSimCore(aircraft, 1 / 120)
    core.reset(InitialConditions(alt_m, tas_mps, 0.0), loading)
    trim = core.trim()
    s = core.state()
    lm = core.linearize()
    idx = [lm.state_names.index(n) for n in STATES]
    a = lm.a[np.ix_(idx, idx)]
    b = lm.b[np.ix_(idx, [lm.input_names.index(n) for n in INPUTS])]

    # Augment with integrators of the altitude, airspeed and heading deviations.
    n, m, ni = len(STATES), len(INPUTS), len(INTEGRATORS)
    a_aug = np.zeros((n + ni, n + ni))
    a_aug[:n, :n] = a
    for j, name in enumerate(INTEGRATORS):
        a_aug[n + j, STATES.index(name)] = 1.0
    b_aug = np.vstack([b, np.zeros((ni, m))])

    # Zero-order-hold discretization: expm([[A, B], [0, 0]] dt).
    block = np.zeros((n + ni + m, n + ni + m))
    block[: n + ni, : n + ni] = a_aug
    block[: n + ni, n + ni :] = b_aug
    phi = expm(block * dt_s)
    ad, bd = phi[: n + ni, : n + ni], phi[: n + ni, n + ni :]

    q = np.diag([1 / cfg.max_state_dev[k] ** 2 for k in STATES] + [1 / cfg.max_integral_dev[k] ** 2 for k in INTEGRATORS])
    r = np.diag([1 / cfg.max_input_dev[k] ** 2 for k in INPUTS])
    p = solve_discrete_are(ad, bd, q, r)
    k = np.linalg.solve(r + bd.T @ p @ bd, bd.T @ p @ ad)

    x_trim = np.array([getattr(s, name) for name in STATES])
    u_trim = np.array([trim.throttle, trim.aileron, trim.elevator + trim.pitch_trim, trim.rudder])
    return DesignPoint(k=k, x_trim=x_trim, u_trim=u_trim, closed_loop_eigs=np.linalg.eigvals(ad - bd @ k))


CACHE_DIR = Path("data/cache/lqr")


class GainSchedule:
    def __init__(self, aircraft: str, loading: Loading, cfg: LQRConfig, dt_s: float, points=None):
        self.cfg = cfg
        self.points = points or [
            [design_point(aircraft, loading, alt, tas, cfg, dt_s) for tas in cfg.tas_grid_mps] for alt in cfg.alt_grid_m
        ]

    @classmethod
    def cached(cls, aircraft: str, loading: Loading, raw: dict, dt_s: float, cache_dir: Path = CACHE_DIR) -> "GainSchedule":
        """Design once per (aircraft files, JSBSim version, loading, LQR config, dt) and reuse.
        Designing trims and linearizes every grid point (~0.8 s each)."""
        key = hashlib.sha256(
            canonical_json({
                "aircraft_hash": aircraft_hash(aircraft), "jsbsim": jsbsim.__version__, "loading": asdict(loading),
                "lqr": raw, "dt_s": dt_s, "states": STATES, "format": 1,
            }).encode()
        ).hexdigest()[:16]  # fmt: skip
        cfg = lqr_config_from_raw(raw)
        path = Path(cache_dir) / f"{aircraft}-{key}.npz"
        if path.is_file():
            data = np.load(path)
            n_alt, n_tas = len(cfg.alt_grid_m), len(cfg.tas_grid_mps)
            points = [
                [DesignPoint(*(data[f][i, j] for f in ("k", "x_trim", "u_trim", "closed_loop_eigs"))) for j in range(n_tas)]
                for i in range(n_alt)
            ]
            return cls(aircraft, loading, cfg, dt_s, points)
        schedule = cls(aircraft, loading, cfg, dt_s)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp.npz")
        np.savez(tmp, **{f: np.array([[getattr(p, f) for p in row] for row in schedule.points]) for f in ("k", "x_trim", "u_trim", "closed_loop_eigs")})
        os.replace(tmp, path)  # atomic: parallel workers may race to write the same file
        return schedule

    def lookup(self, alt_m: float, tas_mps: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Bilinear interpolation of (K, x_trim, u_trim); clamped to the grid edges."""

        def locate(grid: tuple[float, ...], v: float) -> tuple[int, float]:
            v = min(max(v, grid[0]), grid[-1])
            i = min(int(np.searchsorted(grid, v, side="right")) - 1, len(grid) - 2)
            return i, (v - grid[i]) / (grid[i + 1] - grid[i])

        i, fa = locate(self.cfg.alt_grid_m, alt_m)
        j, ft = locate(self.cfg.tas_grid_mps, tas_mps)
        corners = [(self.points[i + di][j + dj], (fa if di else 1 - fa) * (ft if dj else 1 - ft)) for di in (0, 1) for dj in (0, 1)]
        return tuple(sum(w * getattr(p, f) for p, w in corners) for f in ("k", "x_trim", "u_trim"))


class LQRAutopilot:
    def __init__(self, schedule: GainSchedule, trim: Controls, initial: State, targets: Targets, dt_s: float):
        self.schedule = schedule
        self.trim = trim
        self.targets = targets
        self.dt_s = dt_s
        self._integral = np.zeros(len(INTEGRATORS))
        self.ref_alt_m = initial.alt_msl_m
        self.ref_heading_rad = initial.psi_rad
        self.ref_climb_mps = 0.0
        self.ref_turn_rate_radps = 0.0

    def _profile(self, error: float, rate: float, max_rate: float, max_accel: float) -> tuple[float, float]:
        """One step of a rate- and acceleration-limited approach to a target `error` away.
        Returns (position change, new rate). The rate aims for the fastest speed from which
        it can still brake to a stop at the target in discrete steps of dt: the largest v
        with v + (v - a dt) + (v - 2 a dt) + ... <= |error|, i.e.
        v = a dt (sqrt(1/4 + 2 |error| / (a dt^2)) - 1/2)."""
        adt = max_accel * self.dt_s
        brake = adt * (math.sqrt(0.25 + 2.0 * abs(error) / (adt * self.dt_s)) - 0.5)
        wanted = math.copysign(min(max_rate, brake), error)
        dv = max_accel * self.dt_s
        rate = max(rate - dv, min(rate + dv, wanted))
        step = rate * self.dt_s
        if abs(step) >= abs(error) and (error == 0 or (step > 0) == (error > 0)):
            return error, 0.0  # arrive exactly, without overshoot
        return step, rate

    def _advance_references(self, tas_mps: float) -> None:
        cfg, t = self.schedule.cfg, self.targets
        d_alt, self.ref_climb_mps = self._profile(
            t.alt_msl_m - self.ref_alt_m, self.ref_climb_mps, cfg.max_climb_mps, cfg.max_climb_accel_mps2
        )
        self.ref_alt_m += d_alt
        max_rate = min(cfg.max_turn_rate_radps, G * math.tan(cfg.max_bank_rad) / tas_mps)
        d_hdg, self.ref_turn_rate_radps = self._profile(
            wrap_angle_rad(t.heading_rad - self.ref_heading_rad), self.ref_turn_rate_radps, max_rate, cfg.max_turn_accel_radps2
        )
        self.ref_heading_rad = wrap_angle_rad(self.ref_heading_rad + d_hdg)

    def __call__(self, s: State) -> Controls:
        cfg, t = self.schedule.cfg, self.targets
        self._advance_references(s.tas_mps)
        k, x_trim, u_trim = self.schedule.lookup(s.alt_msl_m, s.tas_mps)
        # Feedforward reference states for the commanded steady turn and climb.
        x_ref = x_trim.copy()
        w = self.ref_turn_rate_radps
        phi_ref = math.atan(w * s.tas_mps / G)
        theta = x_trim[STATES.index("theta_rad")]
        x_ref[STATES.index("phi_rad")] = phi_ref
        x_ref[STATES.index("q_radps")] = w * math.sin(phi_ref) * math.cos(theta)
        x_ref[STATES.index("r_radps")] = w * math.cos(phi_ref) * math.cos(theta)
        x_ref[STATES.index("theta_rad")] = theta + math.asin(max(-1.0, min(1.0, self.ref_climb_mps / s.tas_mps)))

        alt_err = s.alt_msl_m - self.ref_alt_m
        hdg_err = wrap_angle_rad(s.psi_rad - self.ref_heading_rad)
        tas_err = s.tas_mps - t.tas_mps
        clipped_alt = max(-cfg.alt_error_clip_m, min(cfg.alt_error_clip_m, alt_err))
        clipped_hdg = max(-cfg.heading_error_clip_rad, min(cfg.heading_error_clip_rad, hdg_err))

        x = np.array([getattr(s, name) for name in STATES]) - x_ref
        x[STATES.index("tas_mps")] = tas_err
        x[STATES.index("psi_rad")] = clipped_hdg
        x[STATES.index("alt_msl_m")] = clipped_alt
        u = u_trim - k @ np.concatenate([x, self._integral])

        lo = np.array([0.0, -1.0, -1.0, -1.0])
        hi = np.ones(4)
        u_sat = np.clip(u, lo, hi)
        # Anti-windup: integrate only when nothing is clipped or saturated, and hold the
        # altitude / heading integrators while their reference is still moving (transient
        # tracking lag is not a steady-state bias and would otherwise cause overshoot).
        if clipped_alt == alt_err and clipped_hdg == hdg_err and np.array_equal(u, u_sat):
            moving = (self.ref_climb_mps != 0.0, False, self.ref_turn_rate_radps != 0.0)
            errors = (alt_err, tas_err, hdg_err)
            self._integral += self.dt_s * np.array([0.0 if m else e for m, e in zip(moving, errors)])

        throttle, aileron, elevator_total, rudder = u_sat
        return replace(
            self.trim,
            throttle=float(throttle),
            aileron=float(aileron),
            elevator=float(np.clip(elevator_total - self.trim.pitch_trim, -1.0, 1.0)),
            rudder=float(rudder),
        )
