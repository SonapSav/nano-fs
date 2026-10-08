// GPS display: position, ground speed and track, and distance, bearing and time to a
// target (the task's runway threshold, else the airfield), from the frame row and the
// flight's geodesy (geo.js). Headings and bearings are true. Pure computations (tested
// with Node) and a canvas drawing in the panel's style.

const MPS_TO_KT = 3600 / 1852;
const M_PER_NM = 1852;
const DEG = 180 / Math.PI;
const MIN_TRACK_MPS = 1; // below this (parked, taxiing slowly) the track is not shown

const wrap360 = (deg) => ((deg % 360) + 360) % 360;
const pad = (n, w) => String(n).padStart(w, "0");

// "N 37°54.123'" / "E 023°42.000'": degrees and decimal minutes, as GPS units show them.
// Rounded to the shown 0.001' first (so 59.9996' carries into the degrees and a value that
// shows as zero is N/E).
function dm(deg, width, pos, neg) {
  const thousandths = Math.round(Math.abs(deg) * 60000);
  const d = Math.floor(thousandths / 60000), m = (thousandths % 60000) / 1000;
  return `${deg < 0 && thousandths > 0 ? neg : pos} ${pad(d, width)}°${m.toFixed(3).padStart(6, "0")}'`;
}
export const formatLat = (deg) => dm(deg, 2, "N", "S");
export const formatLon = (deg) => dm(deg, 3, "E", "W");

// Runway designator from its heading (true, until magnetic variation is modelled): 090 -> "09".
export const runwayNumber = (headingDeg) => pad(Math.round(wrap360(headingDeg) / 10) % 36 || 36, 2);

// The target for a flight's hello: the runway threshold of a runway task, else the airfield
// (the world's origin). {name, north_m, east_m}.
export function gpsTarget(session) {
  const rw = session?.approach ?? session?.takeoff;
  if (rw) return { name: `RWY ${runwayNumber(rw.heading_deg)}`, north_m: rw.threshold_north_m, east_m: rw.threshold_east_m };
  return { name: "AIRFIELD", north_m: 0, east_m: 0 };
}

// {latDeg, lonDeg, gsKt, trackDeg (null when slow), target, distNm, bearingDeg, eteS
// (null when not closing)} for a frame row, with `geodesy` a geo.js Geodesy.
export function gpsData(row, geodesy, target) {
  const [north, east] = geodesy.toMap(row.lat_rad, row.lon_rad);
  const gs = Math.hypot(row.v_north_mps, row.v_east_mps);
  const dn = target.north_m - north, de = target.east_m - east;
  const dist = Math.hypot(dn, de); // on the map: true distance to better than 1e-5 within 25 km
  // Map bearing to true bearing: true = map + convergence at the aircraft.
  const bearing = wrap360((Math.atan2(de, dn) + geodesy.convergence(row.lat_rad, row.lon_rad)) * DEG);
  // Closing speed: the ground velocity's component toward the target (true frame, as the bearing).
  const b = bearing / DEG, closing = row.v_north_mps * Math.cos(b) + row.v_east_mps * Math.sin(b);
  return {
    latDeg: row.lat_rad * DEG, lonDeg: row.lon_rad * DEG,
    gsKt: gs * MPS_TO_KT, trackDeg: gs < MIN_TRACK_MPS ? null : wrap360(Math.atan2(row.v_east_mps, row.v_north_mps) * DEG),
    target: target.name, distNm: dist / M_PER_NM, bearingDeg: bearing, eteS: closing > 1 && dist > 30 ? dist / closing : null,
  };
}

const fmtEte = (s) => (s === null ? "--:--" : s >= 3600 ? `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60), 2)}h` : `${pad(Math.floor(s / 60), 2)}:${pad(Math.floor(s % 60), 2)}`);

// Draw the GPS on its canvas (a tall box, the height of two gauges); `data` from gpsData, or null.
export function drawGps(canvas, data) {
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d"), H = (200 * h) / w; // draw 200 wide
  ctx.setTransform(canvas.width / 200, 0, 0, canvas.width / 200, 0, 0);
  ctx.clearRect(0, 0, 200, H);
  ctx.fillStyle = "#23272b"; // bezel
  ctx.beginPath();
  ctx.roundRect(2, 2, 196, H - 4, 12);
  ctx.fill();
  ctx.fillStyle = "#0b1410"; // screen
  ctx.beginPath();
  ctx.roundRect(10, 10, 180, H - 20, 6);
  ctx.fill();
  const font = (size, weight = 500) => `${weight} ${size}px "Barlow Condensed", "Roboto Condensed", "Arial Narrow", sans-serif`;
  const put = (s, x, y, size, color, align = "left", weight = 500) => {
    ctx.font = font(size, weight);
    ctx.fillStyle = color;
    ctx.textAlign = align;
    ctx.textBaseline = "middle";
    ctx.fillText(s, x, y);
  };
  const GREEN = "#7fe08a", DIM = "#6f8f78", MAGENTA = "#e070d8";
  put("GPS", 100, 26, 17, DIM, "center", 600);
  if (!data) {
    put("NO POSITION", 100, H / 2, 18, DIM, "center");
    return;
  }
  // Rows spread over the screen's height.
  const rows = [
    ["POS", formatLat(data.latDeg), GREEN, 19],
    ["", formatLon(data.lonDeg), GREEN, 19],
    ["GS", `${Math.round(data.gsKt)} kt`, GREEN, 24],
    ["TRK", data.trackDeg === null ? "---°" : `${pad(Math.round(data.trackDeg) % 360, 3)}°`, GREEN, 24],
    ["TO", data.target, MAGENTA, 20],
    ["DIS", `${data.distNm < 10 ? data.distNm.toFixed(2) : data.distNm.toFixed(1)} nm`, MAGENTA, 24],
    ["BRG", `${pad(Math.round(data.bearingDeg) % 360, 3)}°`, MAGENTA, 24],
    ["ETE", fmtEte(data.eteS), MAGENTA, 24],
  ];
  const top = 50, step = (H - top - 22) / rows.length;
  rows.forEach(([label, value, color, size], i) => {
    const y = top + step * (i + 0.5);
    if (label) put(label, 20, y, 15, DIM);
    put(value, 182, y, size, color, "right", 600);
  });
}
