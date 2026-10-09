// The belly camera: a stabilized gimbal under the fuselage (pure, no three.js: tested
// with Node). Visual only; it never reaches the physics.
//
// Pointing: pan from the aircraft's nose (clockwise, 0 to 360 deg, continuous), tilt from
// the horizon (0 deg) to straight down (-90 deg), horizontal field of view 60 deg (wide)
// to 5 deg (telephoto). Stabilized: pan follows the aircraft's heading, but the picture
// keeps its tilt against the horizon and never rolls with the aircraft.
//
// Directions are in the map's NED frame (north, east, down on the map; geo.js), heights
// above the ellipsoid. The scene converts them to its world frame.

export const DEG = Math.PI / 180;
export const TILT_MIN_RAD = -90 * DEG;
export const TILT_MAX_RAD = 0;
export const HFOV_MIN_RAD = 5 * DEG;
export const HFOV_MAX_RAD = 60 * DEG;
export const ASPECT = 16 / 9;
export const HOME = { pan: 0, tilt: TILT_MIN_RAD, hfov: HFOV_MAX_RAD }; // straight down, wide
export const RECENTRE_S = 0.6;
export const MAX_RANGE_M = 30000; // ground points further than this are not found

// Mount: a ball turret under the cabin floor, just ahead of the CG (aircraft.js
// structural x 40 in, y 0, z -0.5 in: lens 4 in below the belly skin), in body axes
// (metres; x forward, y right, z down, from the CG). About 38 cm above the ground on the
// wheels.
export const MOUNT_STRUCT_IN = { x: 40, y: 0, z: -0.5 };
export const MOUNT_BODY_M = [(40.9 - MOUNT_STRUCT_IN.x) * 0.0254, MOUNT_STRUCT_IN.y * 0.0254, (36.6 - MOUNT_STRUCT_IN.z) * 0.0254];

const SLEW_RATE_RAD_S = 60 * DEG; // held keys or hat, at the widest view (slower when zoomed in)
const ZOOM_RATE = 2; // field of view factor per second while held

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const wrap2pi = (a) => ((a % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
const wrapPi = (a) => wrap2pi(a + Math.PI) - Math.PI;

export const vfov = (hfov, aspect = ASPECT) => 2 * Math.atan(Math.tan(hfov / 2) / aspect);
export const zoomFactor = (hfov) => Math.tan(HFOV_MAX_RAD / 2) / Math.tan(hfov / 2);

// The gimbal's pointing and how it moves: drags (radians), held rates (-1..1 per axis),
// eased re-centring, and following a recorded pointing (replays).
export class Gimbal {
  constructor() {
    this.pan = HOME.pan;
    this.tilt = HOME.tilt;
    this.hfov = HOME.hfov;
    this.rates = { pan: 0, tilt: 0, zoom: 0 };
    this.recentre = null;
    this.following = false; // replays: the recorded pointing drives it until the user moves it
  }

  get state() {
    return { pan: this.pan, tilt: this.tilt, hfov: this.hfov };
  }

  set({ pan = this.pan, tilt = this.tilt, hfov = this.hfov }) {
    this.pan = wrap2pi(pan);
    this.tilt = clamp(tilt, TILT_MIN_RAD, TILT_MAX_RAD);
    this.hfov = clamp(hfov, HFOV_MIN_RAD, HFOV_MAX_RAD);
  }

  // The user moves it: re-centring and following stop.
  _take() {
    this.recentre = null;
    this.following = false;
  }

  slew(dPan, dTilt) {
    this._take();
    this.set({ pan: this.pan + dPan, tilt: this.tilt + dTilt });
  }

  // Dragging the picture by (dx, dy) pixels of a view `widthPx` wide: the point under the
  // pointer turns with it (drag right to look right, up to look up), so the speed suits
  // any zoom.
  drag(dx, dy, widthPx) {
    const k = this.hfov / Math.max(1, widthPx);
    this.slew(dx * k, -dy * k);
  }

  zoomBy(factor) {
    this._take();
    this.set({ hfov: 2 * Math.atan(Math.tan(this.hfov / 2) / factor) });
  }

  setRates(rates) {
    this.rates = { pan: clamp(rates.pan ?? 0, -1, 1), tilt: clamp(rates.tilt ?? 0, -1, 1), zoom: clamp(rates.zoom ?? 0, -1, 1) };
    if (this.rates.pan || this.rates.tilt || this.rates.zoom) this._take();
  }

  startRecentre(nowS) {
    this.following = false;
    this.recentre = { t0: nowS, from: this.state };
  }

  // Advance held rates and re-centring by dt seconds (nowS: a clock in seconds).
  step(dt, nowS) {
    const { pan, tilt, zoom } = this.rates;
    if (pan || tilt) {
      const r = SLEW_RATE_RAD_S * (this.hfov / HFOV_MAX_RAD) * dt;
      this.set({ pan: this.pan + pan * r, tilt: this.tilt + tilt * r });
    }
    if (zoom) this.set({ hfov: 2 * Math.atan(Math.tan(this.hfov / 2) / ZOOM_RATE ** (zoom * dt)) });
    if (this.recentre) {
      const k = clamp((nowS - this.recentre.t0) / RECENTRE_S, 0, 1), e = k * k * (3 - 2 * k), f = this.recentre.from;
      const dPan = wrapPi(HOME.pan - f.pan);
      this.set({
        pan: f.pan + dPan * e,
        tilt: f.tilt + (HOME.tilt - f.tilt) * e,
        hfov: Math.exp(Math.log(f.hfov) + (Math.log(HOME.hfov) - Math.log(f.hfov)) * e),
      });
      if (k >= 1) this.recentre = null;
    }
  }
}

// Camera axes in the map's NED frame for a heading on the map: forward (the line of
// sight), up (the picture's top) and right. Stabilized: no roll.
export function cameraAxes(headingMapRad, pan, tilt) {
  const az = headingMapRad + pan, ce = Math.cos(tilt), se = Math.sin(tilt), ca = Math.cos(az), sa = Math.sin(az);
  const forward = [ce * ca, ce * sa, -se];
  const up = [-se * ca, -se * sa, -ce];
  const right = [-sa, ca, 0]; // forward x up
  return { forward, up, right };
}

// A body-axes vector (x forward, y right, z down) in NED for Euler angles (heading on the map).
export function bodyToNed(phi, theta, psiMap, [x, y, z]) {
  const [cf, sf, ct, st, cp, sp] = [phi, theta, psiMap].flatMap((a) => [Math.cos(a), Math.sin(a)]);
  return [
    ct * cp * x + (sf * st * cp - cf * sp) * y + (cf * st * cp + sf * sp) * z,
    ct * sp * x + (sf * st * sp + cf * cp) * y + (cf * st * sp - sf * cp) * z,
    -st * x + sf * ct * y + cf * ct * z,
  ];
}

// Where a ray from `origin` ({n, e, h}: map metres and height) along `dir` (NED, unit)
// meets the ground (groundAt(n, e): terrain height, water included), or null within
// maxRange. Marches in steps sized by the height above the ground, then bisects.
export function groundIntersect(origin, dir, groundAt, maxRange = MAX_RANGE_M) {
  const at = (t) => [origin.n + dir[0] * t, origin.e + dir[1] * t, origin.h - dir[2] * t];
  const clearance = (t) => {
    const [n, e, h] = at(t);
    return h - groundAt(n, e);
  };
  if (clearance(0) <= 0) return { n: origin.n, e: origin.e, h: groundAt(origin.n, origin.e), range: 0 };
  let t0 = 0, t = 0;
  while (t < maxRange) {
    const c = clearance(t);
    if (c <= 0) break;
    t0 = t;
    // Slopes are gentle (a few tens of percent at most): a step of half the clearance
    // along the vertical, or 2 m, cannot jump through a hill.
    t += Math.max(2, Math.min(500, (0.5 * c) / Math.max(0.05, dir[2] + 0.3)));
  }
  if (t >= maxRange && clearance(maxRange) > 0) return null;
  t = Math.min(t, maxRange);
  let lo = t0, hi = t;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    if (clearance(mid) > 0) lo = mid;
    else hi = mid;
  }
  const [n, e, h] = at(hi);
  return { n, e, h, range: hi };
}

// The ground the picture covers: the centre and the four corners (null where a corner
// ray misses the ground, e.g. above the horizon).
export function footprint(origin, axes, hfov, groundAt, aspect = ASPECT) {
  const tx = Math.tan(hfov / 2), ty = Math.tan(vfov(hfov, aspect) / 2);
  const ray = (sx, sy) => {
    const d = [0, 1, 2].map((i) => axes.forward[i] + sx * tx * axes.right[i] + sy * ty * axes.up[i]);
    const len = Math.hypot(...d);
    return groundIntersect(origin, d.map((v) => v / len), groundAt);
  };
  return { centre: groundIntersect(origin, axes.forward, groundAt), corners: [ray(-1, 1), ray(1, 1), ray(1, -1), ray(-1, -1)] };
}

// Recorded pointing (a demonstration's flightsim.camera metadata), for replays.
export class CameraTrack {
  constructor(meta) {
    const cols = meta?.columns ?? [];
    const idx = (name) => cols.indexOf(name);
    const [it, ion, ipan, itilt, ifov] = ["t_s", "on", "pan_rad", "tilt_rad", "hfov_rad"].map(idx);
    this.samples = it < 0 ? [] : (meta.samples ?? []).map((s) => ({ t: s[it], on: Boolean(s[ion]), pan: s[ipan], tilt: s[itilt], hfov: s[ifov] }));
  }

  get empty() {
    return this.samples.length === 0;
  }

  // The pointing at time t: interpolated between samples up to 1 s apart (the viewer
  // reports changes at up to 5 Hz), else the last sample's.
  at(t) {
    const s = this.samples;
    if (!s.length || t < s[0].t) return null;
    let lo = 0, hi = s.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (s[mid].t <= t) lo = mid;
      else hi = mid - 1;
    }
    const a = s[lo], b = s[lo + 1];
    if (!b || b.t - a.t > 1 || !a.on || !b.on) return { ...a };
    const k = (t - a.t) / (b.t - a.t);
    return {
      t, on: true, pan: wrap2pi(a.pan + wrapPi(b.pan - a.pan) * k), tilt: a.tilt + (b.tilt - a.tilt) * k,
      hfov: Math.exp(Math.log(a.hfov) + (Math.log(b.hfov) - Math.log(a.hfov)) * k),
    };
  }

  // Whether the camera was in view at any time.
  get used() {
    return this.samples.some((s) => s.on);
  }
}

// Reporting the pointing during a manual flight: when it was last reported, whether
// this state differs enough to report now (at most 5 Hz; 0.25 deg, 2% zoom, or on/off).
export class CameraReporter {
  constructor() {
    this.last = null;
    this.lastAt = -Infinity;
  }

  reset() {
    this.last = null;
    this.lastAt = -Infinity;
  }

  // A message to send for state {on, pan, tilt, hfov} at clock nowMs, or null.
  next(state, nowMs) {
    const l = this.last;
    const changed = !l || l.on !== state.on ||
      (state.on && (Math.abs(wrapPi(state.pan - l.pan)) > 0.25 * DEG || Math.abs(state.tilt - l.tilt) > 0.25 * DEG || Math.abs(Math.log(state.hfov / l.hfov)) > 0.02));
    if (!changed || (l && l.on === state.on && nowMs - this.lastAt < 200)) return null;
    this.last = { ...state };
    this.lastAt = nowMs;
    return { type: "camera", on: state.on, pan_rad: round4(state.pan), tilt_rad: round4(state.tilt), hfov_rad: round4(state.hfov) };
  }
}
const round4 = (v) => Math.round(v * 1e4) / 1e4;

// --- Overlay ---------------------------------------------------------------------------

const pad3 = (v) => String(Math.round(v) % 360).padStart(3, "0");
const FONT = '"Barlow Condensed", "Arial Narrow", sans-serif';

// What the overlay shows: the pointing, the ground point under the crosshair and its
// position (formatLat/formatLon from gps.js), and the mode.
// info: {pan, tilt, hfov, headingTrueRad, ground: {latDeg, lonDeg, elevM, slantM, groundM, bearingTrueDeg} | null,
//        mode: "LIVE" | "PLAYBACK", recording: bool, formatLat, formatLon}
export function drawCameraOverlay(ctx, w, h, info, compact = false) {
  const g = info.ground;
  const s = compact ? 0.62 : 1;
  const font = (px, weight = 600) => `${weight} ${Math.round(px * s)}px ${FONT}`;
  ctx.save();
  ctx.lineJoin = "round";
  // Text and lines in white with a dark outline: legible over sky, fields and snow alike.
  const stroke = (draw) => {
    ctx.strokeStyle = "rgba(0, 0, 0, 0.65)";
    ctx.lineWidth = 3.5 * s;
    draw();
    ctx.stroke();
    ctx.strokeStyle = "#f4f4f0";
    ctx.lineWidth = 1.5 * s;
    draw();
    ctx.stroke();
  };
  const text = (str, x, y, px, align = "left", weight = 600) => {
    ctx.font = font(px, weight);
    ctx.textAlign = align;
    ctx.textBaseline = "middle";
    ctx.lineWidth = 3.5 * s;
    ctx.strokeStyle = "rgba(0, 0, 0, 0.7)";
    ctx.strokeText(str, x, y);
    ctx.fillStyle = "#f4f4f0";
    ctx.fillText(str, x, y);
  };
  // Crosshair: four arms with a gap, and corner brackets.
  const cx = w / 2, cy = h / 2, arm = Math.min(w, h) * 0.06, gap = arm * 0.35;
  stroke(() => {
    ctx.beginPath();
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      ctx.moveTo(cx + dx * gap, cy + dy * gap);
      ctx.lineTo(cx + dx * arm, cy + dy * arm);
    }
  });
  const m = Math.min(w, h) * 0.06, b = m * 0.6;
  stroke(() => {
    ctx.beginPath();
    for (const [x, y, sx, sy] of [[m, m, 1, 1], [w - m, m, -1, 1], [w - m, h - m, -1, -1], [m, h - m, 1, -1]]) {
      ctx.moveTo(x, y + sy * b);
      ctx.lineTo(x, y);
      ctx.lineTo(x + sx * b, y);
    }
  });
  const panDeg = (info.pan / DEG + 360) % 360, tiltDeg = info.tilt / DEG, zoom = zoomFactor(info.hfov);
  const zoomText = `×${zoom < 9.95 ? zoom.toFixed(1) : zoom.toFixed(0)}`;
  if (compact) {
    text(`${pad3(panDeg)}°  ${Math.round(tiltDeg)}°  ${zoomText}`, m + 4, m + 14 * s + 6, 18);
    if (info.mode === "PLAYBACK") text("PLAYBACK", w - m - 4, m + 14 * s + 6, 18, "right");
    ctx.restore();
    return;
  }
  const top = m + 22;
  text(`PAN ${pad3(panDeg)}°`, m + 8, top, 22);
  text(`TILT ${tiltDeg > -0.5 ? "0" : "−" + Math.round(-tiltDeg)}°`, m + 8, top + 26, 22);
  text(`${zoomText}  FOV ${(info.hfov / DEG).toFixed(info.hfov < 10 * DEG ? 1 : 0)}°`, m + 8, top + 52, 22);
  text(`STAB  ${info.mode}${info.recording ? "  ● REC" : ""}`, w - m - 8, top, 20, "right");
  // Pan dial: the aircraft from above, nose up, and the line of sight.
  const dr = Math.min(w, h) * 0.07, dx = w - m - 8 - dr, dy = top + 30 + dr;
  stroke(() => {
    ctx.beginPath();
    ctx.arc(dx, dy, dr, 0, 2 * Math.PI);
  });
  stroke(() => {
    ctx.beginPath(); // aircraft symbol
    ctx.moveTo(dx, dy - dr * 0.45);
    ctx.lineTo(dx, dy + dr * 0.4);
    ctx.moveTo(dx - dr * 0.4, dy - dr * 0.08);
    ctx.lineTo(dx + dr * 0.4, dy - dr * 0.08);
    ctx.moveTo(dx - dr * 0.16, dy + dr * 0.36);
    ctx.lineTo(dx + dr * 0.16, dy + dr * 0.36);
  });
  // Line of sight: its length shows the tilt (full at the horizon, a dot straight down).
  const reach = dr * Math.cos(info.tilt);
  ctx.save();
  ctx.strokeStyle = "#ffcf3a";
  ctx.fillStyle = "#ffcf3a";
  ctx.lineWidth = 2.5 * s;
  ctx.beginPath();
  ctx.moveTo(dx, dy);
  ctx.lineTo(dx + Math.sin(info.pan) * reach, dy - Math.cos(info.pan) * reach);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(dx + Math.sin(info.pan) * reach, dy - Math.cos(info.pan) * reach, 3 * s, 0, 2 * Math.PI);
  ctx.fill();
  ctx.restore();
  // Tilt scale on the left edge: 0 at the top, -90 at the bottom.
  const sx = m + 8, s0 = h * 0.38, s1 = h * 0.72;
  stroke(() => {
    ctx.beginPath();
    ctx.moveTo(sx, s0);
    ctx.lineTo(sx, s1);
    for (const k of [0, 1 / 3, 2 / 3, 1]) {
      ctx.moveTo(sx, s0 + (s1 - s0) * k);
      ctx.lineTo(sx + 8, s0 + (s1 - s0) * k);
    }
  });
  const ty = s0 + (s1 - s0) * (info.tilt / TILT_MIN_RAD);
  ctx.save();
  ctx.fillStyle = "#ffcf3a";
  ctx.beginPath();
  ctx.moveTo(sx + 10, ty);
  ctx.lineTo(sx + 20, ty - 6);
  ctx.lineTo(sx + 20, ty + 6);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
  // The ground point under the crosshair.
  const bottom = h - m - 18;
  if (g) {
    text(`${info.formatLat(g.latDeg)}   ${info.formatLon(g.lonDeg)}`, w / 2, bottom - 28, 24, "center");
    const nm = (v) => (v < 1852 * 10 ? (v / 1852).toFixed(2) : (v / 1852).toFixed(1));
    text(`ELEV ${Math.round(g.elevM * 3.28084)} ft   SLANT ${nm(g.slantM)} nm   GND ${nm(g.groundM)} nm   BRG ${pad3(g.bearingTrueDeg)}°`, w / 2, bottom, 20, "center", 500);
  } else {
    text("NO GROUND IN SIGHT", w / 2, bottom, 22, "center");
  }
  ctx.restore();
}
