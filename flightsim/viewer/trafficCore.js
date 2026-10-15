// Moving traffic's timing and positions (no three.js; traffic.js draws it): a region's
// traffic.json route (points every step_m on the map, north/east) flown at a constant
// speed, started so that it passes its meeting point (meet_s along the route) when the
// flown aircraft passes closest to that point. Visual only: it reads the shown frames and
// never reaches the physics.
//
// Timing: while the flown aircraft is within trigger_m of the meeting point and closing on
// it, its time to the closest approach is (r . v) / |v|^2 (r: to the point, v: its ground
// velocity on the map). The model starts at meet_s - speed * that time, once that lies on
// the route, and then flies on at its own speed from the frames' clock (t_s: it stops when
// the flight is paused and plays back with a replay). It is gone past the route's end; a
// new approach (the aircraft beyond 1.5 trigger_m, or the clock going back) sets it again.

const G = 9.80665;
const MIN_GROUND_SPEED_MPS = 10; // slower than this, no time to the meeting point is taken

// Position along a route at s metres: {north, east, heading (map, rad), bank (rad, right
// wing down positive, for a coordinated turn along the route's curvature at speed)}.
export function routeAt(route, s, speed = route.speed_mps) {
  const p = route.points, step = route.step_m;
  const f = Math.min(Math.max(s / step, 0), p.length - 1.000001);
  const i = Math.floor(f), t = f - i;
  const north = p[i][0] + (p[i + 1][0] - p[i][0]) * t;
  const east = p[i][1] + (p[i + 1][1] - p[i][1]) * t;
  const head = (k) => {
    const a = p[Math.max(0, Math.min(p.length - 2, k))], b = p[Math.max(1, Math.min(p.length - 1, k + 1))];
    return Math.atan2(b[1] - a[1], b[0] - a[0]);
  };
  const h0 = head(i), h1 = head(i + 1);
  const dh = Math.atan2(Math.sin(h1 - h0), Math.cos(h1 - h0));
  const heading = h0 + dh * t;
  // curvature over 5 steps either side (the route is smoothed; single steps are noisy)
  const k = 5, ha = head(i - k), hb = head(i + k);
  const curv = Math.atan2(Math.sin(hb - ha), Math.cos(hb - ha)) / (2 * k * step);
  return { north, east, heading, bank: Math.atan((speed * speed * curv) / G) };
}

export function routeLength(route) {
  return (route.points.length - 1) * route.step_m;
}

// One route's schedule. update(frame) with {t_s, north, east, v_north, v_east} (the flown
// aircraft on the map) returns null (not flying) or {s, north, east, heading, bank}.
export class Schedule {
  constructor(route) {
    this.route = route;
    this.length = routeLength(route);
    this.reset();
  }

  reset() {
    this.t0 = null; // start time (frame clock) and place along the route
    this.s0 = 0;
    this.done = false;
    this.lastT = null;
  }

  update(f) {
    const r = this.route;
    if (this.lastT !== null && (f.t_s < this.lastT || f.t_s - this.lastT > 5)) this.reset(); // seek, new flight
    this.lastT = f.t_s;
    const dn = r.meet[0] - f.north, de = r.meet[1] - f.east;
    const dist = Math.hypot(dn, de);
    if (this.done) {
      if (dist > 1.5 * r.trigger_m) this.reset();
      return null;
    }
    if (this.t0 === null) {
      const v2 = f.v_north * f.v_north + f.v_east * f.v_east;
      if (dist > r.trigger_m || v2 < MIN_GROUND_SPEED_MPS ** 2) return null;
      const eta = (dn * f.v_north + de * f.v_east) / v2;
      if (eta <= 0) return null;
      const s0 = r.meet_s - r.speed_mps * eta;
      if (s0 < 0) return null; // still too far: wait until the start is on the route
      this.t0 = f.t_s;
      this.s0 = s0;
    }
    const s = this.s0 + r.speed_mps * (f.t_s - this.t0);
    if (s > this.length) {
      this.done = true;
      return null;
    }
    return { s, ...routeAt(r, s) };
  }
}
