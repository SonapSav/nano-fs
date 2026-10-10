"""Environment configuration, loaded from YAML and hashed like run configs."""

import math
from dataclasses import dataclass
from pathlib import Path

from flightsim.config import canonical_json, config_hash, load_raw, parse_loading
from flightsim.core import Atmosphere, InitialConditions, Loading
from flightsim.world.geo import Geodesy


# Controls a task may give its pilot, in action-vector order. Every task has the first four;
# flaps, pitch trim and brake are optional (held at trim otherwise; the brake is 0 at trim).
BASE_ACTIONS = ("elevator", "aileron", "rudder", "throttle")
OPTIONAL_ACTIONS = ("flaps", "pitch_trim", "brake")


@dataclass(frozen=True)
class ComfortConfig:
    """Soft envelope: only the excess beyond each threshold is penalized,
    as weight * min((excess / scale)^2, clip)."""

    bank_threshold_rad: float
    bank_scale_rad: float
    load_factor_dev_threshold: float  # |n - 1| in g
    load_factor_dev_scale: float
    climb_threshold_mps: float  # |vertical speed|
    climb_scale_mps: float
    w_bank: float
    w_load_factor: float
    w_climb: float
    # Flap overspeed: airspeed (CAS) above the flap-extended limit for the current flap
    # position. None = not scored.
    flap_vfe_10_mps: float | None = None  # limit with flaps extended up to 10 deg
    flap_vfe_full_mps: float | None = None  # limit with flaps beyond 10 deg
    flap_overspeed_scale_mps: float = 1.0
    w_flap_overspeed: float = 0.0


@dataclass(frozen=True)
class RewardConfig:
    alt_scale_m: float
    heading_scale_rad: float
    tas_scale_mps: float
    w_alt: float
    w_heading: float
    w_tas: float
    w_action_rate: float
    clip: float
    termination_penalty: float
    comfort: ComfortConfig | None = None
    # Charge each decision step left after a termination at the largest possible per-step
    # cost, so ending an episode early never scores better than flying on badly.
    charge_remaining_steps: bool = False


@dataclass(frozen=True)
class TerminationConfig:
    max_alt_error_m: float
    max_bank_rad: float
    max_alpha_rad: float
    min_alt_agl_m: float
    min_load_factor: float | None = None  # g; structural limits, None = not checked
    max_load_factor: float | None = None
    flap_overspeed_margin_mps: float | None = None  # end the flight this far above the flap limit


@dataclass(frozen=True)
class WindConfig:
    """Per-episode wind: steady speed uniform in a range, direction uniform over 360 deg (or
    over `from_deg`, a prevailing wind), turbulence level drawn with the given
    probabilities (Dryden, MIL-F-8785C)."""

    steady_speed_mps: tuple[float, float]
    turbulence_sigma_mps: dict[str, float]  # level name -> RMS intensity
    turbulence_probability: dict[str, float]  # level name -> probability (sums to 1)
    scale_length_m: float
    from_deg: tuple[float, float] | None = None  # the direction the wind comes FROM (true), uniform in this range


KT_TO_MPS = 1852.0 / 3600.0
FPM_TO_MPS = 0.3048 / 60.0


@dataclass(frozen=True)
class ApproachConfig:
    """Approach and landing task (envs/approach.py). Positions are metres north/east of
    the airfield (the world origin); along-runway distances are measured from the
    threshold in the landing direction."""

    threshold_north_m: float
    threshold_east_m: float
    runway_heading_rad: float
    runway_length_m: float
    runway_width_m: float
    glide_path_rad: float
    aim_point_m: float  # past the threshold, where the glide path meets the runway
    start_distance_m: float  # before the aim point, on the extended centreline
    start_kias: float
    start_flaps: float  # normalized, 1 = 30 deg
    randomize_lateral_m: float
    randomize_vertical_m: float
    randomize_kias: float
    randomize_heading_rad: float
    target_cas_mps: float
    touchdown_zone_m: tuple[float, float]  # past the threshold
    max_sink_mps: float  # at touchdown; harder is a hard landing
    max_bank_rad: float  # at touchdown
    lost_vertical_m: float  # this far off the glide path ends the approach
    lost_lateral_m: float
    settle_s: float  # all wheels down this long: landed
    max_ground_s: float  # after first contact, the episode ends anyway
    reward: dict  # weights and scales, see configs/envs/approach_landing.yaml
    max_drift_rad: float = math.inf  # crab (track - heading) at touchdown; more is a side load
    wind: dict | None = None  # low-altitude wind (MIL-F-8785C 3.7.3), see approach_landing_crosswind.yaml
    # Rollout to a full stop (when configured): landed = stopped on the runway, slower than
    # this; not stopped within max_ground_s of the first contact is a failure (no_stop).
    # Without it, landed = all wheels down for settle_s.
    stop_speed_mps: float | None = None


@dataclass(frozen=True)
class EnvConfig:
    aircraft: str
    sim_rate_hz: float
    control_rate_hz: float
    episode_s: float
    nominal: InitialConditions
    randomize_alt_m: float
    randomize_tas_mps: float
    randomize_heading_rad: float
    target_alt_offset_m: float
    target_heading_offset_rad: float
    loading: Loading
    reward: RewardConfig
    termination: TerminationConfig
    wind: WindConfig | None
    actions: tuple[str, ...]
    config_hash: str
    config_json: str
    terrain: str = "flat"  # "flat" (0 m everywhere), "procedural" (the viewer's terrain) or "dem" (world.scenery)
    approach: ApproachConfig | None = None  # set: the approach and landing task
    takeoff: "TakeoffConfig | None" = None  # set: the takeoff and climb-out task
    circuit: "CircuitConfig | None" = None  # set (with `approach`): takeoff, traffic pattern, landing
    route: "RouteConfig | None" = None  # set: the navigation task (fly a route of waypoints)
    # Viewer conditions (visual only, never the physics): time_of_day, visibility, clouds;
    # each "auto" unless set (see VISUAL_OPTIONS). Part of the config, so replays match.
    visual: tuple[tuple[str, str], ...] = ()
    # Where the world sits on the Earth and how positions map to metres (world/geo.py);
    # configs without a `world` block (logs before 2026-10-08) use the original sphere.
    geodesy: Geodesy = Geodesy()
    # A built real-world region (world/scenery.py) for terrain "dem": the config's world.scenery.
    scenery: str | None = None
    # A non-standard day (the config's `atmosphere`), or None: the ISA standard day.
    atmosphere: Atmosphere | None = None

    @property
    def sim_steps_per_action(self) -> int:
        n = self.sim_rate_hz / self.control_rate_hz
        if abs(n - round(n)) > 1e-9:
            raise ValueError("sim_rate_hz must be a whole multiple of control_rate_hz")
        return round(n)

    @property
    def max_decisions(self) -> int:
        return round(self.episode_s * self.control_rate_hz)


def load_env_config(path: str | Path, overrides: dict | None = None) -> EnvConfig:
    return env_config_from_raw(load_raw(path, overrides))


TERRAIN_MODELS = ("flat", "procedural", "dem")


def _parse_terrain(name) -> str:
    if name not in TERRAIN_MODELS:
        raise ValueError(f"terrain must be one of {TERRAIN_MODELS}, got {name!r}")
    return name


def _parse_atmosphere(a: dict | None) -> Atmosphere | None:
    """`atmosphere: {sea_level_temperature_c, sea_level_pressure_hpa}` (either may be left
    out: standard): the ISA temperature profile shifted to that sea-level temperature."""
    if not a:
        return None
    unknown = set(a) - {"sea_level_temperature_c", "sea_level_pressure_hpa"}
    if unknown:
        raise ValueError(f"unknown atmosphere keys {sorted(unknown)}")
    return Atmosphere(
        temperature_offset_k=float(a.get("sea_level_temperature_c", 15.0)) - 15.0,
        sea_level_pressure_pa=float(a.get("sea_level_pressure_hpa", 1013.25)) * 100.0,
    )


def _parse_scenery(raw: dict) -> str | None:
    name = (raw.get("world") or {}).get("scenery")
    if raw.get("terrain") == "dem" and not name:
        raise ValueError("terrain: dem needs world.scenery (a built real-world region)")
    if name and raw.get("terrain") != "dem":
        raise ValueError("world.scenery is used with terrain: dem")
    return str(name) if name else None


def env_config_from_raw(raw: dict) -> EnvConfig:
    ic, rnd, tg, rw, term = (raw[k] for k in ("initial_conditions", "randomize", "targets", "reward", "termination"))
    geodesy = Geodesy.from_config(raw.get("world"))
    start_lat, start_lon = geodesy.to_geodetic(float(ic.get("north_m", 0.0)), float(ic.get("east_m", 0.0)))
    return EnvConfig(
        aircraft=raw["aircraft"],
        sim_rate_hz=float(raw["sim_rate_hz"]),
        control_rate_hz=float(raw["control_rate_hz"]),
        episode_s=float(raw["episode_s"]),
        nominal=InitialConditions(
            alt_msl_m=float(ic["alt_msl_m"]), tas_mps=float(ic["tas_mps"]), heading_rad=math.radians(ic["heading_deg"]),
            # Optional start position, metres north and east of the airfield (the world origin).
            lat_rad=start_lat, lon_rad=start_lon,
        ),
        randomize_alt_m=float(rnd["alt_msl_m"]),
        randomize_tas_mps=float(rnd["tas_mps"]),
        randomize_heading_rad=math.radians(rnd["heading_deg"]),
        target_alt_offset_m=float(tg["alt_offset_m"]),
        target_heading_offset_rad=math.radians(tg["heading_offset_deg"]),
        loading=parse_loading(raw.get("loading")),
        reward=RewardConfig(
            alt_scale_m=float(rw["alt_scale_m"]),
            heading_scale_rad=math.radians(rw["heading_scale_deg"]),
            tas_scale_mps=float(rw["tas_scale_mps"]),
            w_alt=float(rw["w_alt"]),
            w_heading=float(rw["w_heading"]),
            w_tas=float(rw["w_tas"]),
            w_action_rate=float(rw["w_action_rate"]),
            clip=float(rw["clip"]),
            termination_penalty=float(rw["termination_penalty"]),
            comfort=_parse_comfort(rw.get("comfort")),
            charge_remaining_steps=bool(rw.get("charge_remaining_steps", False)),
        ),
        termination=TerminationConfig(
            max_alt_error_m=float(term["max_alt_error_m"]),
            max_bank_rad=math.radians(term["max_bank_deg"]),
            max_alpha_rad=math.radians(term["max_alpha_deg"]),
            min_alt_agl_m=float(term["min_alt_agl_m"]),
            min_load_factor=float(term["min_load_factor"]) if "min_load_factor" in term else None,
            max_load_factor=float(term["max_load_factor"]) if "max_load_factor" in term else None,
            flap_overspeed_margin_mps=(
                float(term["flap_overspeed_margin_mps"]) if "flap_overspeed_margin_mps" in term else None
            ),
        ),
        wind=_parse_wind(raw.get("wind")),
        actions=_parse_actions(raw.get("actions")),
        terrain=_parse_terrain(raw.get("terrain", "flat")),
        geodesy=geodesy,
        scenery=_parse_scenery(raw),
        atmosphere=_parse_atmosphere(raw.get("atmosphere")),
        approach=_parse_approach(raw.get("approach")),
        takeoff=_parse_takeoff(raw.get("takeoff")),
        circuit=_parse_circuit(raw.get("circuit")),
        route=_parse_route(raw.get("route"), geodesy),
        visual=_parse_visual(raw.get("visual")),
        config_hash=config_hash(raw),
        config_json=canonical_json(raw),
    )


@dataclass(frozen=True)
class TakeoffConfig:
    """Takeoff and climb-out task (envs/takeoff.py). Runway as in ApproachConfig; the
    takeoff runs in the runway direction from `start_along_m` past the threshold."""

    threshold_north_m: float
    threshold_east_m: float
    runway_heading_rad: float
    runway_length_m: float
    runway_width_m: float
    start_along_m: float
    start_flaps: float  # normalized, 1 = 30 deg
    randomize_lateral_m: float
    randomize_heading_rad: float
    climb_cas_mps: float  # climb-out speed the reward asks for once airborne
    target_height_m: float  # climbed this high above the runway: done
    lost_lateral_m: float  # this far off the extended centreline once airborne: failure
    max_ground_s: float  # still on the ground this long after the start: no_liftoff
    reward: dict  # weights and scales, see configs/envs/takeoff.yaml
    wind: dict | None = None  # low-altitude wind, as in the approach task


@dataclass(frozen=True)
class RouteConfig:
    """Navigation task (envs/navigation.py): fly a route of waypoints (envs/route.py) at a
    cruise altitude. Either named waypoints (map metres, converted from latitude/longitude
    when given that way) or a route drawn per seed (`random`)."""

    name: str
    waypoints: tuple | None  # of envs.route.Waypoint; None: random
    random: dict | None  # {waypoints: [min, max], leg_m: [min, max], turn_deg: [min, max]}
    start_north_m: float
    start_east_m: float
    start_course_rad: float | None  # first leg's course for random routes; None: drawn per seed
    randomize_heading_rad: float  # start heading: the first leg's course +/- this
    cruise_alt_m: float  # MSL
    turn_bank_rad: float  # bank the fly-by turn anticipation assumes
    max_xtk_m: float  # further off the active leg: failure (off_course)
    reward: dict  # xtk_scale_m, w_xtk, track_scale_deg, w_track, completion_bonus


# Visual conditions a task config may set (viewer only). "auto": time and visibility take
# the defaults below, clouds follow the flight's wind and turbulence.
VISUAL_OPTIONS = {
    "time_of_day": ("auto", "morning", "midday", "afternoon", "evening", "dusk", "night"),
    "visibility": ("auto", "clear", "normal", "hazy"),
    "clouds": ("auto", "clear", "few", "scattered", "broken"),
}


def _parse_visual(v: dict | None) -> tuple[tuple[str, str], ...]:
    v = dict(v or {})
    for key, value in v.items():
        if key not in VISUAL_OPTIONS or value not in VISUAL_OPTIONS[key]:
            raise ValueError(f"visual.{key} must be one of {VISUAL_OPTIONS.get(key, ())}, got {value!r}")
    return tuple(sorted(v.items()))


@dataclass(frozen=True)
class CircuitConfig:
    """Circuit task (envs/circuit.py): start as in the takeoff task, then a landing judged
    by the `approach` section (runway, glide path, limits, rollout, wind)."""

    start_along_m: float
    start_flaps: float
    randomize_lateral_m: float
    randomize_heading_rad: float
    min_height_m: float  # climbed this high before a landing counts (lower contact: sank_back)
    lost_distance_m: float  # this far from the runway midpoint: failure
    max_ground_s: float  # still on the ground this long after the start: no_liftoff


def _parse_circuit(c: dict | None) -> CircuitConfig | None:
    if not c:
        return None
    st, rnd, lim = c["start"], c["randomize"], c["limits"]
    return CircuitConfig(
        start_along_m=float(st["along_m"]),
        start_flaps=float(st["flaps"]),
        randomize_lateral_m=float(rnd["lateral_m"]),
        randomize_heading_rad=math.radians(rnd["heading_deg"]),
        min_height_m=float(c["min_height_ft"]) * 0.3048,
        lost_distance_m=float(lim["lost_distance_m"]),
        max_ground_s=float(lim["max_ground_s"]),
    )


def _parse_route(r: dict | None, geodesy) -> "RouteConfig | None":
    if r is None:
        return None
    from flightsim.envs.route import Waypoint

    waypoints = None
    if r.get("waypoints"):
        waypoints = []
        for k, w in enumerate(r["waypoints"]):
            if "lat_deg" in w:
                north, east = geodesy.to_map(math.radians(w["lat_deg"]), math.radians(w["lon_deg"]))
            else:
                north, east = float(w["north_m"]), float(w["east_m"])
            waypoints.append(Waypoint(str(w.get("name", f"WP{k + 1}")), north, east, bool(w.get("fly_over", False))))
        waypoints = tuple(waypoints)
    elif not r.get("random"):
        raise ValueError("route needs `waypoints` or `random`")
    start = r.get("start", {})
    return RouteConfig(
        name=str(r.get("name", "route")),
        waypoints=waypoints,
        random=dict(r["random"]) if not waypoints else None,
        start_north_m=float(start.get("north_m", 0.0)),
        start_east_m=float(start.get("east_m", 0.0)),
        start_course_rad=math.radians(r["start_course_deg"]) if r.get("start_course_deg") is not None else None,
        randomize_heading_rad=math.radians(float(r.get("randomize_heading_deg", 0.0))),
        cruise_alt_m=float(r["cruise_alt_ft"]) * 0.3048,
        turn_bank_rad=math.radians(float(r.get("turn_bank_deg", 25.0))),
        max_xtk_m=float(r.get("max_xtk_nm", 2.0)) * 1852.0,
        reward=dict(r.get("reward", {})),
    )


def _parse_takeoff(t: dict | None) -> TakeoffConfig | None:
    if not t:
        return None
    rw, st, rnd, lim = t["runway"], t["start"], t["randomize"], t["limits"]
    return TakeoffConfig(
        threshold_north_m=float(rw["threshold_north_m"]),
        threshold_east_m=float(rw["threshold_east_m"]),
        runway_heading_rad=math.radians(rw["heading_deg"]),
        runway_length_m=float(rw["length_m"]),
        runway_width_m=float(rw["width_m"]),
        start_along_m=float(st["along_m"]),
        start_flaps=float(st["flaps"]),
        randomize_lateral_m=float(rnd["lateral_m"]),
        randomize_heading_rad=math.radians(rnd["heading_deg"]),
        climb_cas_mps=float(t["climb_kias"]) * KT_TO_MPS,
        target_height_m=float(t["target_height_ft"]) * 0.3048,
        lost_lateral_m=float(lim["lost_lateral_m"]),
        max_ground_s=float(lim["max_ground_s"]),
        reward=dict(t["reward"]),
        wind=dict(t["wind"]) if t.get("wind") else None,
    )


def _parse_approach(a: dict | None) -> ApproachConfig | None:
    if not a:
        return None
    rw, st, rnd, lim = a["runway"], a["start"], a["randomize"], a["limits"]
    return ApproachConfig(
        threshold_north_m=float(rw["threshold_north_m"]),
        threshold_east_m=float(rw["threshold_east_m"]),
        runway_heading_rad=math.radians(rw["heading_deg"]),
        runway_length_m=float(rw["length_m"]),
        runway_width_m=float(rw["width_m"]),
        glide_path_rad=math.radians(a["glide_path_deg"]),
        aim_point_m=float(a["aim_point_m"]),
        start_distance_m=float(st["distance_m"]),
        start_kias=float(st["kias"]),
        start_flaps=float(st["flaps"]),
        randomize_lateral_m=float(rnd["lateral_m"]),
        randomize_vertical_m=float(rnd["vertical_m"]),
        randomize_kias=float(rnd["kias"]),
        randomize_heading_rad=math.radians(rnd["heading_deg"]),
        target_cas_mps=float(a["target_kias"]) * KT_TO_MPS,
        touchdown_zone_m=(float(a["touchdown_zone_m"][0]), float(a["touchdown_zone_m"][1])),
        max_sink_mps=float(lim["max_sink_fpm"]) * FPM_TO_MPS,
        max_bank_rad=math.radians(lim["max_bank_deg"]),
        lost_vertical_m=float(lim["lost_vertical_m"]),
        lost_lateral_m=float(lim["lost_lateral_m"]),
        settle_s=float(a["settle_s"]),
        max_ground_s=float(a["rollout"]["max_rollout_s"]) if a.get("rollout") else float(a["max_ground_s"]),
        reward=dict(a["reward"]),
        max_drift_rad=math.radians(lim["max_drift_deg"]) if "max_drift_deg" in lim else math.inf,
        wind=dict(a["wind"]) if a.get("wind") else None,
        stop_speed_mps=float(a["rollout"]["stop_speed_kt"]) * KT_TO_MPS if a.get("rollout") else None,
    )


def _parse_comfort(c: dict | None) -> ComfortConfig | None:
    if not c:
        return None
    return ComfortConfig(
        bank_threshold_rad=math.radians(c["bank_threshold_deg"]),
        bank_scale_rad=math.radians(c["bank_scale_deg"]),
        load_factor_dev_threshold=float(c["load_factor_dev_threshold"]),
        load_factor_dev_scale=float(c["load_factor_dev_scale"]),
        climb_threshold_mps=float(c["climb_threshold_mps"]),
        climb_scale_mps=float(c["climb_scale_mps"]),
        w_bank=float(c["w_bank"]),
        w_load_factor=float(c["w_load_factor"]),
        w_climb=float(c["w_climb"]),
        flap_vfe_10_mps=float(c["flap_vfe_10_mps"]) if "flap_vfe_10_mps" in c else None,
        flap_vfe_full_mps=float(c["flap_vfe_full_mps"]) if "flap_vfe_full_mps" in c else None,
        flap_overspeed_scale_mps=float(c.get("flap_overspeed_scale_mps", 1.0)),
        w_flap_overspeed=float(c.get("w_flap_overspeed", 0.0)),
    )


def _parse_actions(actions: list | None) -> tuple[str, ...]:
    if actions is None:
        return BASE_ACTIONS
    actions = tuple(actions)
    if actions[:4] != BASE_ACTIONS or any(a not in OPTIONAL_ACTIONS for a in actions[4:]) or len(set(actions)) != len(actions):
        raise ValueError(f"actions must be {list(BASE_ACTIONS)} followed by any of {list(OPTIONAL_ACTIONS)}, got {list(actions)}")
    return actions


def _parse_wind(w: dict | None) -> WindConfig | None:
    if not w:
        return None
    turb = w["turbulence"]
    sigma = {k: float(v) for k, v in turb["sigma_mps"].items()}
    prob = {k: float(v) for k, v in turb["probability"].items()}
    if set(prob) - set(sigma):
        raise ValueError(f"turbulence levels without an intensity: {set(prob) - set(sigma)}")
    if abs(sum(prob.values()) - 1.0) > 1e-9:
        raise ValueError("turbulence probabilities must sum to 1")
    lo, hi = (float(x) for x in w["steady_speed_mps"])
    from_deg = tuple(float(x) for x in w["from_deg"]) if "from_deg" in w else None
    return WindConfig((lo, hi), sigma, prob, float(turb["scale_length_m"]), from_deg)
