"""Scripted validation maneuvers flown on the physics core.

Each maneuver builds its own core so results do not depend on earlier runs.
"""

import math
from dataclasses import dataclass, replace

import numpy as np

from flightsim.analysis.modes import Oscillation, oscillation_from_response
from flightsim.core import Atmosphere, Controls, InitialConditions, JSBSimCore, Loading, State

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
    aircraft: str, alt_msl_m: float, cas_mps: float, loading: Loading, controls: Controls = Controls(),
    atmosphere: Atmosphere | None = None,
) -> tuple[JSBSimCore, InitialConditions, Controls]:
    """Trim in level flight at a calibrated airspeed (the core takes true airspeed), on a
    standard day unless `atmosphere` is given."""
    core = JSBSimCore(aircraft, 1 / 120)
    if atmosphere is not None:
        core.set_atmosphere(atmosphere)
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


@dataclass(frozen=True)
class LeanCruise:
    """Steady level cruise at a leaned mixture."""

    mixture: float
    throttle: float
    engine_rpm: float
    fuel_flow_kgps: float


def _engine_after_leaning(aircraft: str, loading: Loading, ic: InitialConditions, start_mixture: float,
                          mixtures: np.ndarray, settle_s: float, dt_s: float) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """Trim at start_mixture, then hold that throttle and set each mixture: RPM and EGT
    after settle_s (the pilot's view while leaning; RPM settles in about 2 s)."""
    rpm, egt = [], []
    for m in mixtures:
        core = JSBSimCore(aircraft, dt_s)
        core.reset(ic, loading, Controls(mixture=start_mixture))
        u = replace(core.trim(), mixture=float(m))
        for _ in range(round(settle_s / dt_s)):
            s = core.step(u)
        rpm.append(s.engine_rpm)
        egt.append(core.engine().egt_k)
    return np.array(rpm), np.array(egt)


def lean_cruise(
    aircraft: str,
    loading: Loading,
    alt_msl_m: float,
    tas_mps: float,
    *,
    method: str = "rpm",
    rpm_drop: float = 37.5,
    egt_rich_k: float = 50.0 * 5.0 / 9.0,
    settle_s: float = 3.0,
    dt_s: float = 1 / 120,
) -> LeanCruise:
    """Lean the mixture as the POH describes, then trim level at tas_mps and read the
    fuel flow. Methods (C172P POH Section 4):
      "rpm": lean until RPM peaks, then further until it drops 25-50 RPM (rpm_drop is the
             middle); the POH ties this to its Section 5 fuel figures.
      "egt": 50 F rich of peak EGT (Figure 4-4, "recommended lean").
    Leaning changes power, so re-trimming at the speed moves the throttle; repeat until
    the mixture settles."""
    if method not in ("rpm", "egt"):
        raise ValueError(f"unknown leaning method {method!r}")
    ic = InitialConditions(alt_msl_m=alt_msl_m, tas_mps=tas_mps, heading_rad=0.0)
    grid = np.round(np.arange(1.0, 0.499, -0.01), 2)
    mixture = 1.0
    for _ in range(6):
        rpm, egt = _engine_after_leaning(aircraft, loading, ic, mixture, grid, settle_s, dt_s)
        if method == "rpm":
            i = int(np.argmax(rpm))
            target = rpm[i] - rpm_drop
            below = np.nonzero(rpm[i:] <= target)[0]
            if not len(below):
                raise RuntimeError("RPM never dropped below its peak by rpm_drop")
            j = i + int(below[0])
            new = float(np.interp(target, [rpm[j], rpm[j - 1]], [grid[j], grid[j - 1]]))
        else:
            i = int(np.argmax(egt))
            target = egt[i] - egt_rich_k
            above = np.nonzero(egt[: i + 1] >= target)[0]
            k = int(above[0])
            new = float(grid[0]) if k == 0 else float(np.interp(target, [egt[k - 1], egt[k]], [grid[k - 1], grid[k]]))
        done = abs(new - mixture) < 0.003
        mixture = new
        if done:
            break
    core = JSBSimCore(aircraft, dt_s)
    core.reset(ic, loading, Controls(mixture=mixture))
    u = core.trim()
    s = core.step(u)
    return LeanCruise(mixture=mixture, throttle=u.throttle, engine_rpm=s.engine_rpm, fuel_flow_kgps=core.engine().fuel_flow_kgps)


# --- Ground roll (POH Section 5 takeoff and landing distances) ------------------------------

GROUND_DT_S = 1.0 / 120.0
K_STEER = 4.0  # rudder (and nosewheel) per rad heading error: keeps the roll straight
K_YAW_DAMP = 1.0  # rudder per rad/s yaw rate
K_WINGS_LEVEL = 2.0  # aileron per rad bank (the propeller torque rolls the aircraft left)
K_ROLL_DAMP = 0.3  # aileron per rad/s roll rate


def _pedals(s: State) -> float:
    """Rudder that holds heading 000 on the ground (rudder + = nose left), like a pilot
    holding the centreline against the propeller's left-turning tendency."""
    return max(-1.0, min(1.0, K_STEER * math.atan2(math.sin(s.psi_rad), math.cos(s.psi_rad)) + K_YAW_DAMP * s.r_radps))


def _wings_level(s: State) -> float:
    return max(-1.0, min(1.0, -K_WINGS_LEVEL * s.phi_rad - K_ROLL_DAMP * s.p_radps))


def landing_ground_roll_m(aircraft: str, loading: Loading, touchdown_cas_mps: float, flaps: float = 1.0) -> float:
    """Ground roll with maximum braking from a touchdown at `touchdown_cas_mps` (sea level,
    calm, so CAS = TAS = ground speed): all wheels down, throttle closed (engine idling),
    full brakes, pedals holding the heading, other controls neutral. Distance from
    touchdown to a stop, integrated from the ground speed."""
    core = JSBSimCore(aircraft, GROUND_DT_S)
    u = Controls(throttle=0.0, flaps=flaps, brake=1.0)
    s = core.reset_on_ground(0.0, loading, u, speed_mps=touchdown_cas_mps)
    distance = 0.0
    while math.hypot(s.v_north_mps, s.v_east_mps) > 0.05 and s.t_s < 120.0:
        s = core.step(replace(u, rudder=_pedals(s)))
        distance += math.hypot(s.v_north_mps, s.v_east_mps) * GROUND_DT_S
    return distance


@dataclass(frozen=True)
class TakeoffRoll:
    static_rpm: float  # full throttle against the brakes, settled
    ground_roll_m: float  # brake release to lift-off (no wheel on the ground)
    liftoff_cas_mps: float
    liftoff_pitch_rad: float
    to_reference_m: float | None = None  # brake release to `reference_cas_mps` (acceleration alone)


K_PITCH_LIMIT = 20.0  # elevator per rad pitch above the limit (eases the back pressure)
K_PITCH_LIMIT_RATE = 4.0  # elevator per rad/s pitch rate in the limiter (the rotation overshoots without it)


def takeoff_roll(
    aircraft: str, loading: Loading, flaps: float, elevator: float, max_pitch_rad: float, hold_s: float = 10.0,
    reference_cas_mps: float | None = None, atmosphere: Atmosphere | None = None,
) -> TakeoffRoll:
    """Short-field takeoff (POH Section 4): brakes set, full throttle (held `hold_s` until
    the RPM settles), brakes released, back pressure `elevator` (negative, "slightly tail
    low") eased so the pitch stays below `max_pitch_rad` (the tail skid touches at about
    10 deg on the main wheels), pedals holding the heading and ailerons the wings level,
    until the aircraft lifts off.
    Raises if anything but the wheels touches the ground."""
    core = JSBSimCore(aircraft, GROUND_DT_S)
    if atmosphere is not None:
        core.set_atmosphere(atmosphere)
    u = Controls(throttle=1.0, flaps=flaps, brake=1.0, elevator=elevator)
    s = core.reset_on_ground(0.0, loading, u)
    for _ in range(round(hold_s / GROUND_DT_S)):
        s = core.step(u)
    static_rpm = s.engine_rpm
    u = replace(u, brake=0.0)
    distance, to_reference = 0.0, None
    while s.t_s < hold_s + 120.0:
        pitch_hold = max(elevator, min(0.0, K_PITCH_LIMIT * (s.theta_rad - max_pitch_rad) + K_PITCH_LIMIT_RATE * s.q_radps))
        s = core.step(replace(u, rudder=_pedals(s), aileron=_wings_level(s), elevator=pitch_hold))
        distance += math.hypot(s.v_north_mps, s.v_east_mps) * GROUND_DT_S
        if to_reference is None and reference_cas_mps is not None and s.cas_mps >= reference_cas_mps:
            to_reference = distance
        contacts = core.contacts()
        if any(v for k, v in contacts.items() if k not in ("NOSE", "LEFT_MAIN", "RIGHT_MAIN")):
            raise RuntimeError(f"structure touched the ground during the takeoff roll: {contacts}")
        if not any(contacts[w] for w in ("NOSE", "LEFT_MAIN", "RIGHT_MAIN")):
            return TakeoffRoll(static_rpm, distance, s.cas_mps, s.theta_rad, to_reference)
    raise RuntimeError("no lift-off within 120 s")


def max_climb_rate_mps(
    aircraft: str, loading: Loading, cas_mps: float, alt_msl_m: float = 30.0, settle_s: float = 20.0, measure_s: float = 20.0,
    atmosphere: Atmosphere | None = None,
) -> float:
    """Full-throttle climb at a held calibrated airspeed, flaps up (POH Figure 5-5): trimmed
    level at `alt_msl_m`, then full throttle with the pitch attitude holding the speed
    (speed on pitch, pitch on elevator). Mean climb rate over `measure_s` after `settle_s`."""
    core, _, trim = trim_at_cas(aircraft, alt_msl_m, cas_mps, loading, atmosphere=atmosphere)
    s, i_err, dt = core.state(), 0.0, core.dt_s
    theta_ref, alt0 = s.theta_rad, None
    for k in range(round((settle_s + measure_s) / dt)):
        err = s.cas_mps - cas_mps  # too fast -> nose up
        i_err += err * dt
        theta_cmd = theta_ref + 0.02 * err + 0.004 * i_err
        elevator = trim.elevator + 4.0 * (s.theta_rad - theta_cmd) + 1.0 * s.q_radps  # elevator + = nose down
        s = core.step(replace(trim, throttle=1.0, elevator=max(-1.0, min(1.0, elevator)), aileron=-2.0 * s.phi_rad - 0.3 * s.p_radps))
        if alt0 is None and (k + 1) * dt >= settle_s:
            alt0 = s.alt_msl_m
    return (s.alt_msl_m - alt0) / measure_s
