// Moving map: a top view of the flight on the world's map (geo.js), drawn on a canvas in
// the map window (map.html). North up or track up; zoom by range. Shows the terrain
// background (mapTiles.js: relief, lakes, forests, rivers, villages, roads), range rings, the
// airfield's runway with its numbers, the task's extended centreline and traffic pattern,
// the flown track, a one-minute predicted path (curved by the turn rate) and the aircraft.
// Display only. Pure geometry and bookkeeping are exported for tests (Node).

import { AIRFIELD } from "./terrainCore.js";
import { patternPath } from "./patternPath.js";
import { gpsData, gpsTarget, runwayNumber } from "./gps.js";

export const M_PER_NM = 1852;
export const RANGES_NM = [0.5, 1, 2, 3, 5, 10, 20]; // width of the shorter screen side
const TRACK_STEP_S = 1; // a track point at most every second of flight time
const TRACK_MAX = 7200; // two hours at one point a second
const PREDICT_S = 60;
const DEG = 180 / Math.PI;

const C = {
  ground: "#1d2a22", ring: "rgba(255, 255, 255, 0.35)", ringText: "rgba(255, 255, 255, 0.85)", runway: "#d9dcd6", centreline: "#c7c9c2",
  pattern: "#d23cc8", track: "#5fd3f3", predict: "#e070d8", aircraft: "#f2a33a", text: "#ecede8", dim: "#9aa3a9",
};

// The flown track: map points [north, east] at most every TRACK_STEP_S; cleared when time
// goes back (a seek or a new flight).
export class Track {
  constructor() {
    this.clear();
  }
  clear() {
    this.points = [];
    this.lastT = null;
  }
  add(t, north, east) {
    if (this.lastT !== null && t < this.lastT) this.clear();
    if (this.lastT !== null && t - this.lastT < TRACK_STEP_S) return;
    this.points.push([north, east]);
    this.lastT = t;
    if (this.points.length > TRACK_MAX) this.points.splice(0, this.points.length - TRACK_MAX);
  }
}

// The runway to draw: the task's (approach or takeoff geometry), else the airfield's own
// runway 09/27 (terrainCore AIRFIELD: along x = east, centred on the origin).
export function mapRunway(hello) {
  const rw = hello?.approach ?? hello?.takeoff;
  if (rw) return { thresholdN: rw.threshold_north_m, thresholdE: rw.threshold_east_m, headingDeg: rw.heading_deg, lengthM: rw.length_m, widthM: rw.width_m, task: true };
  return { thresholdN: -AIRFIELD.z, thresholdE: AIRFIELD.x - AIRFIELD.lengthM / 2, headingDeg: 90, lengthM: AIRFIELD.lengthM, widthM: AIRFIELD.widthM, task: false };
}

// Map position [north, east] of a runway-frame point (along, cross).
export function runwayToMap(rw, along, cross) {
  const h = rw.headingDeg / DEG;
  return [rw.thresholdN + along * Math.cos(h) - cross * Math.sin(h), rw.thresholdE + along * Math.sin(h) + cross * Math.cos(h)];
}

// Screen transform: the aircraft at the centre (a little below in track-up, to see ahead),
// `upDeg` the map bearing at the top of the screen, `mPerPx` metres per pixel.
export function makeView(w, h, north, east, upDeg, rangeNm) {
  const mPerPx = (rangeNm * M_PER_NM) / Math.min(w, h);
  const a = upDeg / DEG, c = Math.cos(a), s = Math.sin(a);
  const cx = w / 2, cy = h / 2;
  return {
    w, h, mPerPx, upDeg, cx, cy,
    toScreen(n, e) {
      const dn = n - north, de = e - east;
      // Rotate so that bearing upDeg points up: screen x = right, y = down.
      const right = de * c - dn * s, up = dn * c + de * s;
      return [cx + right / mPerPx, cy - up / mPerPx];
    },
  };
}

// Points [north, east] of the path flown over the next `seconds` at the current ground
// speed, turning at the current yaw rate, at most half a turn (map frame: velocity turned
// by the convergence).
export function predictedPath(row, convergence, seconds = PREDICT_S, n0 = 0, e0 = 0) {
  const gs = Math.hypot(row.v_north_mps, row.v_east_mps);
  if (gs < 1) return [];
  let track = Math.atan2(row.v_east_mps, row.v_north_mps) - convergence;
  const rate = Math.abs(row.r_radps ?? 0) > 0.002 ? row.r_radps : 0;
  const pts = [[n0, e0]];
  let n = n0, e = e0;
  const dt = 2;
  for (let t = dt; t <= seconds + 1e-9 && Math.abs(rate * (t - dt)) < Math.PI; t += dt) { // at most half a turn
    n += gs * Math.cos(track) * dt;
    e += gs * Math.sin(track) * dt;
    track += rate * dt;
    pts.push([n, e]);
  }
  return pts;
}

// "Nice" ring spacing for a range: about four rings across the shorter side.
export function ringStepNm(rangeNm) {
  const raw = rangeNm / 4;
  return [0.1, 0.25, 0.5, 1, 2, 5, 10].find((s) => s >= raw * 0.99) ?? 10;
}

// Draw the map. `state`: {hello, row, geodesy, track (Track), rangeNm, northUp, background,
// nav} (`background`: optional, a mapTiles.js MapBackground: terrain, villages and roads;
// `nav`: a route's GPS quantities for this frame, nav.js).
// `compact`: the corner map in the 3D view (smaller text, no key help, a short info line).
export function drawMap(ctx, w, h, { hello, row, geodesy, track, rangeNm, northUp, background = null, nav = null, compact = false }) {
  ctx.fillStyle = C.ground;
  ctx.fillRect(0, 0, w, h);
  const font = (size, weight = 500) => `${weight} ${size}px "Barlow Condensed", "Roboto Condensed", "Arial Narrow", sans-serif`;
  const text = (s, x, y, size, color, align = "center", weight = 500) => {
    ctx.font = font(compact ? Math.round(size * 0.78) : size, weight);
    ctx.fillStyle = color;
    ctx.textAlign = align;
    ctx.textBaseline = "middle";
    ctx.fillText(s, x, y);
  };
  if (!row) {
    text("Waiting for a flight…", w / 2, h / 2, 22, C.dim);
    return;
  }
  const [north, east] = geodesy.toMap(row.lat_rad, row.lon_rad);
  const conv = geodesy.convergence(row.lat_rad, row.lon_rad);
  const gs = Math.hypot(row.v_north_mps, row.v_east_mps);
  const trackMapDeg = gs > 2 ? Math.atan2(row.v_east_mps, row.v_north_mps) * DEG - conv * DEG : row.psi_rad * DEG - conv * DEG;
  const upDeg = northUp ? -conv * DEG : trackMapDeg; // north up: true north at the top
  const v = makeView(w, h, north, east, upDeg, rangeNm);
  background?.draw(ctx, { w, h, cx: v.cx, cy: v.cy, x: east, z: -north, angle: upDeg / DEG, mPerPx: v.mPerPx });
  const line = (pts, color, width, dash = []) => {
    if (pts.length < 2) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.beginPath();
    pts.forEach(([n, e], i) => {
      const [x, y] = v.toScreen(n, e);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  };

  // Range rings around the aircraft.
  const step = ringStepNm(rangeNm), maxR = Math.hypot(w, h) / 2;
  ctx.strokeStyle = C.ring;
  ctx.lineWidth = 1;
  for (let k = 1; (k * step * M_PER_NM) / v.mPerPx < maxR; k++) {
    const r = (k * step * M_PER_NM) / v.mPerPx;
    ctx.beginPath();
    ctx.arc(v.cx, v.cy, r, 0, 2 * Math.PI);
    ctx.stroke();
    text(`${+(k * step).toFixed(2)}`, v.cx + r * Math.SQRT1_2 + 4, v.cy - r * Math.SQRT1_2 - 6, 14, C.ringText, "left");
  }

  // Runway, extended centreline (runway tasks) and the traffic pattern (circuits).
  const rw = mapRunway(hello);
  if (rw.task) line([runwayToMap(rw, -3 * M_PER_NM, 0), runwayToMap(rw, 0, 0)], C.centreline, 1, [6, 6]);
  const pat = hello?.pattern && hello?.approach ? patternPath(hello.approach, hello.pattern).points : null;
  if (pat) line(pat.map(([al, cr]) => runwayToMap(rw, al, cr)), C.pattern, 2, [8, 5]);
  const hw = Math.max(rw.widthM / 2, 1.5 * v.mPerPx);
  const corners = [[0, -hw], [rw.lengthM, -hw], [rw.lengthM, hw], [0, hw]].map(([al, cr]) => v.toScreen(...runwayToMap(rw, al, cr)));
  ctx.fillStyle = C.runway;
  ctx.beginPath();
  corners.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
  ctx.fill();
  const labelOff = Math.max(14 * v.mPerPx, 60);
  for (const [al, hdg] of [[-labelOff, rw.headingDeg], [rw.lengthM + labelOff, rw.headingDeg + 180]]) {
    const [x, y] = v.toScreen(...runwayToMap(rw, al, 0));
    text(runwayNumber(hdg), x, y, 15, C.runway, "center", 600);
  }

  // Route (navigation task): legs flown dim, the active leg bright, waypoints with names.
  const route = hello?.route;
  if (route) {
    const pts = [[route.start.north_m, route.start.east_m], ...route.waypoints.map((wp) => [wp.north_m, wp.east_m])];
    const active = nav ? (nav.done ? route.waypoints.length : nav.leg) : 0;
    for (let i = 0; i + 1 < pts.length; i++) {
      line([pts[i], pts[i + 1]], i < active ? "rgba(224, 112, 216, 0.45)" : C.pattern, i === active ? 4 : 2.5);
    }
    route.waypoints.forEach((wp, i) => {
      const [x, y] = v.toScreen(wp.north_m, wp.east_m), next = i === active;
      ctx.fillStyle = next ? C.predict : C.pattern;
      ctx.strokeStyle = "#1d1d1d";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, y - 7); ctx.lineTo(x + 7, y); ctx.lineTo(x, y + 7); ctx.lineTo(x - 7, y);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      if (next) {
        ctx.strokeStyle = C.predict;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 13, 0, 2 * Math.PI);
        ctx.stroke();
      }
      text(wp.name, x + 12, y - 14, 15, C.text, "left", 600);
    });
  }

  // Flown track, predicted path, aircraft.
  line([...track.points, [north, east]], C.track, 2);
  line(predictedPath(row, conv, PREDICT_S, north, east), C.predict, 2, [4, 4]);
  const hdgScreen = (row.psi_rad - conv) * DEG - upDeg; // aircraft heading relative to screen up
  ctx.save();
  ctx.translate(v.cx, v.cy);
  ctx.rotate(hdgScreen / DEG);
  ctx.fillStyle = C.aircraft;
  ctx.strokeStyle = "#1d1d1d";
  ctx.lineWidth = 1;
  ctx.beginPath(); // a small aeroplane, nose up
  ctx.moveTo(0, -13); ctx.lineTo(2, -6); ctx.lineTo(12, -1); ctx.lineTo(12, 2); ctx.lineTo(2, 1);
  ctx.lineTo(1.5, 8); ctx.lineTo(5, 11); ctx.lineTo(5, 13); ctx.lineTo(0, 12); ctx.lineTo(-5, 13); ctx.lineTo(-5, 11);
  ctx.lineTo(-1.5, 8); ctx.lineTo(-2, 1); ctx.lineTo(-12, 2); ctx.lineTo(-12, -1); ctx.lineTo(-2, -6);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();

  // North arrow (true north), top right.
  const na = (-conv * DEG - upDeg) / DEG, nx = w - (compact ? 18 : 34), ny = compact ? 22 : 40;
  ctx.save();
  ctx.translate(nx, ny);
  ctx.rotate(na);
  ctx.fillStyle = C.text;
  ctx.beginPath();
  ctx.moveTo(0, -16); ctx.lineTo(7, 8); ctx.lineTo(0, 3); ctx.lineTo(-7, 8);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
  text("N", nx, ny + 24, 14, C.text, "center", 600);

  // Info: mode and range top left; GPS line at the bottom.
  text(compact ? `${rangeNm} nm` : `${northUp ? "NORTH UP" : "TRACK UP"}   ${rangeNm} nm`, compact ? 8 : 14, compact ? 14 : 22, 17, C.text, "left", 600);
  const target = nav && route ? { name: route.waypoints[nav.leg].name, north_m: route.waypoints[nav.leg].north_m, east_m: route.waypoints[nav.leg].east_m } : gpsTarget(hello);
  const g = gpsData(row, geodesy, target, nav);
  const pad3 = (d) => String(Math.round(d) % 360).padStart(3, "0");
  const xtk = nav ? `   DTK ${pad3(g.dtkDeg)}°   XTK ${(Math.abs(g.xtkM) / M_PER_NM).toFixed(2)} nm${Math.abs(g.xtkM) >= 9 ? (g.xtkM > 0 ? " R" : " L") : ""}` : "";
  if (compact) {
    text(`${g.target} ${g.distNm < 10 ? g.distNm.toFixed(1) : g.distNm.toFixed(0)} nm ${pad3(g.bearingDeg)}°${nav ? `  XTK ${(Math.abs(g.xtkM) / M_PER_NM).toFixed(2)}` : ""}`, 8, h - 12, 16, C.text, "left", 600);
    return;
  }
  text(`GS ${Math.round(g.gsKt)} kt   TRK ${g.trackDeg === null ? "---" : pad3(g.trackDeg)}°   ${g.target} ${g.distNm < 10 ? g.distNm.toFixed(2) : g.distNm.toFixed(1)} nm ${pad3(g.bearingDeg)}°${xtk}`, 14, h - 18, 17, C.text, "left", 600);
  text("+ / − zoom   N north up / track up", w - 14, h - 18, 14, C.dim, "right");
}
