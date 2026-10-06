"""JSBSim-backed physics core.

The only module that talks to JSBSim. It converts JSBSim's imperial units to
SI at the boundary, so everything returned is a `State` in SI. It knows
nothing about wall-clock time: each `step` advances exactly one fixed `dt_s`.
"""

import hashlib
from functools import cache
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

import jsbsim
import numpy as np

from flightsim.core.types import Controls, EngineStatus, LinearModel, MassProperties, State

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
    "brake": "fcs/left-brake-cmd-norm",  # also written to the right brake (_apply)
}
_RIGHT_BRAKE = "fcs/right-brake-cmd-norm"
# Nosewheel steering. The pedals move the rudder and the nosewheel together (as in the
# C172), but the c172p model does not link them: rudder-cmd-norm leaves the steering at 0.
# steer-cmd-norm +1 turns the nose right (10 deg), rudder + is nose left, hence the sign.
_STEER = "fcs/steer-cmd-norm"


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


@cache
def _contact_points(aircraft: str) -> tuple[tuple[str, str, tuple[float, float, float]], ...]:
    """(name, type, structural location in inches) of each ground contact point, in JSBSim's
    unit order, from the aircraft file."""
    root = Path(jsbsim.get_default_root_dir())
    model = ET.parse(root / "aircraft" / aircraft / f"{aircraft}.xml").getroot()
    points = []
    for c in model.iter("contact"):
        loc = c.find("location")
        scale = {"IN": 1.0, "FT": 12.0, "M": 1 / 0.0254}[loc.get("unit", "IN").upper()]
        points.append((c.get("name"), c.get("type"), tuple(float(loc.find(k).text) * scale for k in ("x", "y", "z"))))
    return tuple(points)


def point_height_in(point_in, cg_in, phi_rad: float, theta_rad: float, cg_agl_in: float) -> float:
    """Height (inches) above the local ground plane of a structural point (x aft, y right,
    z up, inches) for a CG at cg_agl_in and the given bank and pitch."""
    bx, by, bz = cg_in[0] - point_in[0], point_in[1] - cg_in[1], cg_in[2] - point_in[2]  # body axes, z down
    down = -math.sin(theta_rad) * bx + math.sin(phi_rad) * math.cos(theta_rad) * by + math.cos(phi_rad) * math.cos(theta_rad) * bz
    return cg_agl_in - down


def contact_names(aircraft: str) -> tuple[str, ...]:
    """Names of the aircraft's ground contact points (wheels, skids, wingtips) in JSBSim's
    unit order (c172p: NOSE, LEFT_MAIN, RIGHT_MAIN, NOSE_SKID, TAIL_SKID, LEFT_TIP, RIGHT_TIP)."""
    return tuple(name for name, _, _ in _contact_points(aircraft))


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

    def reset(
        self, ic: InitialConditions, loading: Loading = Loading(), controls: Controls = Controls(), ground_elevation_m: float = 0.0
    ) -> State:
        """Set initial conditions, loading and control commands. `controls` are also the
        starting point for `trim` (e.g. set flaps here to trim with flaps down).
        `ground_elevation_m` is the terrain height under the initial position; during a run,
        `set_ground_elevation_m` keeps it current (the gear and AGL follow it)."""
        fdm = self._fdm
        fdm["ic/terrain-elevation-ft"] = ground_elevation_m / FT_TO_M
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
        # Starting the engine resets the mixture command to full rich (verified 2026-10-05;
        # other commands are kept), so apply the requested mixture again.
        fdm[_CONTROL_PROPS["mixture"]] = controls.mixture
        self._step_count = 0
        return self.state()

    def reset_on_ground(
        self, heading_rad: float, loading: Loading = Loading(), controls: Controls = Controls(),
        ground_elevation_m: float = 0.0, lat_rad: float = 0.0, lon_rad: float = 0.0,
        speed_mps: float = 0.0, settle_s: float = 10.0, wind_north_mps: float = 0.0, wind_east_mps: float = 0.0,
    ) -> State:  # fmt: skip
        """Start on the wheels, engine idling, then apply `controls`.

        JSBSim starts the engine at about 2470 RPM whatever the throttle, so the aircraft
        first sits for `settle_s` with the brakes set, the throttle closed and the stick
        and pedals neutral while the engine spins down to idle. With `speed_mps` > 0 it then restarts rolling along
        `heading_rad` at that ground speed, keeping the engine state (run_ic does not reset
        it). JSBSim's ground trim puts the gear in equilibrium both times; it fails at speed
        with the brakes set, so they are released first.

        A steady wind (the velocity of the air, NED) builds up over the first half of the
        settle, at rest on the brakes: switched on at once, a crosswind jolts the parked
        aircraft. Rolling starts are calm only."""
        fdm = self._fdm
        idle = replace(controls, elevator=0.0, aileron=0.0, rudder=0.0, throttle=0.0, brake=1.0)
        self.reset(InitialConditions(ground_elevation_m + 1.4, 0.0, heading_rad, lat_rad, lon_rad), loading, idle, ground_elevation_m)
        self._ground_trim()
        n_settle = round(settle_s / self.dt_s)
        for k in range(n_settle):
            if wind_north_mps or wind_east_mps:
                frac = min(1.0, 2.0 * (k + 1) / n_settle)
                fdm["atmosphere/wind-north-fps"] = frac * wind_north_mps / FT_TO_M
                fdm["atmosphere/wind-east-fps"] = frac * wind_east_mps / FT_TO_M
            self.step(idle)
        if speed_mps > 0.0:
            if wind_north_mps or wind_east_mps:
                raise ValueError("a rolling start on the ground is calm only")
            rest = self.state()
            self._apply(replace(idle, brake=0.0))
            fdm["ic/h-sl-ft"] = rest.alt_msl_m / FT_TO_M
            fdm["ic/lat-geod-rad"] = rest.lat_rad
            fdm["ic/long-gc-rad"] = rest.lon_rad
            fdm["ic/vw-mag-fps"] = 0.0
            fdm["ic/phi-rad"] = 0.0
            fdm["ic/theta-rad"] = rest.theta_rad
            fdm["ic/psi-true-rad"] = heading_rad
            fdm["ic/vn-fps"] = speed_mps * math.cos(heading_rad) / FT_TO_M
            fdm["ic/ve-fps"] = speed_mps * math.sin(heading_rad) / FT_TO_M
            fdm["ic/vd-fps"] = 0.0
            fdm["ic/p-rad_sec"] = fdm["ic/q-rad_sec"] = fdm["ic/r-rad_sec"] = 0.0
            if not fdm.run_ic():
                raise RuntimeError("JSBSim run_ic failed")
            self._ground_trim()
        self._apply(controls)
        self._step_count = 0
        return self.state()

    def _ground_trim(self) -> None:
        try:
            self._fdm["simulation/do_simple_trim"] = 2  # JSBSim tGround: altitude and pitch on the gear
        except jsbsim.TrimFailureError as e:
            raise TrimError(f"ground trim: {e}") from e

    def add_steady_wind(self, wind_north_mps: float, wind_east_mps: float) -> State:
        """After `trim` in calm air: restart in a steady wind with the same air-relative state
        (position, attitude, controls; ground velocity = air velocity + wind). The engine
        keeps its trimmed state. A uniform steady wind does not change the flight relative
        to the air, so this is still trimmed, without JSBSim's trim in wind (which fails in
        strong winds at approach speeds; see CLAUDE.md)."""
        fdm, calm = self._fdm, self.state()
        fdm["ic/h-sl-ft"] = calm.alt_msl_m / FT_TO_M
        fdm["ic/lat-geod-rad"] = calm.lat_rad
        fdm["ic/long-gc-rad"] = calm.lon_rad
        fdm["ic/vw-mag-fps"] = math.hypot(wind_north_mps, wind_east_mps) / FT_TO_M
        fdm["ic/vw-dir-deg"] = math.degrees(math.atan2(wind_east_mps, wind_north_mps))
        fdm["ic/phi-rad"] = calm.phi_rad
        fdm["ic/theta-rad"] = calm.theta_rad
        fdm["ic/psi-true-rad"] = calm.psi_rad
        fdm["ic/vn-fps"] = (calm.v_north_mps + wind_north_mps) / FT_TO_M
        fdm["ic/ve-fps"] = (calm.v_east_mps + wind_east_mps) / FT_TO_M
        fdm["ic/vd-fps"] = calm.v_down_mps / FT_TO_M
        fdm["ic/p-rad_sec"] = calm.p_radps
        fdm["ic/q-rad_sec"] = calm.q_radps
        fdm["ic/r-rad_sec"] = calm.r_radps
        controls = self.controls()
        if not fdm.run_ic():
            raise RuntimeError("JSBSim run_ic failed")
        self._apply(controls)
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

    def contacts(self) -> dict[str, bool]:
        """Which contact points touch the ground now, by name (see contact_names). Wheels
        (BOGEY) report JSBSim's weight-on-wheels; JSBSim exposes nothing for STRUCTURE
        points (skids, wingtips), so those are found geometrically: the point's height
        above the local ground plane from the CG height and attitude."""
        f = self._fdm
        cg = (f["inertia/cg-x-in"], f["inertia/cg-y-in"], f["inertia/cg-z-in"])
        phi, theta, agl_in = f["attitude/phi-rad"], f["attitude/theta-rad"], f["position/h-agl-ft"] * 12.0
        return {
            name: bool(f[f"gear/unit[{i}]/WOW"]) if kind == "BOGEY" else point_height_in(point, cg, phi, theta, agl_in) <= 0.0
            for i, (name, kind, point) in enumerate(_contact_points(self.aircraft))
        }

    def set_ground_elevation_m(self, elevation_m: float) -> None:
        """Terrain height under the aircraft from the next step on. JSBSim treats the ground
        as level at this height around the aircraft (slopes under the gear are ignored)."""
        self._fdm["position/terrain-elevation-asl-ft"] = elevation_m / FT_TO_M

    def set_gust_ned_mps(self, north: float, east: float, down: float) -> None:
        """Turbulence velocity added to the steady wind from the next step on (NED, m/s)."""
        self._fdm["atmosphere/gust-north-fps"] = north / FT_TO_M
        self._fdm["atmosphere/gust-east-fps"] = east / FT_TO_M
        self._fdm["atmosphere/gust-down-fps"] = down / FT_TO_M

    def _apply(self, controls: Controls) -> None:
        for name, prop in _CONTROL_PROPS.items():
            self._fdm[prop] = getattr(controls, name)
        self._fdm[_RIGHT_BRAKE] = controls.brake
        self._fdm[_STEER] = -controls.rudder

    def engine(self) -> EngineStatus:
        """Fuel flow (mass, so independent of the model's fuel density) and EGT."""
        f = self._fdm
        return EngineStatus(
            fuel_flow_kgps=f["propulsion/engine/fuel-flow-rate-pps"] * LBM_TO_KG,
            egt_k=(f["propulsion/engine/egt-degF"] - 32.0) * 5.0 / 9.0 + 273.15,
        )

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
