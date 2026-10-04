"""Scripted validation maneuvers flown on the physics core.

Each maneuver builds its own core so results do not depend on earlier runs.
"""

from dataclasses import dataclass, replace

import numpy as np

from flightsim.analysis.modes import Oscillation, oscillation_from_response
from flightsim.core import Controls, InitialConditions, JSBSimCore, Loading, State

KT_TO_MPS = 1852.0 / 3600.0
_NOMINAL_IC = InitialConditions(alt_msl_m=1524.0, tas_mps=50.0, heading_rad=0.0)


def loading_for(
    aircraft: str,
    mass_kg: float,
    cg_x_m: float,
    fuel_kg: float,
    *,
    n_pointmasses: int = 5,
    n_tanks: int = 2,
    front_seat: int = 0,
    rear_seat: int = 2,
) -> Loading:
    """Split payload between a front and a rear seat to hit a total mass and CG.

    The CG is linear in the seat masses, so two probe loadings give the exact split.
    Raises ValueError if the target is outside what the two seats can reach.
    """

    def loading(front_kg: float, rear_kg: float) -> Loading:
        masses = [0.0] * n_pointmasses
        masses[front_seat], masses[rear_seat] = front_kg, rear_kg
        return Loading(pointmasses_kg=tuple(masses), fuel_tanks_kg=(fuel_kg / n_tanks,) * n_tanks)

    core = JSBSimCore(aircraft, 1 / 120)
    core.reset(_NOMINAL_IC, loading(0.0, 0.0))
    payload = mass_kg - core.mass_properties().mass_kg
    if payload < 0:
        raise ValueError(f"empty aircraft + fuel already exceeds {mass_kg:.1f} kg")
    core.reset(_NOMINAL_IC, loading(payload, 0.0))
    cg_front = core.mass_properties().cg_x_m
    core.reset(_NOMINAL_IC, loading(0.0, payload))
    cg_rear = core.mass_properties().cg_x_m
    rear_fraction = (cg_x_m - cg_front) / (cg_rear - cg_front)
    if not 0.0 <= rear_fraction <= 1.0:
        raise ValueError(f"CG {cg_x_m:.4f} m not reachable (range {cg_front:.4f}..{cg_rear:.4f} m)")
    return loading(payload * (1 - rear_fraction), payload * rear_fraction)


def trim_at_cas(
    aircraft: str, alt_msl_m: float, cas_mps: float, loading: Loading, controls: Controls = Controls()
) -> tuple[JSBSimCore, InitialConditions, Controls]:
    """Trim in level flight at a calibrated airspeed (the core takes true airspeed)."""
    core = JSBSimCore(aircraft, 1 / 120)
    tas = cas_mps
    for _ in range(6):
        core.reset(InitialConditions(alt_msl_m, tas, 0.0), loading, controls)
        tas *= cas_mps / core.state().cas_mps
    ic = InitialConditions(alt_msl_m, tas, 0.0)
    core.reset(ic, loading, controls)
    return core, ic, core.trim()


@dataclass(frozen=True)
class StallResult:
    vs_cas_mps: float  # minimum calibrated airspeed reached
    alpha_max_rad: float
    decel_mps2: float  # deceleration over the 8 s to 2 s before the minimum
    elevator_limited: bool  # elevator at full nose-up when the minimum was reached


def stall_speed(
    aircraft: str,
    loading: Loading,
    flaps: float,
    *,
    alt_msl_m: float = 1524.0,
    entry_cas_mps: float = 70 * KT_TO_MPS,
    decel_kts_per_s: float = 1.0,
) -> StallResult:
    """Power-off, wings-level stall: trim, close the throttle, then decelerate at about
    1 kt/s using elevator until the aircraft cannot slow further. The stall speed is the
    minimum calibrated airspeed reached (the certification definition)."""
    core, _, trim = trim_at_cas(aircraft, alt_msl_m, entry_cas_mps, loading, Controls(flaps=flaps))
    dt = core.dt_s
    s = core.state()
    u = replace(trim, throttle=0.0)
    target = s.cas_mps
    integral = 0.0
    floor = 25 * KT_TO_MPS
    n = int(((entry_cas_mps - floor) / (decel_kts_per_s * KT_TO_MPS) + 15.0) / dt)
    t, cas, alpha, elev = np.empty(n), np.empty(n), np.empty(n), np.empty(n)
    for i in range(n):
        target = max(target - decel_kts_per_s * KT_TO_MPS * dt, floor)
        err = s.cas_mps - target  # too fast -> pull (negative elevator)
        integral += err * dt
        e = trim.elevator - 0.08 * err - 0.01 * integral + 0.3 * s.q_radps
        a = trim.aileron - 1.0 * s.phi_rad - 0.2 * s.p_radps
        u = replace(u, elevator=min(1.0, max(-1.0, e)), aileron=min(1.0, max(-1.0, a)))
        s = core.step(u)
        t[i], cas[i], alpha[i], elev[i] = s.t_s, s.cas_mps, s.alpha_rad, u.elevator
    k = int(np.argmin(cas))
    window = (t > t[k] - 8.0) & (t < t[k] - 2.0)
    decel = -float(np.polyfit(t[window], cas[window], 1)[0])
    return StallResult(
        vs_cas_mps=float(cas[k]),
        alpha_max_rad=float(alpha.max()),
        decel_mps2=decel,
        elevator_limited=bool(elev[k] <= -1.0),
    )


def phugoid_response(
    aircraft: str,
    loading: Loading,
    *,
    alt_msl_m: float,
    trim_cas_mps: float,
    release_cas_mps: float,
    push: float = 0.1,
    record_s: float = 120.0,
) -> tuple[Oscillation, np.ndarray, np.ndarray]:
    """Push the nose down from trim until `release_cas_mps`, return controls to trim
    (stick fixed) and identify the free phugoid from the airspeed response."""
    core, _, trim = trim_at_cas(aircraft, alt_msl_m, trim_cas_mps, loading)
    u = replace(trim, elevator=trim.elevator + push)
    s: State = core.state()
    for _ in range(int(60.0 / core.dt_s)):
        s = core.step(u)
        if s.cas_mps >= release_cas_mps:
            break
    else:
        raise RuntimeError(f"never reached {release_cas_mps:.1f} m/s CAS with push {push}")
    t0 = s.t_s
    n = int(record_s / core.dt_s)
    t, cas = np.empty(n), np.empty(n)
    for i in range(n):
        s = core.step(trim)
        t[i], cas[i] = s.t_s - t0, s.cas_mps
    return oscillation_from_response(t, cas, trim_cas_mps), t, cas
