// Six-pack instruments drawn on 2D canvases. Inputs are SI frame rows (log schema v1);
// conversion to cockpit units happens here. Markings follow the C172P POH (Figure 2-2,
// Section 2 power plant markings).

const MPS_TO_KT = 3600 / 1852;
const M_TO_FT = 1 / 0.3048;
const DEG = 180 / Math.PI;
const G = 9.80665;

const C = {
  face: "#000000",
  marking: "#ecede8",
  dim: "#8c9196",
  bug: "#d23cc8",
  green: "#3fa34d",
  yellow: "#e2b93b",
  red: "#d9412b",
  sky: "#3d7dc4",
  earth: "#8a5a2b",
  pointer: "#f2a33a",
};

// POH Figure 2-2, KIAS. The model has no position error, so CAS is shown as IAS.
const ASI = { white: [33, 85], green: [44, 127], yellow: [127, 158], red: 158, min: 30, max: 170 };
// POH Section 2: sea-level green arc and red line.
const TACH = { green: [2100, 2450], red: 2700, max: 3500 };

function setup(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const size = canvas.clientWidth;
  if (canvas.width !== Math.round(size * dpr)) {
    canvas.width = canvas.height = Math.round(size * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(canvas.width / 200, 0, 0, canvas.height / 200, 0, 0); // draw in a 200 x 200 box
  ctx.clearRect(0, 0, 200, 200);
  return ctx;
}

function face(ctx) {
  ctx.beginPath();
  ctx.arc(100, 100, 98, 0, 2 * Math.PI);
  ctx.fillStyle = "#23272b";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(100, 100, 92, 0, 2 * Math.PI);
  ctx.fillStyle = C.face;
  ctx.fill();
}

function text(ctx, s, x, y, size = 16, color = C.marking, weight = 500) {
  ctx.fillStyle = color;
  ctx.font = `${weight} ${size}px "Barlow Condensed", "Roboto Condensed", "Arial Narrow", sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(s, x, y);
}

// Angle on a dial: value v in [lo, hi] mapped onto [a0, a1] radians, 0 = up, clockwise.
function dialAngle(v, lo, hi, a0, a1) {
  const f = Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
  return a0 + f * (a1 - a0);
}

function polar(r, a) {
  return [100 + r * Math.sin(a), 100 - r * Math.cos(a)];
}

function arcBand(ctx, r, w, a0, a1, color) {
  ctx.beginPath();
  ctx.arc(100, 100, r, a0 - Math.PI / 2, a1 - Math.PI / 2);
  ctx.strokeStyle = color;
  ctx.lineWidth = w;
  ctx.lineCap = "butt";
  ctx.stroke();
}

function tick(ctx, a, r0, r1, width = 2, color = C.marking) {
  const [x0, y0] = polar(r0, a);
  const [x1, y1] = polar(r1, a);
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.stroke();
}

function needle(ctx, a, length, width = 4, color = C.marking, tail = 14) {
  ctx.save();
  ctx.translate(100, 100);
  ctx.rotate(a);
  ctx.beginPath();
  ctx.moveTo(-width / 2, tail);
  ctx.lineTo(width / 2, tail);
  ctx.lineTo(width / 2, -length + 8);
  ctx.lineTo(0, -length);
  ctx.lineTo(-width / 2, -length + 8);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
  ctx.restore();
  ctx.beginPath();
  ctx.arc(100, 100, 6, 0, 2 * Math.PI);
  ctx.fillStyle = "#30353a";
  ctx.fill();
}

function bug(ctx, a, r = 90) {
  ctx.save();
  ctx.translate(100, 100);
  ctx.rotate(a);
  ctx.beginPath();
  ctx.moveTo(-7, -r);
  ctx.lineTo(7, -r);
  ctx.lineTo(7, -r + 9);
  ctx.lineTo(0, -r + 4);
  ctx.lineTo(-7, -r + 9);
  ctx.closePath();
  ctx.fillStyle = C.bug;
  ctx.fill();
  ctx.restore();
}

export function drawAirspeed(canvas, row) {
  const ctx = setup(canvas);
  face(ctx);
  const a0 = (-150 * Math.PI) / 180, a1 = (150 * Math.PI) / 180;
  const A = (v) => dialAngle(v, ASI.min, ASI.max, a0, a1);
  arcBand(ctx, 84, 6, A(ASI.white[0]), A(ASI.white[1]), C.marking);
  arcBand(ctx, 78, 6, A(ASI.green[0]), A(ASI.green[1]), C.green);
  arcBand(ctx, 78, 6, A(ASI.yellow[0]), A(ASI.yellow[1]), C.yellow);
  tick(ctx, A(ASI.red), 72, 88, 4, C.red);
  for (let v = 40; v <= 160; v += 10) {
    tick(ctx, A(v), v % 20 === 0 ? 62 : 67, 74, v % 20 === 0 ? 2.5 : 1.5);
    if (v % 20 === 0) text(ctx, String(v), ...polar(50, A(v)), 15);
  }
  text(ctx, "Airspeed", 100, 132, 13, C.dim);
  text(ctx, "knots", 100, 146, 13, C.dim);
  if (!row) return;
  const kt = row.cas_mps * MPS_TO_KT;
  needle(ctx, A(kt), 80);
}

export function drawAttitude(canvas, row) {
  const ctx = setup(canvas);
  face(ctx);
  const phi = row ? row.phi_rad : 0;
  const theta = row ? row.theta_rad : 0;
  const pxPerDeg = 3.2;
  ctx.save();
  ctx.beginPath();
  ctx.arc(100, 100, 90, 0, 2 * Math.PI);
  ctx.clip();
  ctx.translate(100, 100);
  ctx.rotate(-phi);
  const y = theta * DEG * pxPerDeg;
  ctx.fillStyle = C.sky;
  ctx.fillRect(-200, -400 + y, 400, 400);
  ctx.fillStyle = C.earth;
  ctx.fillRect(-200, y, 400, 400);
  ctx.strokeStyle = C.marking;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(-200, y);
  ctx.lineTo(200, y);
  ctx.stroke();
  for (let p = -30; p <= 30; p += 5) {
    if (p === 0) continue;
    const w = p % 10 === 0 ? 24 : 12;
    const yy = y - p * pxPerDeg;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(-w, yy);
    ctx.lineTo(w, yy);
    ctx.stroke();
    if (p % 10 === 0) {
      text(ctx, String(Math.abs(p)), -w - 12, yy, 12);
      text(ctx, String(Math.abs(p)), w + 12, yy, 12);
    }
  }
  // Bank scale rotates with the horizon; the pointer at the top stays fixed.
  for (const b of [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60]) {
    const a = (b * Math.PI) / 180;
    const len = b === 0 || Math.abs(b) === 30 || Math.abs(b) === 60 ? 12 : 7;
    ctx.save();
    ctx.rotate(a);
    ctx.beginPath();
    ctx.moveTo(0, -88);
    ctx.lineTo(0, -88 + len);
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }
  ctx.restore();
  // Fixed aircraft symbol and bank pointer
  ctx.fillStyle = C.pointer;
  ctx.fillRect(48, 98, 34, 4);
  ctx.fillRect(118, 98, 34, 4);
  ctx.beginPath();
  ctx.arc(100, 100, 4, 0, 2 * Math.PI);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(100, 24);
  ctx.lineTo(94, 34);
  ctx.lineTo(106, 34);
  ctx.closePath();
  ctx.fill();
}

export function drawAltimeter(canvas, row, targets) {
  const ctx = setup(canvas);
  face(ctx);
  for (let i = 0; i < 50; i++) {
    const a = (i / 50) * 2 * Math.PI;
    tick(ctx, a, i % 5 === 0 ? 72 : 78, 86, i % 5 === 0 ? 2.5 : 1.2);
    if (i % 5 === 0) text(ctx, String(i / 5), ...polar(60, a), 18);
  }
  text(ctx, "Altitude, feet", 100, 72, 13, C.dim);
  if (targets) {
    const tft = targets.alt_msl_m * M_TO_FT;
    bug(ctx, ((tft % 1000) / 1000) * 2 * Math.PI);
  }
  if (!row) return;
  const ft = row.alt_msl_m * M_TO_FT;
  needle(ctx, ((ft % 10000) / 10000) * 2 * Math.PI, 46, 6, C.marking, 8); // thousands
  needle(ctx, ((ft % 1000) / 1000) * 2 * Math.PI, 80, 3.5, C.marking, 14); // hundreds
  // Digital window over the needles, like the drum readout on modern altimeters.
  ctx.fillStyle = "#1d2125";
  ctx.fillRect(74, 118, 52, 18);
  text(ctx, Math.round(ft).toLocaleString("en-US"), 100, 127.5, 15, C.marking, 600);
}

export function drawTurnCoordinator(canvas, row) {
  const ctx = setup(canvas);
  face(ctx);
  // Standard-rate (3 deg/s) marks at +/-15 degrees of wing tilt.
  for (const s of [-1, 1]) {
    tick(ctx, Math.PI / 2 * s + (15 * Math.PI / 180) * s, 70, 84, 3);
    tick(ctx, Math.PI / 2 * s, 70, 84, 3);
  }
  text(ctx, "L", 34, 124, 15);
  text(ctx, "R", 166, 124, 15);
  text(ctx, "2 min", 100, 66, 13, C.dim);
  // Slip ball tube
  ctx.beginPath();
  ctx.ellipse(100, 140, 40, 9, 0, 0, 2 * Math.PI);
  ctx.fillStyle = "#1d2125";
  ctx.fill();
  ctx.strokeStyle = C.dim;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(91, 130); ctx.lineTo(91, 150); ctx.moveTo(109, 130); ctx.lineTo(109, 150);
  ctx.stroke();
  // Heading rate (deg/s): psi_dot = (q sin(phi) + r cos(phi)) / cos(theta).
  const turnRate = row
    ? ((row.q_radps * Math.sin(row.phi_rad) + row.r_radps * Math.cos(row.phi_rad)) / Math.cos(row.theta_rad)) * DEG
    : 0;
  const tilt = Math.max(-30, Math.min(30, (turnRate / 3) * 15)) * Math.PI / 180;
  ctx.save();
  ctx.translate(100, 100);
  ctx.rotate(tilt);
  ctx.fillStyle = C.marking;
  ctx.fillRect(-62, -2, 124, 4);
  ctx.beginPath();
  ctx.arc(0, 0, 8, 0, 2 * Math.PI);
  ctx.fill();
  ctx.fillRect(-2, -16, 4, 12);
  ctx.restore();
  // Ball moves opposite to the lateral specific force (skid pushes it outward).
  const slip = row ? Math.max(-1, Math.min(1, row.ay_mps2 / (0.25 * G))) : 0;
  ctx.beginPath();
  ctx.arc(100 - slip * 32, 140, 7, 0, 2 * Math.PI);
  ctx.fillStyle = "#0a0a0a";
  ctx.strokeStyle = C.marking;
  ctx.lineWidth = 1.5;
  ctx.fill();
  ctx.stroke();
}

export function drawHeading(canvas, row, targets) {
  const ctx = setup(canvas);
  face(ctx);
  const psi = row ? row.psi_rad : 0;
  ctx.save();
  ctx.translate(100, 100);
  ctx.rotate(-psi);
  for (let d = 0; d < 360; d += 5) {
    const a = (d * Math.PI) / 180;
    ctx.save();
    ctx.rotate(a);
    ctx.beginPath();
    ctx.moveTo(0, -86);
    ctx.lineTo(0, d % 10 === 0 ? -74 : -80);
    ctx.strokeStyle = C.marking;
    ctx.lineWidth = d % 10 === 0 ? 2 : 1.2;
    ctx.stroke();
    if (d % 30 === 0) {
      const label = { 0: "N", 90: "E", 180: "S", 270: "W" }[d] ?? String(d / 10);
      text(ctx, label, 0, -60, d % 90 === 0 ? 18 : 15, C.marking, d % 90 === 0 ? 600 : 500);
    }
    ctx.restore();
  }
  if (targets) {
    ctx.save();
    ctx.rotate(targets.heading_rad);
    ctx.beginPath();
    ctx.moveTo(-7, -90); ctx.lineTo(7, -90); ctx.lineTo(7, -81); ctx.lineTo(0, -86); ctx.lineTo(-7, -81);
    ctx.closePath();
    ctx.fillStyle = C.bug;
    ctx.fill();
    ctx.restore();
  }
  ctx.restore();
  // Lubber line and aircraft symbol
  ctx.fillStyle = C.pointer;
  ctx.beginPath();
  ctx.moveTo(100, 12); ctx.lineTo(95, 24); ctx.lineTo(105, 24);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = C.pointer;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(100, 82); ctx.lineTo(100, 122);
  ctx.moveTo(84, 98); ctx.lineTo(116, 98);
  ctx.moveTo(92, 118); ctx.lineTo(108, 118);
  ctx.stroke();
}

export function drawVerticalSpeed(canvas, row) {
  const ctx = setup(canvas);
  face(ctx);
  // +/-2000 ft/min over +/-170 degrees, zero at 9 o'clock as on the C172 VSI.
  const A = (fpm) => -Math.PI / 2 + (Math.max(-2000, Math.min(2000, fpm)) / 2000) * (170 * Math.PI / 180);
  for (let v = -2000; v <= 2000; v += 100) {
    const major = v % 500 === 0;
    tick(ctx, A(v), major ? 70 : 77, 86, major ? 2.5 : 1.2);
    if (v % 1000 === 0 || Math.abs(v) === 500) {
      text(ctx, String(Math.abs(v) / 100), ...polar(58, A(v)), 15);
    }
  }
  text(ctx, "Up", 118, 74, 13, C.dim);
  text(ctx, "Down", 118, 126, 13, C.dim);
  text(ctx, "100 ft/min", 136, 100, 12, C.dim);
  if (!row) return;
  needle(ctx, A(-row.v_down_mps * M_TO_FT * 60), 80);
}

export function drawTachometer(canvas, row) {
  const ctx = setup(canvas);
  face(ctx);
  const a0 = (-135 * Math.PI) / 180, a1 = (135 * Math.PI) / 180;
  const A = (rpm) => dialAngle(rpm, 0, TACH.max, a0, a1);
  arcBand(ctx, 80, 7, A(TACH.green[0]), A(TACH.green[1]), C.green);
  tick(ctx, A(TACH.red), 72, 88, 4, C.red);
  for (let v = 0; v <= TACH.max; v += 100) {
    const major = v % 500 === 0;
    tick(ctx, A(v), major ? 66 : 72, 76, major ? 2.5 : 1);
    if (major) text(ctx, String(v / 100), ...polar(54, A(v)), 15);
  }
  text(ctx, "RPM", 100, 132, 13, C.dim);
  text(ctx, "×100", 100, 146, 13, C.dim);
  if (!row) return;
  needle(ctx, A(row.engine_rpm), 78);
}

export function drawControls(canvas, row) {
  const ctx = setup(canvas);
  ctx.fillStyle = C.face;
  ctx.beginPath();
  ctx.roundRect(4, 4, 192, 192, 10);
  ctx.fill();
  const cmd = (k) => (row && row[k] != null ? row[k] : null);
  // Yoke box: aileron right = right, elevator positive (push) = down.
  const box = { x: 16, y: 16, s: 120 };
  ctx.strokeStyle = C.dim;
  ctx.lineWidth = 1;
  ctx.strokeRect(box.x, box.y, box.s, box.s);
  ctx.beginPath();
  ctx.moveTo(box.x + box.s / 2, box.y); ctx.lineTo(box.x + box.s / 2, box.y + box.s);
  ctx.moveTo(box.x, box.y + box.s / 2); ctx.lineTo(box.x + box.s, box.y + box.s / 2);
  ctx.stroke();
  text(ctx, "Yoke", box.x + box.s / 2, box.y + box.s + 12, 13, C.dim);
  const ail = cmd("cmd_aileron_norm"), ele = cmd("cmd_elevator_norm");
  if (ail != null && ele != null) {
    ctx.beginPath();
    ctx.arc(box.x + box.s / 2 * (1 + ail), box.y + box.s / 2 * (1 + ele), 6, 0, 2 * Math.PI);
    ctx.fillStyle = C.pointer;
    ctx.fill();
  }
  // Rudder bar
  const rx = 16, ry = 166, rw = 120;
  ctx.strokeRect(rx, ry, rw, 10);
  const rud = cmd("cmd_rudder_norm");
  if (rud != null) {
    ctx.fillStyle = C.pointer;
    ctx.fillRect(rx + rw / 2 + (rw / 2) * rud - 3, ry - 2, 6, 14);
  }
  text(ctx, "Rudder", rx + rw / 2, ry + 22, 13, C.dim);
  // Throttle
  const tx = 156, ty = 16, th = 150;
  ctx.strokeRect(tx, ty, 14, th);
  const thr = cmd("cmd_throttle_norm");
  if (thr != null) {
    ctx.fillStyle = C.marking;
    ctx.fillRect(tx + 1, ty + th * (1 - thr), 12, th * thr);
  }
  text(ctx, "Throttle", tx + 7, ty + th + 14, 13, C.dim);
  // Pitch trim as a tick on the throttle's left edge (positive = nose down).
  const trim = cmd("cmd_pitch_trim_norm");
  if (trim != null) {
    ctx.fillStyle = C.bug;
    ctx.fillRect(tx - 9, ty + th / 2 + (th / 2) * trim - 1.5, 7, 3);
  }
}

export function drawAll(els, row, targets) {
  drawAirspeed(els.asi, row);
  drawAttitude(els.ai, row);
  drawAltimeter(els.alt, row, targets);
  drawTurnCoordinator(els.tc, row);
  drawHeading(els.hi, row, targets);
  drawVerticalSpeed(els.vsi, row);
  drawTachometer(els.tach, row);
  drawControls(els.controls, row);
}

export const units = { MPS_TO_KT, M_TO_FT, DEG, G };
