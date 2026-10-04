"""Physics-core interface types.

These are the only state/control definitions the rest of the project sees.
All quantities are SI (m, m/s, rad, rad/s, kg, N, W); normalized commands are
unitless. Nothing here refers to JSBSim, so the core can be replaced without
touching consumers.
"""

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Controls:
    """Pilot/autopilot commands. Ranges follow the usual cockpit conventions."""

    elevator: float = 0.0  # [-1, 1], positive = nose down (stick forward)
    aileron: float = 0.0  # [-1, 1], positive = roll right
    rudder: float = 0.0  # [-1, 1], positive = trailing edge left = nose LEFT (JSBSim convention)
    throttle: float = 0.0  # [0, 1]
    mixture: float = 1.0  # [0, 1]
    flaps: float = 0.0  # [0, 1], fraction of full flap travel
    pitch_trim: float = 0.0  # [-1, 1], added to elevator by the flight control system


@dataclass(frozen=True)
class State:
    """Full aircraft state at one instant."""

    t_s: float

    # Position
    lat_rad: float
    lon_rad: float
    alt_msl_m: float
    alt_agl_m: float

    # Velocity in the local North-East-Down frame
    v_north_mps: float
    v_east_mps: float
    v_down_mps: float

    # Velocity in body axes (x forward, y right, z down), relative to the air mass
    u_mps: float
    v_mps: float
    w_mps: float

    # Attitude (Euler angles, ZYX)
    phi_rad: float
    theta_rad: float
    psi_rad: float

    # Body angular rates
    p_radps: float
    q_radps: float
    r_radps: float

    # Accelerations at the CG in body axes, including gravity reaction (what an accelerometer reads)
    ax_mps2: float
    ay_mps2: float
    az_mps2: float

    # Air data
    tas_mps: float
    cas_mps: float
    alpha_rad: float
    beta_rad: float
    mach: float
    air_density_kgpm3: float

    # Control surface positions (actual, after the flight control system)
    elevator_pos_rad: float
    aileron_left_pos_rad: float
    aileron_right_pos_rad: float
    rudder_pos_rad: float
    flap_pos_rad: float

    # Propulsion
    engine_rpm: float
    engine_power_w: float
    thrust_n: float

    # Mass
    mass_kg: float
    fuel_mass_kg: float

    # Wind in the local NED frame (zero until wind models are added)
    wind_north_mps: float
    wind_east_mps: float
    wind_down_mps: float


STATE_FIELDS = tuple(f.name for f in fields(State))
CONTROL_FIELDS = tuple(f.name for f in fields(Controls))
