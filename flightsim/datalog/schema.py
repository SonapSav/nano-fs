"""Log schema, version 1. The single definition of what a flight log contains.

Do not rename columns or change units without bumping SCHEMA_VERSION.
Columns are listed explicitly (not derived from State) so a change to the
core's State cannot silently change the log format; a test checks they agree.

One row per timestep. Row i holds state i and the command the controller
produced from it; the final row's commands are null.
"""

import pyarrow as pa

SCHEMA_VERSION = 1

RUN_COLUMNS = (
    pa.field("step", pa.int64(), nullable=False),
    pa.field("run_id", pa.string(), nullable=False),
    pa.field("seed", pa.int64(), nullable=False),
    pa.field("config_hash", pa.string(), nullable=False),
)

STATE_COLUMNS = (
    "t_s",
    "lat_rad", "lon_rad", "alt_msl_m", "alt_agl_m",
    "v_north_mps", "v_east_mps", "v_down_mps",
    "u_mps", "v_mps", "w_mps",
    "phi_rad", "theta_rad", "psi_rad",
    "p_radps", "q_radps", "r_radps",
    "ax_mps2", "ay_mps2", "az_mps2",
    "tas_mps", "cas_mps", "alpha_rad", "beta_rad", "mach", "air_density_kgpm3",
    "elevator_pos_rad", "aileron_left_pos_rad", "aileron_right_pos_rad", "rudder_pos_rad", "flap_pos_rad",
    "engine_rpm", "engine_power_w", "thrust_n",
    "mass_kg", "fuel_mass_kg",
    "wind_north_mps", "wind_east_mps", "wind_down_mps",
)  # fmt: skip

# Controls field -> log column
COMMAND_COLUMNS = {
    "elevator": "cmd_elevator_norm",
    "aileron": "cmd_aileron_norm",
    "rudder": "cmd_rudder_norm",
    "throttle": "cmd_throttle_norm",
    "mixture": "cmd_mixture_norm",
    "flaps": "cmd_flaps_norm",
    "pitch_trim": "cmd_pitch_trim_norm",
}

SCHEMA = pa.schema(
    [
        *RUN_COLUMNS,
        *(pa.field(name, pa.float64(), nullable=False) for name in STATE_COLUMNS),
        *(pa.field(name, pa.float64(), nullable=True) for name in COMMAND_COLUMNS.values()),
    ]
)

# File-level metadata keys (Parquet key-value metadata, all values are strings).
META_SCHEMA_VERSION = "flightsim.schema_version"
META_RUN_ID = "flightsim.run_id"
META_AIRCRAFT = "flightsim.aircraft"
META_AIRCRAFT_HASH = "flightsim.aircraft_hash"
META_JSBSIM_VERSION = "flightsim.jsbsim_version"
META_CONFIG_JSON = "flightsim.config_json"
META_TRIM_JSON = "flightsim.trim_json"
META_PILOT = "flightsim.pilot"  # optional: "human" for demonstrations


def make_run_id(config_hash: str, seed: int) -> str:
    """Deterministic: the same config and seed always give the same run id."""
    return f"{config_hash[:12]}-s{seed}"
