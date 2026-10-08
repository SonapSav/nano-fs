"""Routes for the navigation task: waypoints, legs and GPS-style sequencing.

Geometry is on the world's map (world/geo.py: metres north and east of the origin); a leg
is the straight map line between two points (within the tens of km of the procedural
world it differs from the geodesic by well under a metre). Directions here are map
bearings; true bearing = map bearing + the grid convergence (zero at the default origin).

Sequencing, as a GPS navigator does it:
- fly-by waypoints (default): the next leg becomes active when the distance to go along
  the leg falls to the turn anticipation, R tan(|turn| / 2) with the turn radius
  R = ground speed^2 / (g tan(bank)), so a turn at that bank rolls out on the next leg;
- fly-over waypoints, the last one, and turns over MAX_FLY_BY_DEG: when the aircraft
  passes abeam the waypoint (distance to go along the leg <= 0).

During a fly-by turn the path is the arc tangent to both legs with that radius (fixed at
the leg change): desired track and cross-track error are measured against the arc until
the aircraft passes its end on the next leg, so the turn is part of the path (as a GPS
navigator's turn transition), not an error.

The viewer has a port in flightsim/viewer/nav.js (tests compare them).
"""

import math
from dataclasses import dataclass

G0 = 9.80665
MAX_FLY_BY_DEG = 150.0  # sharper turns are flown over the waypoint


@dataclass(frozen=True)
class Waypoint:
    name: str
    north_m: float
    east_m: float
    fly_over: bool = False


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class Route:
    """A start point and waypoints; leg i runs from point i to point i + 1 (point 0 the start)."""

    def __init__(self, start_north_m: float, start_east_m: float, waypoints: list[Waypoint]):
        if not waypoints:
            raise ValueError("a route needs at least one waypoint")
        self.waypoints = list(waypoints)
        self.points = [(start_north_m, start_east_m), *((w.north_m, w.east_m) for w in waypoints)]
        self.courses = []  # map bearing of each leg (rad)
        self.lengths = []
        for (n0, e0), (n1, e1) in zip(self.points, self.points[1:]):
            length = math.hypot(n1 - n0, e1 - e0)
            if length < 1.0:
                raise ValueError("route points must be at least 1 m apart")
            self.courses.append(math.atan2(e1 - e0, n1 - n0))
            self.lengths.append(length)

    @property
    def legs(self) -> int:
        return len(self.waypoints)

    def turn_rad(self, leg: int) -> float:
        """Signed course change at the end of `leg` (+ right); 0 after the last leg."""
        return _wrap_pi(self.courses[leg + 1] - self.courses[leg]) if leg + 1 < self.legs else 0.0

    def as_dict(self) -> dict:
        n, e = self.points[0]
        return {"start": {"north_m": n, "east_m": e},
                "waypoints": [{"name": w.name, "north_m": w.north_m, "east_m": w.east_m, "fly_over": w.fly_over} for w in self.waypoints]}  # fmt: skip


def leg_geometry(route: Route, leg: int, north_m: float, east_m: float) -> tuple[float, float, float]:
    """(along, cross, to_go): metres from the leg's start along it, metres right of it, and
    the distance to go along it to its waypoint."""
    (n0, e0) = route.points[leg]
    c = route.courses[leg]
    dn, de = north_m - n0, east_m - e0
    along = dn * math.cos(c) + de * math.sin(c)
    cross = -dn * math.sin(c) + de * math.cos(c)
    return along, cross, route.lengths[leg] - along


def turn_radius_m(ground_speed_mps: float, bank_rad: float) -> float:
    return ground_speed_mps**2 / (G0 * math.tan(bank_rad))


def turn_anticipation_m(ground_speed_mps: float, turn_rad: float, bank_rad: float) -> float:
    """Distance before a fly-by waypoint to start a turn of `turn_rad` at `bank_rad`."""
    return turn_radius_m(ground_speed_mps, bank_rad) * math.tan(abs(turn_rad) / 2.0)


def is_fly_by(route: "Route", leg: int) -> bool:
    """Whether the waypoint ending `leg` is flown by (with a turn arc)."""
    return leg + 1 < route.legs and not route.waypoints[leg].fly_over and abs(route.turn_rad(leg)) <= math.radians(MAX_FLY_BY_DEG)


class Navigator:
    """The active leg of a route as the aircraft flies it, and the GPS quantities."""

    def __init__(self, route: Route, turn_bank_rad: float):
        self.route = route
        self.turn_bank_rad = turn_bank_rad
        self.active = 0  # active leg
        self.done = False  # passed the last waypoint
        self.sequenced_at: list[float] = []  # simulation times the legs ended
        self.arc: dict | None = None  # the turn in progress: centre, radius, direction, end point

    def update(self, north_m: float, east_m: float, ground_speed_mps: float, t_s: float, turn_speed_mps: float | None = None) -> None:
        """Sequence to the next leg when due, and end a turn at the arc's end (call at
        every simulation step). `turn_speed_mps`: the ground speed the turn arcs are sized
        for (default: the current ground speed); with wind, the fastest of the turn, true
        airspeed + wind speed, so a turn into a tailwind stays within the planned bank."""
        r = self.route
        if self.arc is not None:
            c = r.courses[self.active]
            bn, be = self.arc["end"]
            if (north_m - bn) * math.cos(c) + (east_m - be) * math.sin(c) >= 0.0:
                self.arc = None
        while not self.done:
            _, _, to_go = leg_geometry(r, self.active, north_m, east_m)
            fly_by = is_fly_by(r, self.active)
            radius = turn_radius_m(turn_speed_mps if turn_speed_mps is not None else ground_speed_mps, self.turn_bank_rad)
            anticipation = radius * math.tan(abs(r.turn_rad(self.active)) / 2.0) if fly_by else 0.0
            if to_go > anticipation:
                return
            self.sequenced_at.append(t_s)
            if self.active == r.legs - 1:
                self.done = True
                return
            if fly_by and anticipation > 1.0:
                self.arc = self._arc(self.active, radius, anticipation)
            self.active += 1

    def _arc(self, leg: int, radius: float, anticipation: float) -> dict:
        """The turn from `leg` to the next: tangent to both legs, `anticipation` before and
        after their common waypoint."""
        r = self.route
        wn, we = r.points[leg + 1]
        c1, c2, turn = r.courses[leg], r.courses[leg + 1], r.turn_rad(leg)
        side = 1.0 if turn > 0 else -1.0  # +1: right turn, centre to the right of the inbound leg
        an, ae = wn - anticipation * math.cos(c1), we - anticipation * math.sin(c1)
        centre = (an - side * radius * math.sin(c1), ae + side * radius * math.cos(c1))
        end = (wn + anticipation * math.cos(c2), we + anticipation * math.sin(c2))
        return {"centre": centre, "radius": radius, "side": side, "end": end}

    def quantities(self, north_m: float, east_m: float, track_map_rad: float) -> dict:
        """GPS quantities for the active leg (map bearings): desired track, cross-track error
        (+ right of the leg), track angle error (desired - actual), distance to go along the
        leg and direct to the waypoint, the turn at its end."""
        r, leg = self.route, self.active
        _, cross, to_go = leg_geometry(r, leg, north_m, east_m)
        dtk = r.courses[leg]
        if self.arc is not None:  # turning: the arc is the path
            a = self.arc
            cn, ce = a["centre"]
            dist = math.hypot(north_m - cn, east_m - ce)
            radial = math.atan2(east_m - ce, north_m - cn)  # bearing from the centre to the aircraft
            dtk = _wrap_pi(radial + a["side"] * math.pi / 2)
            cross = a["side"] * (a["radius"] - dist)  # right of the path: + (inside a right turn)
        wn, we = r.points[leg + 1]
        return {
            "leg": leg, "waypoint": r.waypoints[leg].name, "dtk_map_rad": dtk, "xtk_m": cross, "turning": self.arc is not None,
            "track_error_rad": _wrap_pi(dtk - track_map_rad), "to_go_m": to_go,
            "distance_m": math.hypot(wn - north_m, we - east_m), "next_turn_rad": r.turn_rad(leg),
            "arc_radius_m": self.arc["radius"] if self.arc else None, "arc_side": self.arc["side"] if self.arc else None,
        }  # fmt: skip


def random_route(rng, start_north_m: float, start_east_m: float, start_course_rad: float, n_waypoints: tuple[int, int],
                 leg_m: tuple[float, float], turn_deg: tuple[float, float]) -> Route:  # fmt: skip
    """A seeded random route: the first leg along `start_course_rad`, then turns of a random
    size (uniform in `turn_deg`) and direction. Draw order: count, then per leg its length,
    then per turn its size and direction."""
    n = int(rng.integers(n_waypoints[0], n_waypoints[1] + 1))
    pts, course = [], start_course_rad
    north, east = start_north_m, start_east_m
    for k in range(n):
        if k > 0:
            turn = math.radians(rng.uniform(*turn_deg)) * (1.0 if rng.uniform() < 0.5 else -1.0)
            course = _wrap_pi(course + turn)
        length = rng.uniform(*leg_m)
        north, east = north + length * math.cos(course), east + length * math.sin(course)
        pts.append(Waypoint(f"WP{k + 1}", north, east))
    return Route(start_north_m, start_east_m, pts)
