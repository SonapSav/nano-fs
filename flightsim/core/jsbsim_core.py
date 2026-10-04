"""JSBSim-backed physics core.

The only module that talks to JSBSim. It converts JSBSim's imperial units to
SI at the boundary, so everything returned is a `State` in SI. It knows
nothing about wall-clock time: each `step` advances exactly one fixed `dt_s`.
"""

import hashlib
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import jsbsim
import numpy as np

from flightsim.core.types import Controls, LinearModel, MassProperties, State

FT_TO_M = 0.3048
IN_TO_M = 0.0254
SLUG_TO_KG = 14.593902937
LBF_TO_N = 4.4482216152605
LBM_TO_KG = 0.45359237
HP_TO_W = 745.69987158227
SLUGFT3_TO_KGM3 = SLUG_TO_KG / FT_TO_M**3

# Controls field -> JSBSim command property. All are normalized, so no unit conversion.
_CONTROL_PROPS = {
    "elevator": "fcs/elevator-cmd-norm",
    "aileron": "fcs/aileron-cmd-norm",
    "rudder": "fcs/rudder-cmd-norm",
    "throttle": "fcs/throttle-cmd-norm",
    "mixture": "fcs/mixture-cmd-norm",
    "flaps": "fcs/flap-cmd-norm",
    "pitch_trim": "fcs/pitch-trim-cmd-norm",
}


# JSBSim linearization state name -> (State field name, factor to SI)
_LIN_STATES = {
    "Vt": ("tas_mps", FT_TO_M),
    "Alpha": ("alpha_rad", 1.0),
    "Theta": ("theta_rad", 1.0),
    "Q": ("q_radps", 1.0),
    "Rpm0": ("engine_rpm", 1.0),
    "Beta": ("beta_rad", 1.0),
    "Phi": ("phi_rad", 1.0),
    "P": ("p_radps", 1.0),
    "Psi": ("psi_rad", 1.0),
    "R": ("r_radps", 1.0),
    "Latitude": ("lat_rad", 1.0),
    "Longitude": ("lon_rad", 1.0),
    "Alt": ("alt_msl_m", FT_TO_M),
}
_LIN_INPUTS = {"ThtlCmd": "throttle", "DaCmd": "aileron", "DeCmd": "elevator", "DrCmd": "rudder"}


@dataclass(frozen=True)
class InitialConditions:
    alt_msl_m: float
    tas_mps: float
    heading_rad: float
    lat_rad: float = 0.0
    lon_rad: float = 0.0
    flight_path_rad: float = 0.0
    # Steady horizontal wind: velocity of the air mass (the direction it blows TOWARD), NED.
    wind_north_mps: float = 0.0
    wind_east_mps: float = 0.0


@dataclass(frozen=True)
class Loading:
    """Payload and fuel. Point masses follow the aircraft file's order (c172p: pilot,
    co-pilot, left passenger, right passenger, baggage). None keeps the model default."""

    pointmasses_kg: tuple[float, ...] | None = None
    fuel_tanks_kg: tuple[float, ...] | None = None


class TrimError(RuntimeError):
    pass


def _silence_banner() -> None:
    # The FGFDMExec constructor prints a banner unless the global debug level is 0.
    jsbsim.FGJSBBase().debug_lvl = 0


def aircraft_hash(aircraft: str) -> str:
    """SHA-256 over the aircraft's definition files and the engine/propeller files it references."""
    root = Path(jsbsim.get_default_root_dir())
    files = sorted((root / "aircraft" / aircraft).glob("*.xml"))
    model = ET.parse(root / "aircraft" / aircraft / f"{aircraft}.xml").getroot()
    referenced = {e.get("file") for e in model.iter() if e.tag in ("engine", "thruster")}
    files += sorted(root / "engine" / f"{name}.xml" for name in referenced)
    h = hashlib.sha256()
    for f in files:
        h.update(str(f.relative_to(root)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


class JSBSimCore:
    def __init__(self, aircraft: str, dt_s: float):
        _silence_banner()
        self.aircraft = aircraft
        self.dt_s = dt_s
        self.jsbsim_version = jsbsim.__version__
        self._fdm = jsbsim.FGFDMExec(None)
        self._fdm.set_debug_level(0)
        if not self._fdm.load_model(aircraft):
            raise ValueError(f"JSBSim could not load aircraft {aircraft!r}")
        self._fdm.set_dt(dt_s)
        self._step_count = 0

    def reset(self, ic: InitialConditions, loading: Loading = Loading(), controls: Controls = Controls()) -> State:
        """Set initial conditions, loading and control commands. `controls` are also the
        starting point for `trim` (e.g. set flaps here to trim with flaps down)."""
        fdm = self._fdm
        self._apply(controls)
        for i, kg in enumerate(loading.pointmasses_kg or ()):
            fdm[f"inertia/pointmass-weight-lbs[{i}]"] = kg / LBM_TO_KG
        for i, kg in enumerate(loading.fuel_tanks_kg or ()):
            fdm[f"propulsion/tank[{i}]/contents-lbs"] = kg / LBM_TO_KG
        fdm["ic/h-sl-ft"] = ic.alt_msl_m / FT_TO_M
        if ic.wind_north_mps or ic.wind_east_mps:
            # JSBSim's IC wind handling only gives an air-relative start (zero sideslip, crabbed
            # into the wind) if the ground velocity is set directly: ground = air + wind.
            # ic/vw-north-fps ignores writes, so the wind goes in as magnitude + direction
            # (ic/vw-dir-deg is the direction the air moves toward). Verified 2026-10-04.
            fdm["ic/vw-mag-fps"] = math.hypot(ic.wind_north_mps, ic.wind_east_mps) / FT_TO_M
            fdm["ic/vw-dir-deg"] = math.degrees(math.atan2(ic.wind_east_mps, ic.wind_north_mps))
            fdm["ic/psi-true-rad"] = ic.heading_rad
            horizontal = ic.tas_mps * math.cos(ic.flight_path_rad)
            fdm["ic/vn-fps"] = (horizontal * math.cos(ic.heading_rad) + ic.wind_north_mps) / FT_TO_M
            fdm["ic/ve-fps"] = (horizontal * math.sin(ic.heading_rad) + ic.wind_east_mps) / FT_TO_M
            fdm["ic/vd-fps"] = -ic.tas_mps * math.sin(ic.flight_path_rad) / FT_TO_M
        else:
            fdm["ic/vw-mag-fps"] = 0.0
            fdm["ic/vt-fps"] = ic.tas_mps / FT_TO_M
            fdm["ic/psi-true-rad"] = ic.heading_rad
            fdm["ic/gamma-rad"] = ic.flight_path_rad
        fdm["ic/lat-geod-rad"] = ic.lat_rad
        fdm["ic/long-gc-rad"] = ic.lon_rad
        self.set_gust_ned_mps(0.0, 0.0, 0.0)  # gusts are not part of the IC and survive run_ic
        if not fdm.run_ic():
            raise RuntimeError("JSBSim run_ic failed")
        fdm["propulsion/set-running"] = -1  # all engines running
        self._step_count = 0
        return self.state()

    def trim(self) -> Controls:
        """Trim for steady flight at the current initial conditions; returns the trim controls.

        Uses JSBSim's full trim, which adjusts throttle, pitch trim, aileron and rudder.
        """
        try:
            self._fdm["simulation/do_simple_trim"] = 1
        except jsbsim.TrimFailureError as e:
            raise TrimError(str(e)) from e
        return self.controls()

    def set_gust_ned_mps(self, north: float, east: float, down: float) -> None:
        """Turbulence velocity added to the steady wind from the next step on (NED, m/s)."""
        self._fdm["atmosphere/gust-north-fps"] = north / FT_TO_M
        self._fdm["atmosphere/gust-east-fps"] = east / FT_TO_M
        self._fdm["atmosphere/gust-down-fps"] = down / FT_TO_M

    def _apply(self, controls: Controls) -> None:
        for name, prop in _CONTROL_PROPS.items():
            self._fdm[prop] = getattr(controls, name)

    def mass_properties(self) -> MassProperties:
        f = self._fdm
        return MassProperties(
            mass_kg=f["inertia/mass-slugs"] * SLUG_TO_KG,
            cg_x_m=f["inertia/cg-x-in"] * IN_TO_M,
            cg_y_m=f["inertia/cg-y-in"] * IN_TO_M,
            cg_z_m=f["inertia/cg-z-in"] * IN_TO_M,
        )

    def controls(self) -> Controls:
        return Controls(**{name: self._fdm[prop] for name, prop in _CONTROL_PROPS.items()})

    def linearize(self) -> LinearModel:
        """Linear model about the current state (call after `trim`), converted to SI."""
        lin = jsbsim.FGLinearization(self._fdm)
        # FGLinearization suspends integration (dt = 0) and resume_integration() does not
        # undo it, so restore the timestep explicitly or later steps silently freeze.
        self._fdm.set_dt(self.dt_s)
        names, scale = zip(*(_LIN_STATES[n] for n in lin.x_names))
        s = np.diag(scale)
        s_inv = np.diag(1.0 / np.asarray(scale))
        return LinearModel(
            a=s @ lin.system_matrix @ s_inv,
            b=s @ lin.input_matrix,
            state_names=names,
            input_names=tuple(_LIN_INPUTS[n] for n in lin.u_names),
        )

    def step(self, controls: Controls) -> State:
        self._apply(controls)
        t_before = self._fdm.get_sim_time()
        if not self._fdm.run():
            raise RuntimeError("JSBSim run() returned False")
        if not math.isclose(self._fdm.get_sim_time() - t_before, self.dt_s, rel_tol=1e-9):
            raise RuntimeError("JSBSim did not advance by dt (integration suspended?)")
        self._step_count += 1
        return self.state()

    def state(self) -> State:
        f = self._fdm
        return State(
            # Integer step count avoids accumulating float error in time.
            t_s=self._step_count * self.dt_s,
            lat_rad=f["position/lat-geod-rad"],
            lon_rad=f["position/long-gc-rad"],
            alt_msl_m=f["position/h-sl-ft"] * FT_TO_M,
            alt_agl_m=f["position/h-agl-ft"] * FT_TO_M,
            v_north_mps=f["velocities/v-north-fps"] * FT_TO_M,
            v_east_mps=f["velocities/v-east-fps"] * FT_TO_M,
            v_down_mps=f["velocities/v-down-fps"] * FT_TO_M,
            u_mps=f["velocities/u-aero-fps"] * FT_TO_M,
            v_mps=f["velocities/v-aero-fps"] * FT_TO_M,
            w_mps=f["velocities/w-aero-fps"] * FT_TO_M,
            phi_rad=f["attitude/phi-rad"],
            theta_rad=f["attitude/theta-rad"],
            psi_rad=f["attitude/psi-rad"],
            p_radps=f["velocities/p-rad_sec"],
            q_radps=f["velocities/q-rad_sec"],
            r_radps=f["velocities/r-rad_sec"],
            # Non-gravitational force / mass = specific force, as an accelerometer at the CG reads.
            ax_mps2=f["forces/fbx-total-lbs"] / f["inertia/mass-slugs"] * FT_TO_M,
            ay_mps2=f["forces/fby-total-lbs"] / f["inertia/mass-slugs"] * FT_TO_M,
            az_mps2=f["forces/fbz-total-lbs"] / f["inertia/mass-slugs"] * FT_TO_M,
            tas_mps=f["velocities/vt-fps"] * FT_TO_M,
            cas_mps=f["velocities/vc-fps"] * FT_TO_M,
            alpha_rad=f["aero/alpha-rad"],
            beta_rad=f["aero/beta-rad"],
            mach=f["velocities/mach"],
            air_density_kgpm3=f["atmosphere/rho-slugs_ft3"] * SLUGFT3_TO_KGM3,
            elevator_pos_rad=f["fcs/elevator-pos-rad"],
            aileron_left_pos_rad=f["fcs/left-aileron-pos-rad"],
            aileron_right_pos_rad=f["fcs/right-aileron-pos-rad"],
            rudder_pos_rad=f["fcs/rudder-pos-rad"],
            flap_pos_rad=f["fcs/flap-pos-rad"],
            engine_rpm=f["propulsion/engine/engine-rpm"],
            engine_power_w=f["propulsion/engine/power-hp"] * HP_TO_W,
            thrust_n=f["propulsion/engine/thrust-lbs"] * LBF_TO_N,
            mass_kg=f["inertia/mass-slugs"] * SLUG_TO_KG,
            fuel_mass_kg=f["propulsion/total-fuel-lbs"] * LBM_TO_KG,
            wind_north_mps=f["atmosphere/total-wind-north-fps"] * FT_TO_M,
            wind_east_mps=f["atmosphere/total-wind-east-fps"] * FT_TO_M,
            wind_down_mps=f["atmosphere/total-wind-down-fps"] * FT_TO_M,
        )
