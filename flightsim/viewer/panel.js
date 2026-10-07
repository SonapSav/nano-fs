// The instrument panel: six-pack, tachometer, control positions and the readouts, drawn
// from frames. Shared by the viewer page and the separate instruments window
// (panel.html), so both show exactly the same thing.
//
// The instruments window mirrors the viewer window over a BroadcastChannel (same browser,
// same computer; no server involved). Messages, viewer -> window: {type: "state", hello,
// row, run} (a new flight or preview, sent on request too), {type: "frame", row},
// {type: "viewer"} (once a second). Window -> viewer: {type: "sync"} (send the state),
// {type: "alive"} (once a second; the viewer hides its own panel meanwhile),
// {type: "closed"}, {type: "key", event: "down" | "up", code, key, shiftKey, repeat}
// (keys pressed while the instruments window has the focus, handled by the viewer as its
// own, so flying with the keyboard keeps working).

import { drawAll, indicatedKt, units } from "./gauges.js";

export const PANEL_CHANNEL = "flightsim-panel";
export const PANEL_TIMEOUT_MS = 3000; // the other window is gone after this long without a message

const deg360 = (rad) => ((rad * units.DEG) % 360 + 360) % 360;

const PANEL_HTML = `
  <canvas class="gauge" data-g="asi" aria-label="Airspeed"></canvas>
  <canvas class="gauge" data-g="ai" aria-label="Attitude"></canvas>
  <canvas class="gauge" data-g="alt" aria-label="Altimeter"></canvas>
  <canvas class="gauge" data-g="tc" aria-label="Turn coordinator"></canvas>
  <canvas class="gauge" data-g="hi" aria-label="Heading"></canvas>
  <canvas class="gauge" data-g="vsi" aria-label="Vertical speed"></canvas>
  <canvas class="gauge" data-g="tach" aria-label="Engine RPM"></canvas>
  <canvas class="gauge" data-g="controls" aria-label="Control positions"></canvas>
  <div class="readout">
    <dl>
      <dt>Altitude</dt><dd data-r="alt">–</dd>
      <dt data-l="talt">Altitude target</dt><dd data-r="talt" class="target">–</dd>
      <dt>Heading</dt><dd data-r="hdg">–</dd>
      <dt data-l="thdg">Heading target</dt><dd data-r="thdg" class="target">–</dd>
      <dt data-l="dist" hidden>To threshold</dt><dd data-r="dist" class="target" hidden>–</dd>
      <dt data-l="wind" hidden>Wind (20 ft)</dt><dd data-r="wind" hidden>–</dd>
      <dt title="Indicated airspeed (POH Figure 5-1 calibration)">Airspeed (IAS)</dt><dd data-r="kias">–</dd>
      <dt>Angle of attack</dt><dd data-r="aoa">–</dd>
      <dt>Load factor</dt><dd data-r="g">–</dd>
      <dt>Flaps</dt><dd data-r="flaps">–</dd>
      <dt>Pitch trim</dt><dd data-r="trim">–</dd>
      <dt>Brakes</dt><dd data-r="brake">–</dd>
    </dl>
    <div class="run"></div>
  </div>`;

export class InstrumentPanel {
  constructor(root) {
    root.innerHTML = PANEL_HTML;
    const q = (sel) => root.querySelector(sel);
    this.gauges = Object.fromEntries([...root.querySelectorAll("canvas[data-g]")].map((c) => [c.dataset.g, c]));
    this.readout = Object.fromEntries([...root.querySelectorAll("dd[data-r]")].map((d) => [d.dataset.r, d]));
    // The block below addresses rows as $("r-x") / $("l-x").
    this.$ = (id) => q(id.startsWith("r-") ? `[data-r="${id.slice(2)}"]` : `[data-l="${id.slice(2)}"]`);
    this.run = q(".run");
    this.session = null;
    this.circuitClimbed = false; // a circuit is on final only after climbing out
  }

  // A circuit has climbed out (from then on, final approach speed and glide path apply).
  get climbedOut() {
    return this.circuitClimbed;
  }

  // A new flight (its hello message, or null for none).
  setSession(hello) {
    this.session = hello;
    this.circuitClimbed = false;
    showApproachRows(this, hello?.approach ? "approach" : hello?.takeoff ? "takeoff" : null);
  }

  setRunText(text) {
    this.run.textContent = text;
  }

  draw(row) {
    drawAll(this.gauges, row, this.session?.targets);
    updateReadout(this, row);
  }
}

// Approach and takeoff: deviations from the runway task replace the cruise targets.
function showApproachRows(p, kind) {
  const $ = p.$, session = p.session;
  const on = Boolean(kind);
  $("l-talt").textContent = { approach: "Glide path", takeoff: "Climb speed" }[kind] ?? "Altitude target";
  $("l-thdg").textContent = on ? "Centreline" : "Heading target";
  $("l-dist").textContent = kind === "takeoff" ? "Runway left" : "To threshold";
  $("l-dist").hidden = $("r-dist").hidden = !on;
  const w = on ? (session?.approach ?? session?.takeoff)?.wind : null;
  $("l-wind").hidden = $("r-wind").hidden = !w;
  if (w) {
    const kt = (v) => Math.round(Math.abs(v) * 1.943844);
    const cross = kt(w.crosswind_mps) ? `, ${kt(w.crosswind_mps)} kt crosswind from the ${w.crosswind_mps > 0 ? "right" : "left"}` : "";
    const gust = w.gust_factor_mps && kt(w.gust_factor_mps) ? `G${kt(w.u20_mps + w.gust_factor_mps)}` : ""; // METAR style, e.g. 17G23
    $("r-wind").textContent = `${String(Math.round(w.from_deg) % 360).padStart(3, "0")}° ${kt(w.u20_mps)}${gust} kt${cross}`;
  }
}

function approachDeviations(row, a) {
  const R_EARTH = 6371000, h = (a.heading_deg * Math.PI) / 180;
  const dn = row.lat_rad * R_EARTH - a.threshold_north_m, de = row.lon_rad * R_EARTH - a.threshold_east_m;
  const along = dn * Math.cos(h) + de * Math.sin(h), cross = -dn * Math.sin(h) + de * Math.cos(h);
  const gp = row.alt_msl_m - (a.elevation_m + Math.max(0, a.aim_point_m - along) * Math.tan((a.glide_path_deg * Math.PI) / 180));
  return { along, cross, gp };
}

// Takeoff: POH Section 4 normal takeoff, nose wheel up at 55 KIAS, climb 70-80 KIAS (75 used).
export const ROTATE_KT = 55;
export const CLIMB_KT = 75;

function updateReadout(p, row) {
  const $ = p.$, session = p.session, readout = p.readout;
  const a = session?.approach ?? session?.takeoff;
  if (session?.takeoff) {
    const tk = session.takeoff;
    if (row) {
      const d = approachDeviations(row, { ...tk, aim_point_m: 0, glide_path_deg: 0 });
      const kt = indicatedKt(row), height = row.alt_msl_m - tk.elevation_m;
      const airborne = height > 3;
      readout.talt.textContent = !airborne ? `rotate at ${ROTATE_KT} kt`
        : Math.abs(kt - CLIMB_KT) < 3 ? "on speed" : `${Math.round(Math.abs(kt - CLIMB_KT))} kt ${kt > CLIMB_KT ? "fast" : "slow"}`;
      readout.thdg.textContent = Math.abs(d.cross) < 2 ? "on centreline" : `${Math.abs(d.cross).toFixed(0)} m ${d.cross > 0 ? "right" : "left"}`;
      $("r-dist").textContent = airborne ? "airborne" : `${Math.max(0, Math.round(tk.length_m - d.along))} m`;
    } else {
      readout.talt.textContent = readout.thdg.textContent = $("r-dist").textContent = "–";
    }
  } else if (a) {
    if (row) {
      const d = approachDeviations(row, a);
      const ft = Math.round(d.gp * units.M_TO_FT);
      // A circuit: the glide path only means something on final (near the centreline, heading in).
      const towardRunway = Math.cos(row.psi_rad - (a.heading_deg * Math.PI) / 180) > 0.8;
      if (row.alt_msl_m - a.elevation_m > 200) p.circuitClimbed = true;
      // (before the threshold also counts: a replay may jump straight to final)
      const onFinal = a.task !== "circuit" || ((p.circuitClimbed || d.along < 0) && Math.abs(d.cross) < 300 && towardRunway);
      readout.talt.textContent = !onFinal ? "in the pattern" : Math.abs(ft) < 10 ? "on path" : `${Math.abs(ft)} ft ${ft > 0 ? "high" : "low"}`;
      readout.thdg.textContent = Math.abs(d.cross) < 2 ? "on centreline" : `${Math.abs(d.cross).toFixed(0)} m ${d.cross > 0 ? "right" : "left"}`;
      $("r-dist").textContent = !onFinal ? "–" : d.along < 0 ? `${(-d.along / 1852).toFixed(2)} nm` : "over the runway";
    } else {
      readout.talt.textContent = readout.thdg.textContent = $("r-dist").textContent = "–";
    }
  }
  const t = a ? null : session?.targets;
  readout.alt.textContent = row ? `${Math.round(row.alt_msl_m * units.M_TO_FT).toLocaleString("en-US")} ft` : "–";
  if (!a) readout.talt.textContent = t ? `${Math.round(t.alt_msl_m * units.M_TO_FT).toLocaleString("en-US")} ft` : "–";
  readout.hdg.textContent = row ? `${String(Math.round(deg360(row.psi_rad)) % 360).padStart(3, "0")}°` : "–";
  if (!a) readout.thdg.textContent = t ? `${String(Math.round(deg360(t.heading_rad)) % 360).padStart(3, "0")}°` : "–";
  readout.kias.textContent = row ? `${indicatedKt(row).toFixed(0)} kt` : "–";
  readout.aoa.textContent = row ? `${(row.alpha_rad * units.DEG).toFixed(1)}°` : "–";
  readout.g.textContent = row ? `${(-row.az_mps2 / units.G).toFixed(2)} g` : "–";
  if (row) {
    const flapDeg = row.flap_pos_rad * units.DEG;
    // POH 1981 C172P Figure 2-1: 110 KIAS with 10 deg flaps, 85 KIAS beyond.
    const vfe = flapDeg <= 0.5 ? Infinity : flapDeg <= 10.5 ? 110 : 85;
    const over = indicatedKt(row) > vfe; // the POH limits are KIAS
    readout.flaps.textContent = `${Math.round(flapDeg)}°${over ? ` over ${vfe} kt limit` : ""}`;
    readout.flaps.classList.toggle("warn", over);
    const trim = row.cmd_pitch_trim_norm;
    readout.trim.textContent = trim == null ? "–" : `${Math.round(Math.abs(trim) * 100)}% ${trim >= 0 ? "nose down" : "nose up"}`;
    const brake = row.cmd_brake_norm;
    readout.brake.textContent = brake == null ? "–" : brake < 0.01 ? "off" : `${Math.round(brake * 100)}%`;
  } else {
    readout.flaps.textContent = readout.trim.textContent = readout.brake.textContent = "–";
  }
}

