"""Free flight: no task. From rest on a stand, engine idling (the takeoff task's ground
start, JSBSimCore.reset_on_ground), the pilot does what they like: taxi, take off, fly,
land and take off again. No targets and no reward (0 every step). It ends only on

  crash      the tail, a wingtip or the nose touches the ground (envs/runway.py STRIKES),
             or the wheels touch down sinking faster than `free.max_sink_fpm`
  water      touching the sea or a lake (real-world scenery)

or after the config's episode_s (truncated). Landing is not an end.

Parking brake: set at the start (an idling C172 creeps off its stand, about 11 kt within a
minute on level ground with the brakes off) and released the first time the pilot's brake
command passes half (B pressed); until then the brakes are at least PARKING_BRAKE, whatever
the pilot's brake command. Not fully on: JSBSim's fully braked gear in wind rocks the
aircraft onto its tail (CLAUDE.md, JSBSim pitfalls); 0.3, the ground start's wind hold,
holds it at idle and up to 20 % power in calm air. Applied in the env, so it reaches the physics through the action as any
other command. Buildings are not
known to the physics (only the terrain), so flying into one is not a crash.

Rigging (`free.rigging`): the aileron and rudder commands the centred stick and pedals give
(the episode's trim; the pilot's input is relative to it). The airborne tasks start
trimmed by JSBSim, so centred flies straight there; parked, there is no such trim, and with
centred controls at zero the model rolls left hard (propeller torque, P-factor: 30-50 deg
of bank in 20 s hands-off at 70-110 kt). As a mechanic rigs a real 172 to fly hands-off in
cruise, the free flight centres on the model's trim at 90 kt (the owner's choice A,
2026-10-15): hands-off nearly straight at cruise, a little right at 110 kt, left at climb
power. A constant command offset through the action like any other command.

Wind (`free.wind`, optional): the runway tasks' low-altitude wind (MIL-F-8785C 3.7.3: log
profile and Dryden turbulence aligned with the wind), built up while the aircraft waits on
its stand; its crosswind and tailwind limits are against the stand's heading (the windy
configs: no tailwind, at most 15 kt across, as the takeoff tasks: with 4-7 kt of tailwind
or more the parked aircraft tips onto its tail in JSBSim, verified over 40 seeds 2026-10-15). Above 1000 ft the mean wind and the turbulence stay as at
1000 ft (project choice: the model's low-altitude band ends there; its log profile would
keep growing).
"""

import math

import numpy as np

from flightsim.core import Controls, InitialConditions, State
from flightsim.envs.altitude_heading import AltitudeHeadingHoldEnv
from flightsim.envs.runway import STRIKES, WHEELS, LowAltitudeGusts, draw_low_altitude_wind, wind_at_height_mps, wind_report

PARKING_BRAKE = 0.3
GUST_AIRSPEED_MPS = 50.0  # the turbulence filters' airspeed (project choice: a C172's cruise, ~100 kt)
WIND_TOP_M = 1000 * 0.3048


class FreeFlightEnv(AltitudeHeadingHoldEnv):
    def __init__(self, cfg, record: bool = False):
        super().__init__(cfg, record=record)
        if cfg.free is None:
            raise ValueError("the free flight needs a `free` section")

    def _draw_start(self, rng: np.random.Generator) -> tuple[InitialConditions, float, float]:
        f = self.cfg.free
        lat, lon = self.cfg.geodesy.to_geodetic(f.start_north_m, f.start_east_m)
        self.stand_elevation_m = self._ground_m(lat, lon)
        self.free_wind = draw_low_altitude_wind(f.wind, f.start_heading_rad, rng)
        ic = InitialConditions(alt_msl_m=self.stand_elevation_m + 1.4, tas_mps=0.0, heading_rad=f.start_heading_rad % (2 * math.pi),
                               lat_rad=lat, lon_rad=lon)  # fmt: skip
        return ic, ic.alt_msl_m, ic.heading_rad  # no targets: the start's, unused

    def _start_controls(self) -> Controls:
        f = self.cfg.free  # parked: the parking brake; centred stick and pedals at the rigging
        return Controls(throttle=0.0, brake=PARKING_BRAKE, aileron=f.rigging_aileron, rudder=f.rigging_rudder)

    def _start_core(self, ic: InitialConditions) -> tuple[Controls, State]:
        w, wind_n, wind_e = self.free_wind, 0.0, 0.0
        if w is not None:
            w["start_mps"] = wind_at_height_mps(w["u20_mps"], 1.4)
            wind_n, wind_e = w["start_mps"] * w["to_north"], w["start_mps"] * w["to_east"]
        u = self._start_controls()
        state = self._core.reset_on_ground(
            ic.heading_rad, self.cfg.loading, u, ground_elevation_m=self.stand_elevation_m,
            lat_rad=ic.lat_rad, lon_rad=ic.lon_rad, wind_north_mps=wind_n, wind_east_mps=wind_e,
        )  # fmt: skip
        return u, state

    def _on_reset(self) -> None:
        w = self.free_wind
        self._gusts = None if w is None else LowAltitudeGusts(w, GUST_AIRSPEED_MPS, 1.0 / self.cfg.sim_rate_hz)
        self.crash: str | None = None
        self.on_ground = True
        self.landings = 0  # touchdowns after flight
        self.parking_brake = "brake" in self.action_names
        self._airborne = False

    def _sim_step(self, u: Controls) -> State:
        prev = self._state
        if self._gusts is not None:
            height = min(max(0.0, prev.alt_agl_m), WIND_TOP_M)
            self._core.set_gust_ned_mps(*self._gusts.step(height))
        s = super()._sim_step(u)
        self._track(prev, s)
        return s

    def _track(self, prev: State, s: State) -> None:
        contacts = self._core.contacts()
        for point, reason in STRIKES.items():
            if contacts.get(point) and self.crash is None:
                self.crash = reason
        on_ground = any(contacts[w] for w in WHEELS)
        if on_ground and not self.on_ground and self._airborne:
            sink = max(prev.v_down_mps, s.v_down_mps)
            if sink > self.cfg.free.max_sink_mps and self.crash is None:
                self.crash = "hard_landing"
            self.landings += 1
            self._airborne = False
        if not on_ground and s.alt_agl_m > 3.0:  # really flying, not a bounce on the roll
            self._airborne = True
        self.on_ground = on_ground

    def step(self, action):
        if self.parking_brake:
            action = np.array(action, dtype=np.float32)
            i = self.action_names.index("brake")
            if (float(action[i]) + 1.0) / 2.0 > 0.5:
                self.parking_brake = False  # released; this command applies as given
            else:
                action[i] = max(float(action[i]), 2.0 * PARKING_BRAKE - 1.0)
        return super().step(action)

    def _reward(self, action: np.ndarray) -> float:
        self.last_comfort_cost, self.last_comfort_terms = 0.0, {}
        return 0.0

    def _termination_reason(self) -> str | None:
        return self.crash

    def max_step_cost(self) -> float:
        return 0.0

    def conditions(self) -> dict:
        w = self.free_wind
        return {
            "start_north_m": self.cfg.free.start_north_m, "start_east_m": self.cfg.free.start_east_m,
            "start_heading_deg": math.degrees(self.cfg.free.start_heading_rad),
            "wind_u20_mps": w["u20_mps"] if w else 0.0, "wind_from_deg": w["from_deg"] if w else 0.0,
            "turbulence": bool(w and w["turbulence"]),
        }  # fmt: skip

    def wind_info(self) -> dict | None:
        """The wind as a pilot is told it (envs/runway.py wind_report), or None when calm."""
        return wind_report(self.free_wind)

    def _info(self) -> dict:
        info = super()._info()
        info["free"] = ({"crash": self.crash, "landings": self.landings, "on_ground": self.on_ground, "parking_brake": self.parking_brake}
                        if hasattr(self, "crash") else None)  # fmt: skip
        return info
