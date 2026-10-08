// Route navigation in the viewer: a port of flightsim/envs/route.py (sequencing with fly-by
// turn anticipation capped at half of each adjacent leg, one turn at a time, and turn arcs
// sized for the flight's planned turn speed, `turn_speed_mps` in the route as
// envs/navigation.py plans it), so the GPS, the map and the HUD show the active leg,
// desired track and cross-track error the task uses. Tests compare it with Python.
//
// The viewer follows a flight frame by frame (30 a second), so a leg can change up to one
// frame later than in the task (a metre or two). After a seek back or a replay that starts
// mid-flight, `NavTracker` picks the leg nearest the aircraft and carries on from there.
// Map bearings throughout; true = map + convergence (geo.js).

const G0 = 9.80665;
const MAX_FLY_BY_RAD = (150 * Math.PI) / 180;
const wrapPi = (a) => {
  const t = (a + Math.PI) % (2 * Math.PI);
  return (t < 0 ? t + 2 * Math.PI : t) - Math.PI;
};

export class Route {
  // `r`: the stream's route ({start: {north_m, east_m}, waypoints: [{name, north_m, east_m, fly_over}]}).
  constructor(r) {
    this.waypoints = r.waypoints;
    this.points = [[r.start.north_m, r.start.east_m], ...r.waypoints.map((w) => [w.north_m, w.east_m])];
    this.courses = [];
    this.lengths = [];
    for (let i = 0; i + 1 < this.points.length; i++) {
      const [n0, e0] = this.points[i], [n1, e1] = this.points[i + 1];
      this.courses.push(Math.atan2(e1 - e0, n1 - n0));
      this.lengths.push(Math.hypot(n1 - n0, e1 - e0));
    }
  }
  get legs() {
    return this.waypoints.length;
  }
  turn(leg) {
    return leg + 1 < this.legs ? wrapPi(this.courses[leg + 1] - this.courses[leg]) : 0;
  }
  isFlyBy(leg) {
    return leg + 1 < this.legs && !this.waypoints[leg].fly_over && Math.abs(this.turn(leg)) <= MAX_FLY_BY_RAD;
  }
}

// [along, cross, toGo] for a leg (cross: + right of it).
export function legGeometry(route, leg, n, e) {
  const [n0, e0] = route.points[leg], c = route.courses[leg];
  const dn = n - n0, de = e - e0;
  const along = dn * Math.cos(c) + de * Math.sin(c);
  return [along, -dn * Math.sin(c) + de * Math.cos(c), route.lengths[leg] - along];
}

export const turnRadius = (speed, bank) => (speed * speed) / (G0 * Math.tan(bank));

export class Navigator {
  constructor(route, bankRad, active = 0) {
    this.route = route;
    this.bank = bankRad;
    this.active = active;
    this.done = false;
    this.arc = null;
  }

  update(n, e, gs, turnSpeedMps = null) {
    const r = this.route;
    if (this.arc) {
      const c = r.courses[this.active], [bn, be] = this.arc.end;
      if ((n - bn) * Math.cos(c) + (e - be) * Math.sin(c) >= 0) this.arc = null;
    }
    while (!this.done && !this.arc) {
      const toGo = legGeometry(r, this.active, n, e)[2];
      const flyBy = r.isFlyBy(this.active);
      let radius = turnRadius(turnSpeedMps ?? gs, this.bank);
      let anticipation = 0;
      if (flyBy) {
        const halfTan = Math.tan(Math.abs(r.turn(this.active)) / 2);
        anticipation = Math.min(radius * halfTan, 0.5 * r.lengths[this.active], 0.5 * r.lengths[this.active + 1]);
        if (halfTan > 0) radius = anticipation / halfTan;
      }
      if (toGo > anticipation) return;
      if (this.active === r.legs - 1) {
        this.done = true;
        return;
      }
      if (flyBy && anticipation > 1) this.arc = this._arc(this.active, radius, anticipation);
      this.active += 1;
    }
  }

  _arc(leg, radius, anticipation) {
    const r = this.route, [wn, we] = r.points[leg + 1], c1 = r.courses[leg], c2 = r.courses[leg + 1];
    const side = r.turn(leg) > 0 ? 1 : -1;
    const an = wn - anticipation * Math.cos(c1), ae = we - anticipation * Math.sin(c1);
    return {
      centre: [an - side * radius * Math.sin(c1), ae + side * radius * Math.cos(c1)], radius, side,
      end: [wn + anticipation * Math.cos(c2), we + anticipation * Math.sin(c2)],
    };
  }

  // {leg, waypoint, dtkMap, xtk, turning, trackError, toGo, distance, nextTurn} (radians, metres).
  quantities(n, e, trackMap) {
    const r = this.route, leg = this.active;
    let [, cross, toGo] = legGeometry(r, leg, n, e);
    let dtk = r.courses[leg];
    if (this.arc) {
      const { centre: [cn, ce], radius, side } = this.arc;
      dtk = wrapPi(Math.atan2(e - ce, n - cn) + (side * Math.PI) / 2);
      cross = side * (radius - Math.hypot(n - cn, e - ce));
    }
    const [wn, we] = r.points[leg + 1];
    return {
      leg, waypoint: r.waypoints[leg].name, dtkMap: dtk, xtk: cross, turning: Boolean(this.arc),
      trackError: wrapPi(dtk - trackMap), toGo, distance: Math.hypot(wn - n, we - e), nextTurn: r.turn(leg),
    };
  }
}

// The leg a position most likely belongs to (after a seek): the nearest leg segment, later
// legs winning ties within 50 m.
export function nearestLeg(route, n, e) {
  let best = 0, bestD = Infinity;
  for (let leg = 0; leg < route.legs; leg++) {
    const [along, cross] = legGeometry(route, leg, n, e);
    const a = Math.max(0, Math.min(route.lengths[leg], along));
    const d = Math.hypot(along - a, cross);
    if (d <= bestD + 50) {
      best = leg;
      bestD = Math.min(d, bestD);
    }
  }
  return best;
}

// Follows a flight's frames: the navigator, re-synced when time goes back or jumps.
export class NavTracker {
  constructor(routeInfo, geodesy) {
    this.route = new Route(routeInfo);
    this.bank = (routeInfo.turn_bank_deg * Math.PI) / 180;
    this.turnSpeed = routeInfo.turn_speed_mps ?? null; // planned per flight (older logs: the ground speed)
    this.geodesy = geodesy;
    this.nav = null;
    this.lastT = null;
  }

  // The GPS quantities for a frame row (true bearings added: dtkTrue).
  update(row) {
    const [n, e] = this.geodesy.toMap(row.lat_rad, row.lon_rad);
    const conv = this.geodesy.convergence(row.lat_rad, row.lon_rad);
    const jumped = this.lastT === null || row.t_s < this.lastT || row.t_s - this.lastT > 2;
    if (!this.nav || jumped) this.nav = new Navigator(this.route, this.bank, row.t_s < 0.5 ? 0 : nearestLeg(this.route, n, e));
    this.lastT = row.t_s;
    this.nav.update(n, e, Math.hypot(row.v_north_mps, row.v_east_mps), this.turnSpeed);
    const q = this.nav.quantities(n, e, Math.atan2(row.v_east_mps, row.v_north_mps) - conv);
    return { ...q, dtkTrue: q.dtkMap + conv, done: this.nav.done, legs: this.route.legs };
  }
}
