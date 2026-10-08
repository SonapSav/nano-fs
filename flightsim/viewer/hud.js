// Head-up display for the cockpit view (an option: a real C172 has none). Visual only.
//
// Fixed to the aircraft, like a real HUD: the symbols live on a combiner in front of the
// pilot, HUD_FIELD degrees around the boresight; looking around moves it out of view.
// Conformal: the horizon, pitch ladder and flight path marker are drawn through the same
// camera as the 3D view, so they sit exactly on the outside world. Speed, altitude and
// heading tapes, the bank scale and the small readouts are fixed on the combiner.
//
// Drawn on a 2D canvas over the 3D view, from the frame row (state) and the camera. A thin
// dark outline under every line and letter keeps it readable against a bright sky; a
// blurred shadow ("shadow") looks the same but costs ~10x more in Firefox (measured
// 2026-10-07: 22 ms against 2.6 ms per frame, headless Firefox 153).
//
// Runway tasks add (phase 2): the runway outline and a dashed extended centreline (1 nm),
// conformal, so the runway stays visible through haze; on approach (heading toward the
// runway) the aim point and a dashed glide path reference line at the glide path angle
// below the horizon (put the flight path marker where the line crosses the aim point:
// on the glide path); and speed bugs on the speed tape (R rotate, C climb, A approach).

import * as THREE from "three";
import { indicatedKt, units } from "./gauges.js";

export const HUD_GREEN = "#5cff6e";
// Combiner field of view around the boresight (body axes), degrees.
export const HUD_FIELD = { halfWidthDeg: 25, upDeg: 18, downDeg: 20 };
const D2R = Math.PI / 180;
const FAR_M = 10000; // directions are drawn as points this far from the eye
const FPV_MIN_SPEED_MPS = 5; // below this the flight path is meaningless (parked, taxiing)

const nedToWorld = (n, e, d) => new THREE.Vector3(e, -d, -n);

// Unit vector in body axes (x forward, y right, z down) for an azimuth (right +) and an
// elevation (up +) in degrees.
export function bodyVector(azDeg, elDeg) {
  const a = azDeg * D2R, e = elDeg * D2R;
  return new THREE.Vector3(Math.cos(e) * Math.cos(a), Math.cos(e) * Math.sin(a), -Math.sin(e));
}

// World direction (viewer frame: x east, y up, z south) of a heading and an elevation.
export function worldDirection(headingRad, elevRad) {
  return nedToWorld(Math.cos(elevRad) * Math.cos(headingRad), Math.cos(elevRad) * Math.sin(headingRad), -Math.sin(elevRad));
}

// Screen position (pixels) of a world direction seen from the camera, or null if behind.
export function project(dir, camera, w, h) {
  const p = camera.position.clone().addScaledVector(dir, FAR_M).project(camera);
  if (p.z > 1 || p.z < -1) return null;
  return { x: ((p.x + 1) / 2) * w, y: ((1 - p.y) / 2) * h };
}

// Screen position of a world point (not a direction), or null if behind the camera.
export function projectPoint(point, camera, w, h) {
  const p = point.clone().project(camera);
  if (p.z > 1 || p.z < -1) return null;
  return { x: ((p.x + 1) / 2) * w, y: ((1 - p.y) / 2) * h };
}

// World point of a runway position: `along` metres past the threshold, `cross` metres
// right of the centreline, on the runway surface. `rw`: the hello's runway geometry.
export function runwayPoint(rw, along, cross) {
  const h = rw.heading_deg * D2R;
  const north = rw.threshold_north_m + along * Math.cos(h) - cross * Math.sin(h);
  const east = rw.threshold_east_m + along * Math.sin(h) + cross * Math.cos(h);
  return nedToWorld(north, east, -rw.elevation_m);
}

// World direction of the flight path (ground velocity), or null when too slow.
// `convergence`: true north to map north (geo.js); the velocity is true.
export function flightPathDirection(row, convergence = 0) {
  const c = Math.cos(convergence), s = Math.sin(convergence);
  const v = nedToWorld(row.v_north_mps * c + row.v_east_mps * s, row.v_east_mps * c - row.v_north_mps * s, row.v_down_mps);
  return v.length() < FPV_MIN_SPEED_MPS ? null : v.normalize();
}

const pad3 = (deg) => String(Math.round(((deg % 360) + 360) % 360) % 360).padStart(3, "0");

// Draw the HUD for a frame. `aircraftMatrix`: the scene's aircraft matrix (body axes as
// its basis). Runway tasks: `runway` (the hello's approach or takeoff geometry),
// `approach` (true to show the aim point and glide path reference), `speedBugs`
// ([{kt, label}]); `convergence`: true north to map north (geo.js); `contrast`: "outline" (default), "shadow" or "none". Returns false when
// the combiner is out of view (looking away).
export function drawHud(ctx, w, h, { camera, aircraftMatrix, row, targets, runway = null, approach = false, speedBugs = [], contrast = "outline", convergence = 0 }) {
  const rot = new THREE.Matrix4().extractRotation(aircraftMatrix);
  const body = (az, el) => project(bodyVector(az, el).applyMatrix4(rot), camera, w, h);
  const F = HUD_FIELD;
  const corners = [body(-F.halfWidthDeg, F.upDeg), body(F.halfWidthDeg, F.upDeg), body(F.halfWidthDeg, -F.downDeg), body(-F.halfWidthDeg, -F.downDeg)];
  const centre = body(0, 0);
  if (!centre || corners.some((c) => !c)) return false;
  const up5 = body(0, 5), down5 = body(0, -5);
  const pxPerDeg = Math.hypot(up5.x - down5.x, up5.y - down5.y) / 10;
  const fs = Math.max(12, pxPerDeg * 1.3); // font size
  const lw = Math.max(1.5, pxPerDeg * 0.12);

  ctx.save();
  ctx.beginPath();
  corners.forEach((c, i) => (i ? ctx.lineTo(c.x, c.y) : ctx.moveTo(c.x, c.y)));
  ctx.closePath();
  ctx.clip();
  ctx.strokeStyle = ctx.fillStyle = HUD_GREEN;
  ctx.lineWidth = lw;
  ctx.lineCap = "round";
  if (contrast === "shadow") {
    ctx.shadowColor = "rgba(0, 0, 0, 0.6)";
    ctx.shadowBlur = 3;
  }
  const outline = contrast === "outline";
  const DARK = "rgba(0, 0, 0, 0.45)";
  ctx.font = `600 ${fs}px "Barlow Condensed", "Arial Narrow", sans-serif`;
  ctx.textBaseline = "middle";
  const line = (pts, dash = []) => {
    ctx.setLineDash(dash);
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    if (outline) {
      ctx.strokeStyle = DARK;
      ctx.lineWidth = lw + 2;
      ctx.stroke();
      ctx.strokeStyle = HUD_GREEN;
      ctx.lineWidth = lw;
    }
    ctx.stroke();
    ctx.setLineDash([]);
  };
  // A polyline with gaps where points are out of view (null).
  const pieces = (pts, dash = []) => {
    let run = [];
    for (const p of [...pts, null]) {
      if (p) run.push(p);
      else {
        if (run.length > 1) line(run, dash);
        run = [];
      }
    }
  };
  const text = (s, x, y, align = "center") => {
    ctx.textAlign = align;
    if (outline) {
      ctx.strokeStyle = DARK;
      ctx.lineWidth = 3;
      ctx.strokeText(s, x, y);
      ctx.strokeStyle = HUD_GREEN;
      ctx.lineWidth = lw;
    }
    ctx.fillText(s, x, y);
  };

  // --- Conformal: horizon and pitch ladder, centred on the aircraft's heading. ---------
  // Kept between the tapes and below the heading tape, so they never overlap.
  ctx.save();
  ctx.beginPath();
  ctx.rect(centre.x - 14 * pxPerDeg, centre.y - 11.5 * pxPerDeg, 28 * pxPerDeg, 40 * pxPerDeg);
  ctx.clip();
  const psi = row.psi_rad - convergence; // on the map (the world the ladder is drawn in)
  const right = nedToWorld(-Math.sin(psi), Math.cos(psi), 0);
  const along = (centreDir, offDeg) => project(centreDir.clone().addScaledVector(right, Math.tan(offDeg * D2R)).normalize(), camera, w, h);
  let horizonAngle = 0;
  for (let e = -90; e <= 90; e += 5) {
    const c = worldDirection(psi, e * D2R);
    if (e === 0) {
      const a = along(c, -60), b = along(c, 60);
      if (a && b) {
        line([a, b]);
        horizonAngle = Math.atan2(b.y - a.y, b.x - a.x);
      }
      continue;
    }
    const hook = e > 0 ? -1 : 1; // end hooks point toward the horizon
    const hooked = worldDirection(psi, (e + hook) * D2R);
    const dash = e < 0 ? [pxPerDeg * 0.8, pxPerDeg * 0.5] : [];
    for (const side of [-1, 1]) {
      const inner = along(c, side * 2.5), outer = along(c, side * 7), tip = along(hooked, side * 7);
      if (!inner || !outer || !tip) continue;
      line([inner, outer, tip], dash);
      const label = along(c, side * 9);
      if (label) text(String(Math.abs(e)), label.x, label.y);
    }
  }

  // Glide path reference: a long-dashed line at the glide path angle below the horizon.
  if (runway && approach) {
    const c = worldDirection(psi, -runway.glide_path_deg * D2R);
    const a = along(c, -9.5), b = along(c, 9.5), lbl = along(c, 10.3);
    if (a && b) line([a, b], [pxPerDeg * 1.6, pxPerDeg * 0.7]);
    if (lbl) text("GP", lbl.x, lbl.y, "left");
  }
  ctx.restore();

  // --- Conformal: runway outline, extended centreline and aim point. --------------------
  if (runway) {
    const pt = (al, cr) => projectPoint(runwayPoint(runway, al, cr), camera, w, h);
    // Sampled along the edges, drawn in the visible pieces (on the runway, the near end
    // is beside or behind the pilot).
    const hw = runway.width_m / 2, L = runway.length_m, n = 24, outline = [];
    for (let k = 0; k <= n; k++) outline.push(pt((L * k) / n, -hw));
    for (let k = 0; k <= 4; k++) outline.push(pt(L, -hw + (2 * hw * k) / 4));
    for (let k = n; k >= 0; k--) outline.push(pt((L * k) / n, hw));
    for (let k = 0; k <= 4; k++) outline.push(pt(0, hw - (2 * hw * k) / 4));
    pieces(outline);
    const ext = [];
    for (let al = -1852; al <= 0; al += 1852 / 12) ext.push(pt(al, 0));
    pieces(ext, [pxPerDeg * 0.6, pxPerDeg * 0.6]);
    const aim = approach && pt(runway.aim_point_m, 0);
    if (aim) {
      const r = fs * 0.45;
      line([{ x: aim.x, y: aim.y - r }, { x: aim.x + r, y: aim.y }, { x: aim.x, y: aim.y + r }, { x: aim.x - r, y: aim.y }, { x: aim.x, y: aim.y - r }]);
    }
  }

  // --- Conformal: flight path marker (where the aircraft is going). --------------------
  const fpDir = flightPathDirection(row, convergence);
  const fp = fpDir && project(fpDir, camera, w, h);
  if (fp) {
    const r = fs * 0.45, ca = Math.cos(horizonAngle), sa = Math.sin(horizonAngle);
    const at = (dx, dy) => ({ x: fp.x + dx * ca - dy * sa, y: fp.y + dx * sa + dy * ca }); // marker frame: level with the horizon
    ctx.beginPath();
    ctx.arc(fp.x, fp.y, r, 0, 2 * Math.PI);
    ctx.stroke();
    line([at(-r, 0), at(-r * 2.6, 0)]);
    line([at(r, 0), at(r * 2.6, 0)]);
    line([at(0, -r), at(0, -r * 1.9)]);
  }

  // --- Fixed on the combiner. -----------------------------------------------------------
  // Boresight (waterline): where the nose points.
  const r0 = fs * 0.5;
  line([
    { x: centre.x - 2 * r0, y: centre.y }, { x: centre.x - r0, y: centre.y }, { x: centre.x - r0 / 2, y: centre.y + r0 / 2 },
    { x: centre.x, y: centre.y }, { x: centre.x + r0 / 2, y: centre.y + r0 / 2 }, { x: centre.x + r0, y: centre.y },
    { x: centre.x + 2 * r0, y: centre.y },
  ]);

  const halfTape = 10 * pxPerDeg; // tape half height
  const box = (cx, cy, label, align) => {
    ctx.font = `700 ${fs * 1.15}px "Barlow Condensed", "Arial Narrow", sans-serif`;
    const tw = ctx.measureText(label).width + fs * 0.6, th = fs * 1.5;
    const x0 = align === "right" ? cx - tw : align === "left" ? cx : cx - tw / 2;
    ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
    ctx.fillRect(x0, cy - th / 2, tw, th);
    ctx.fillStyle = HUD_GREEN;
    ctx.strokeRect(x0, cy - th / 2, tw, th);
    text(label, x0 + tw / 2, cy);
    ctx.font = `600 ${fs}px "Barlow Condensed", "Arial Narrow", sans-serif`;
  };
  // Vertical tape: values `v` around `value`, `perPx` units per pixel, ticks every `tick`,
  // labels every `labelEvery`, on the side `dir` (-1 left of the line, +1 right).
  const tape = (x, value, unitsPerHalf, tick, labelEvery, dir, fmt) => {
    const pxPerUnit = halfTape / unitsPerHalf;
    line([{ x, y: centre.y - halfTape }, { x, y: centre.y + halfTape }]);
    const first = Math.ceil((value - unitsPerHalf) / tick) * tick;
    for (let v = first; v <= value + unitsPerHalf; v += tick) {
      const y = centre.y - (v - value) * pxPerUnit;
      const major = Math.round(v / labelEvery) * labelEvery === Math.round(v);
      line([{ x, y }, { x: x + dir * (major ? fs * 0.6 : fs * 0.35), y }]);
      if (major && Math.abs(y - centre.y) > fs * 1.1) text(fmt(v), x + dir * fs * 0.9, y, dir < 0 ? "right" : "left");
    }
    return pxPerUnit;
  };
  const caret = (x, y, dir) => line([{ x, y: y - fs * 0.35 }, { x: x - dir * fs * 0.45, y }, { x, y: y + fs * 0.35 }]);

  // Airspeed (indicated, knots), left.
  const kias = indicatedKt(row);
  const sx = body(-17, 0)?.x ?? centre.x - 17 * pxPerDeg;
  const pxPerKt = tape(sx, kias, 25, 5, 10, -1, (v) => (v >= 0 ? String(v) : ""));
  box(sx - fs * 0.4, centre.y, `${Math.max(0, Math.round(kias))}`, "right");
  // Ground speed (GPS) above the speed tape, as the target altitude sits above the altitude tape.
  text(`GS ${Math.round(Math.hypot(row.v_north_mps, row.v_east_mps) * units.MPS_TO_KT)}`, sx - fs * 0.4, centre.y - halfTape - fs * 0.9, "right");
  for (const bug of speedBugs) {
    const y = centre.y - (bug.kt - kias) * pxPerKt;
    if (!(Math.abs(y - centre.y) <= halfTape)) continue; // off the tape
    caret(sx + fs * 0.6, y, -1);
    text(bug.label, sx + fs * 0.75, y, "left");
  }

  // Altitude (feet) and vertical speed, right.
  const ft = row.alt_msl_m * units.M_TO_FT;
  const ax = body(17, 0)?.x ?? centre.x + 17 * pxPerDeg;
  const pxPerFt = tape(ax, ft, 250, 50, 100, 1, (v) => v.toLocaleString("en-US"));
  box(ax + fs * 0.4, centre.y, Math.round(ft).toLocaleString("en-US"), "left");
  const fpm = -row.v_down_mps * units.M_TO_FT * 60;
  text(`${fpm >= 0 ? "+" : "−"}${Math.abs(Math.round(fpm / 10) * 10)}`, ax + fs * 0.4, centre.y + halfTape + fs * 0.9, "left");
  if (targets?.alt_msl_m != null) {
    const tft = targets.alt_msl_m * units.M_TO_FT;
    const y = Math.max(centre.y - halfTape, Math.min(centre.y + halfTape, centre.y - (tft - ft) * pxPerFt));
    caret(ax - fs * 0.15, y, 1);
    text(Math.round(tft).toLocaleString("en-US"), ax + fs * 0.4, centre.y - halfTape - fs * 0.9, "left");
  }

  // Heading tape, top.
  const hy = body(0, 13.5)?.y ?? centre.y - 13.5 * pxPerDeg;
  const hdg = (((row.psi_rad / D2R) % 360) + 360) % 360; // true heading on the tape
  const pxPerHdg = pxPerDeg * 0.7, halfW = 11 * pxPerDeg;
  line([{ x: centre.x - halfW, y: hy }, { x: centre.x + halfW, y: hy }]);
  for (let d = Math.ceil((hdg - 15) / 5) * 5; d <= hdg + 15; d += 5) {
    const x = centre.x + (d - hdg) * pxPerHdg;
    if (Math.abs(x - centre.x) > halfW) continue;
    const major = ((d % 10) + 10) % 10 === 0;
    line([{ x, y: hy }, { x, y: hy - (major ? fs * 0.5 : fs * 0.3) }]);
    if (major && Math.abs(x - centre.x) > fs * 1.6) text(pad3(d).slice(0, 2), x, hy - fs * 1.0);
  }
  box(centre.x, hy - fs * 1.0, pad3(hdg), "center");
  if (targets?.heading_rad != null) {
    const d = ((((targets.heading_rad / D2R - hdg) % 360) + 540) % 360) - 180;
    const x = centre.x + Math.max(-halfW, Math.min(halfW, d * pxPerHdg));
    line([{ x: x - fs * 0.35, y: hy + fs * 0.45 }, { x, y: hy + fs * 0.05 }, { x: x + fs * 0.35, y: hy + fs * 0.45 }]);
  }

  // Bank scale (bottom arc) with the sky pointer, and the slip indicator under it.
  const rb = 12.5 * pxPerDeg;
  ctx.beginPath();
  ctx.arc(centre.x, centre.y, rb, (90 - 60) * D2R, (90 + 60) * D2R);
  ctx.stroke();
  for (const b of [0, 10, 20, 30, 45, 60]) {
    for (const s of b ? [-1, 1] : [1]) {
      const t = (90 + s * b) * D2R, len = b % 30 === 0 ? fs * 0.6 : fs * 0.35;
      line([{ x: centre.x + rb * Math.cos(t), y: centre.y + rb * Math.sin(t) }, { x: centre.x + (rb + len) * Math.cos(t), y: centre.y + (rb + len) * Math.sin(t) }]);
    }
  }
  const tp = Math.PI / 2 + horizonAngle; // world "down" as seen on the combiner
  const pr = rb - fs * 0.15, ux = Math.cos(tp), uy = Math.sin(tp), vx = -uy, vy = ux;
  const tipP = { x: centre.x + pr * ux, y: centre.y + pr * uy };
  ctx.beginPath();
  ctx.moveTo(tipP.x, tipP.y);
  ctx.lineTo(tipP.x - fs * 0.6 * ux + fs * 0.35 * vx, tipP.y - fs * 0.6 * uy + fs * 0.35 * vy);
  ctx.lineTo(tipP.x - fs * 0.6 * ux - fs * 0.35 * vx, tipP.y - fs * 0.6 * uy - fs * 0.35 * vy);
  ctx.closePath();
  ctx.fill();
  // Slip: the ball moves against the lateral specific force (accelerometer y).
  const slip = Math.max(-fs * 1.5, Math.min(fs * 1.5, (row.ay_mps2 / units.G) * fs * 12));
  const bx = tipP.x - fs * 0.85 * ux + slip * vx, by = tipP.y - fs * 0.85 * uy + slip * vy;
  ctx.fillRect(bx - fs * 0.35, by - fs * 0.15, fs * 0.7, fs * 0.3);

  // Small readouts, lower left.
  const lx = body(-23, 0)?.x ?? centre.x - 23 * pxPerDeg, ly = centre.y + halfTape + fs * 1.2;
  text(`G ${(-row.az_mps2 / units.G).toFixed(1)}`, lx, ly, "left");
  text(`α ${(row.alpha_rad / D2R).toFixed(1)}`, lx, ly + fs * 1.2, "left");

  ctx.restore();
  return true;
}
