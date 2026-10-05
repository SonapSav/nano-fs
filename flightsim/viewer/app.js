// Viewer: consumes the state stream (protocol 1) and draws it. Rendering never affects
// the physics. The only message that does is pilot input during a manual flight, sent
// as stick/pedal/throttle values that the server applies as policy actions.

import { FlightScene } from "./scene.js";
import { drawAll, units } from "./gauges.js";
import { HANDLED_KEYS, PilotInput } from "./input.js";
import { AXES, DEFAULTS, centred, saveSettings } from "./stick.js";
import { groupLogs } from "./flightlist.js";

const $ = (id) => document.getElementById(id);
const els = {
  source: $("source"), seed: $("seed"), seedLabel: $("seed-label"), record: $("record"), recordLabel: $("record-label"),
  play: $("play"), pause: $("pause"), stop: $("stop"), speed: $("speed"), fill: $("progress-fill"), clock: $("clock"),
  message: $("message"), run: $("run"), hint: $("hint"),
};
const gauges = { asi: $("asi"), ai: $("ai"), alt: $("alt"), tc: $("tc"), hi: $("hi"), vsi: $("vsi"), tach: $("tach"), controls: $("controls") };
const readout = { alt: $("r-alt"), talt: $("r-talt"), hdg: $("r-hdg"), thdg: $("r-thdg"), kias: $("r-kias"), aoa: $("r-aoa"), g: $("r-g"), flaps: $("r-flaps"), trim: $("r-trim") };

const LIVE = "live"; // PID autopilot
const LIVE_LQR = "live_lqr";
const isLive = (v) => v === LIVE || v === LIVE_LQR;
const MANUAL = "manual"; // calm air
const MANUAL_WIND = "manual_wind";
const isManual = (v) => v === MANUAL || v === MANUAL_WIND;
const INPUT_SEND_HZ = 30;
const VIEW_HINT = "Drag to look around, scroll to zoom, space to pause, C for cockpit view";
const FLY_HINT = "Arrows pitch and roll; Z/X rudder; W/S throttle; F/V flaps; T/G trim; Shift full deflection. Gamepad: LB/RB flaps, D-pad trim";

const scene = new FlightScene($("view"));
const pilot = new PilotInput();
let ws = null;
let session = null; // hello message of the current playback
let latest = null; // latest frame row
let paused = false;
let dirty = true;
let inputTimer = null;
let lastReplay = null; // path of the replay last played, to restart it from a seek after it ended
let pendingSeek = null; // time of the last seek, until its frame arrives

// Optional URL parameters: ?source=live|live_lqr|manual|manual_wind|<log path>&seed=3&speed=5&autoplay=1
const params = new URLSearchParams(location.search);
let autoplay = params.get("autoplay") === "1";
if (params.has("seed")) els.seed.value = params.get("seed");
if (params.has("speed") && [...els.speed.options].some((o) => o.value === params.get("speed"))) els.speed.value = params.get("speed");
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const deg360 = (rad) => ((rad * units.DEG) % 360 + 360) % 360;
const flying = () => session?.source === "manual" && !els.stop.disabled;

function say(text) {
  els.message.textContent = text;
}

function setPlaying(playing) {
  els.pause.disabled = !playing;
  els.stop.disabled = !playing;
  els.pause.textContent = "Pause";
  paused = false;
  if (!playing) stopInput();
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

const FILTER_FROM = 20; // show the filter box once there are this many recorded flights
const sourceFilter = $("source-filter");
let allLogs = [];

function populateSources(logs = allLogs) {
  allLogs = logs;
  const current = els.source.value;
  els.source.replaceChildren();
  els.source.add(new Option("Fly it yourself (calm air)", MANUAL));
  els.source.add(new Option("Fly it yourself (wind and turbulence)", MANUAL_WIND));
  els.source.add(new Option("Watch the PID autopilot", LIVE));
  els.source.add(new Option("Watch the LQR autopilot", LIVE_LQR));
  sourceFilter.hidden = logs.length < FILTER_FROM && !sourceFilter.value;
  for (const g of groupLogs(logs, sourceFilter.value)) {
    const group = document.createElement("optgroup");
    group.label = g.label;
    for (const o of g.options) group.append(new Option(o.label, o.value));
    if (g.more) {
      const more = new Option(`… ${g.more} more: filter by seed to find them`, "");
      more.disabled = true;
      group.append(more);
    }
    els.source.add(group);
  }
  if ([...els.source.options].some((o) => o.value === current && !o.disabled)) els.source.value = current;
  updateSourceOptions();
}

function updateSourceOptions() {
  const v = els.source.value;
  els.seed.hidden = els.seedLabel.hidden = !isLive(v) && !isManual(v);
  els.record.hidden = els.recordLabel.hidden = !isManual(v);
  els.speed.disabled = isManual(v); // manual flights run in real time
}

function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.addEventListener("open", () => {
    say("Fly the task yourself, watch the autopilot fly it, or replay a recorded flight. Then press Play.");
    send({ type: "list" });
  });
  ws.addEventListener("close", () => {
    setPlaying(false);
    say("Lost the connection to the stream server. Retrying…");
    setTimeout(connect, 2000);
  });
  ws.addEventListener("message", (ev) => handle(JSON.parse(ev.data)));
}

function startInput() {
  stopInput();
  let last = performance.now();
  inputTimer = setInterval(() => {
    const now = performance.now();
    const v = pilot.update((now - last) / 1000);
    last = now;
    if (!paused) send({ type: "input", ...v });
    $("pad-status").textContent = pilot.gamepadName
      ? `Gamepad: ${pilot.gamepadName.replace(/\s*\(.*$/, "")}`
      : "No gamepad detected: press a button on it to connect";
  }, 1000 / INPUT_SEND_HZ);
}

function stopInput() {
  clearInterval(inputTimer);
  inputTimer = null;
  $("pad-status").textContent = "";
  els.hint.textContent = VIEW_HINT;
}

function handle(msg) {
  switch (msg.type) {
    case "logs":
      populateSources(msg.logs);
      if (params.has("source")) {
        els.source.value = params.get("source");
        params.delete("source");
      }
      updateSourceOptions();
      if (autoplay) {
        autoplay = false;
        play();
      }
      break;
    case "hello":
      session = msg;
      latest = null;
      pendingSeek = null;
      updateSeekable();
      scene.reset();
      scene.setTargets(msg.targets);
      els.run.textContent = `${msg.source === "live" ? `${(msg.pilot ?? "pid").toUpperCase()} autopilot` : { manual: "You are flying", replay: "Replay" }[msg.source]} ${msg.run_id}`;
      say(msg.source === "manual" ? "Fly to the magenta altitude and heading bugs." : "");
      setPlaying(true);
      break;
    case "frame":
      // After a seek, start the trail at the new position (frames sent before the seek may still arrive).
      if (latest && (msg.row.t_s < latest.t_s || (pendingSeek !== null && Math.abs(msg.row.t_s - pendingSeek) < 0.05))) {
        scene.clearTrail();
        if (msg.row.t_s >= latest.t_s) pendingSeek = null;
      }
      if (session?.source === "manual" && latest === null) {
        // Start from the trimmed throttle so the aircraft keeps flying level.
        pilot.reset(msg.row.cmd_throttle_norm ?? 0.7, msg.row.cmd_flaps_norm ?? 0, msg.row.cmd_pitch_trim_norm ?? 0);
        startInput(); // resets the hint, so set the flying hint after it
        els.hint.textContent = FLY_HINT;
      }
      latest = msg.row;
      scene.update(latest);
      dirty = true;
      break;
    case "end":
      setPlaying(false);
      if (msg.reason === "finished") say("Flight finished. Press Play to go again.");
      else if (msg.reason.startsWith("terminated:")) say(`The flight ended early: ${msg.reason.slice(11).replace("_", " ")} limit exceeded.`);
      break;
    case "saved":
      say(`Saved your flight as a demonstration: data/${msg.path}`);
      send({ type: "list" });
      break;
    case "error":
      say(msg.message);
      break;
  }
}

function play() {
  const speed = Number(els.speed.value);
  const seed = Number(els.seed.value) || 0;
  const v = els.source.value;
  if (isManual(v)) send({ type: "play", source: "manual", conditions: v === MANUAL ? "calm" : "windy", seed, record: els.record.checked });
  else if (isLive(v)) send({ type: "play", source: "live", autopilot: v === LIVE_LQR ? "lqr" : "pid", seed, speed });
  else {
    lastReplay = v;
    send({ type: "play", source: "replay", path: v, speed });
  }
  document.activeElement?.blur(); // so the arrow keys fly instead of changing the menu
}

// Seeking (replays only): click or drag the progress bar, or use the arrow keys on it.
const track = $("progress-track");
const canSeek = () => session?.source === "replay" && session.duration_s > 0;

function updateSeekable() {
  const on = canSeek();
  track.classList.toggle("seekable", on);
  if (on) {
    track.setAttribute("role", "slider");
    track.tabIndex = 0;
    track.setAttribute("aria-valuemax", String(Math.round(session.duration_s)));
  } else {
    track.removeAttribute("role");
    track.removeAttribute("tabindex");
  }
}

function showPosition(t) {
  els.fill.style.width = `${Math.min(100, (t / session.duration_s) * 100)}%`;
  els.clock.textContent = `${fmtTime(t)} / ${fmtTime(session.duration_s)}`;
  track.setAttribute("aria-valuenow", String(Math.round(t)));
  track.setAttribute("aria-valuetext", fmtTime(t));
}

function seekTo(t) {
  if (!canSeek()) return;
  t = Math.max(0, Math.min(session.duration_s, t));
  pendingSeek = t;
  if (!els.stop.disabled) send({ type: "seek", t_s: t });
  else if (lastReplay) send({ type: "play", source: "replay", path: lastReplay, speed: Number(els.speed.value), start_s: t });
  showPosition(t);
}

const trackTime = (e) => {
  const r = track.getBoundingClientRect();
  return ((e.clientX - r.left) / r.width) * session.duration_s;
};
let dragging = false;
let lastSeekSent = 0;
track.addEventListener("pointerdown", (e) => {
  if (!canSeek()) return;
  dragging = true;
  track.setPointerCapture(e.pointerId);
  seekTo(trackTime(e));
  lastSeekSent = performance.now();
});
track.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const t = Math.max(0, Math.min(session.duration_s, trackTime(e)));
  if (performance.now() - lastSeekSent > 100 && !els.stop.disabled) {
    seekTo(t); // at most 10 seeks per second while dragging
    lastSeekSent = performance.now();
  } else {
    showPosition(t);
  }
});
track.addEventListener("pointerup", (e) => {
  if (!dragging) return;
  dragging = false;
  seekTo(trackTime(e));
});
track.addEventListener("keydown", (e) => {
  if (!canSeek()) return;
  const now = latest?.t_s ?? 0;
  const t = { ArrowLeft: now - 5, ArrowRight: now + 5, ArrowDown: now - 30, ArrowUp: now + 30, Home: 0, End: session.duration_s }[e.key];
  if (t === undefined) return;
  e.preventDefault();
  seekTo(t);
});

function togglePause() {
  if (els.pause.disabled) return;
  paused = !paused;
  send({ type: paused ? "pause" : "resume" });
  els.pause.textContent = paused ? "Resume" : "Pause";
}

els.play.addEventListener("click", play);
els.pause.addEventListener("click", togglePause);
els.stop.addEventListener("click", () => send({ type: "stop" }));
els.speed.addEventListener("change", () => send({ type: "speed", value: Number(els.speed.value) }));
els.source.addEventListener("change", updateSourceOptions);
sourceFilter.addEventListener("input", () => populateSources());
els.source.addEventListener("focus", () => send({ type: "list" }));
// Chase or cockpit view (C key or the button); remembered in this browser.
function setView(view) {
  scene.setView(view);
  const inside = view === "cockpit";
  $("view-toggle").textContent = inside ? "Chase view" : "Cockpit view";
  $("view-toggle").setAttribute("aria-pressed", String(inside));
  try {
    localStorage.setItem("flightsim.view", view);
  } catch {
    // not remembered; the view still changes
  }
}
$("view-toggle").addEventListener("click", () => setView(scene.view === "cockpit" ? "chase" : "cockpit"));
try {
  if (localStorage.getItem("flightsim.view") === "cockpit") setView("cockpit");
} catch {
  // storage unavailable: start in the chase view
}

document.addEventListener("keydown", (e) => {
  const inForm = ["INPUT", "SELECT", "BUTTON"].includes(document.activeElement?.tagName);
  if (e.code === "KeyC" && !inForm && !e.repeat) {
    setView(scene.view === "cockpit" ? "chase" : "cockpit");
    return;
  }
  if (e.code === "Space" && !inForm) {
    e.preventDefault();
    togglePause();
  } else if (flying() && HANDLED_KEYS.has(e.code) && !(inForm && document.activeElement?.tagName !== "BUTTON")) {
    e.preventDefault();
    pilot.keydown(e);
  }
});
document.addEventListener("keyup", (e) => pilot.keyup(e));
window.addEventListener("blur", () => pilot.releaseAll());
window.addEventListener("resize", () => (dirty = true));

function updateReadout(row) {
  const t = session?.targets;
  readout.alt.textContent = row ? `${Math.round(row.alt_msl_m * units.M_TO_FT).toLocaleString("en-US")} ft` : "–";
  readout.talt.textContent = t ? `${Math.round(t.alt_msl_m * units.M_TO_FT).toLocaleString("en-US")} ft` : "–";
  readout.hdg.textContent = row ? `${String(Math.round(deg360(row.psi_rad)) % 360).padStart(3, "0")}°` : "–";
  readout.thdg.textContent = t ? `${String(Math.round(deg360(t.heading_rad)) % 360).padStart(3, "0")}°` : "–";
  readout.kias.textContent = row ? `${(row.cas_mps * units.MPS_TO_KT).toFixed(0)} kt` : "–";
  readout.aoa.textContent = row ? `${(row.alpha_rad * units.DEG).toFixed(1)}°` : "–";
  readout.g.textContent = row ? `${(-row.az_mps2 / units.G).toFixed(2)} g` : "–";
  if (row) {
    const flapDeg = row.flap_pos_rad * units.DEG;
    // POH 1981 C172P Figure 2-1: 110 KIAS with 10 deg flaps, 85 KIAS beyond.
    const vfe = flapDeg <= 0.5 ? Infinity : flapDeg <= 10.5 ? 110 : 85;
    const over = row.cas_mps * units.MPS_TO_KT > vfe;
    readout.flaps.textContent = `${Math.round(flapDeg)}°${over ? ` over ${vfe} kt limit` : ""}`;
    readout.flaps.classList.toggle("warn", over);
    const trim = row.cmd_pitch_trim_norm;
    readout.trim.textContent = trim == null ? "–" : `${Math.round(Math.abs(trim) * 100)}% ${trim >= 0 ? "nose down" : "nose up"}`;
  } else {
    readout.flaps.textContent = readout.trim.textContent = "–";
  }
}

function frame() {
  if (dirty) {
    drawAll(gauges, latest, session?.targets);
    updateReadout(latest);
    if (latest && session?.duration_s && !dragging) showPosition(latest.t_s);
    dirty = false;
  }
  scene.render();
  const bx = scene.boresightX();
  const marker = $("boresight");
  marker.style.display = bx === null ? "none" : "block";
  if (bx !== null) marker.style.left = `${(bx * 100).toFixed(2)}%`;
  requestAnimationFrame(frame);
}

// Stick settings dialog: edits pilot.stick in place, applies immediately, saves per browser.
const AXIS_LABELS = { pitch: "Pitch", roll: "Roll", rudder: "Rudder" };
function renderStickRows() {
  const rows = $("stick-rows");
  rows.replaceChildren();
  for (const axis of AXES) {
    const row = document.createElement("div");
    row.className = "stick-row";
    row.innerHTML = `<span class="axis">${AXIS_LABELS[axis]}</span>`;
    for (const [key, label, min] of [["sensitivity", "Sensitivity", 0.1], ["expo", "Expo", 0]]) {
      const id = `stick-${axis}-${key}`;
      const lab = document.createElement("label");
      lab.htmlFor = id;
      const input = Object.assign(document.createElement("input"), { type: "range", id, min, max: 1, step: 0.05 });
      input.value = pilot.stick[axis][key];
      input.setAttribute("aria-label", `${AXIS_LABELS[axis]} ${label.toLowerCase()}`);
      const out = document.createElement("output");
      out.htmlFor = id;
      out.textContent = Number(input.value).toFixed(2);
      input.addEventListener("input", () => {
        pilot.stick[axis][key] = Number(input.value);
        out.textContent = Number(input.value).toFixed(2);
        saveSettings(pilot.stick);
      });
      const what = Object.assign(document.createElement("span"), { className: "what", textContent: label });
      lab.append(what, input);
      row.append(lab, out);
    }
    rows.append(row);
  }
}
function renderDeadzone() {
  const input = $("stick-deadzone");
  input.value = pilot.stick.deadzone;
  $("stick-deadzone-out").textContent = Number(input.value).toFixed(2);
}
$("stick-deadzone").addEventListener("input", (e) => {
  pilot.stick.deadzone = Number(e.target.value);
  $("stick-deadzone-out").textContent = pilot.stick.deadzone.toFixed(2);
  saveSettings(pilot.stick);
});

// Live raw stick positions while the dialog is open, so stick drift is visible.
let liveTimer = null;
function updateLive() {
  pilot.update(0); // refresh rawAxes without advancing anything time-based
  const axes = pilot.rawAxes;
  const el = $("stick-live");
  if (!axes) {
    el.textContent = "No gamepad detected: press a button on it to connect.";
    return;
  }
  const fmt = (v) => (v >= 0 ? "+" : "") + (v ?? 0).toFixed(3);
  const names = [["Left stick X (roll)", 0], ["Left stick Y (pitch)", 1], ["Right stick X (rudder)", 2]];
  el.innerHTML = names
    .map(([n, i]) => {
      const raw = axes[i] ?? 0;
      const corrected = centred(raw, pilot.stick.centre[i]);
      const drift = Math.abs(corrected) >= pilot.stick.deadzone ? ' class="drift"' : "";
      return `<span${drift}>${n}: raw ${fmt(raw)}, after calibration ${fmt(corrected)}</span>`;
    })
    .join("<br>") + "<br>Hands off, a value in yellow is drift that reaches the controls: calibrate the centre, or raise the dead zone.";
}
$("stick-dialog").addEventListener("close", () => clearInterval(liveTimer));

function showCalibration() {
  const c = pilot.stick.centre;
  const any = c.some((v) => v !== 0);
  $("stick-calibrate-status").textContent = any
    ? `Centre: ${c.map((v) => (v >= 0 ? "+" : "") + v.toFixed(3)).join(", ")}`
    : "Not calibrated";
}

// Average each stick's rest position over one second, hands off.
$("stick-calibrate").addEventListener("click", () => {
  const button = $("stick-calibrate");
  if (!pilot.rawAxes) {
    $("stick-calibrate-status").textContent = "No gamepad detected: press a button on it first.";
    return;
  }
  button.disabled = true;
  const sums = [0, 0, 0];
  let n = 0;
  $("stick-calibrate-status").textContent = "Measuring, keep your hands off the sticks…";
  const timer = setInterval(() => {
    pilot.update(0);
    if (pilot.rawAxes) {
      for (let i = 0; i < 3; i++) sums[i] += pilot.rawAxes[i] ?? 0;
      n++;
    }
    if (n >= 20) {
      clearInterval(timer);
      const centre = sums.map((s) => s / n);
      if (centre.some((c) => Math.abs(c) >= 0.5)) {
        $("stick-calibrate-status").textContent = "A stick was held far from centre; let go and try again.";
      } else {
        pilot.stick.centre = centre;
        saveSettings(pilot.stick);
        showCalibration();
      }
      button.disabled = false;
    }
  }, 50);
});
$("stick-calibrate-clear").addEventListener("click", () => {
  pilot.stick.centre = [0, 0, 0];
  saveSettings(pilot.stick);
  showCalibration();
});

$("stick-open").addEventListener("click", () => {
  renderStickRows();
  renderDeadzone();
  showCalibration();
  updateLive();
  clearInterval(liveTimer);
  liveTimer = setInterval(updateLive, 100);
  $("stick-dialog").showModal();
});
$("stick-reset").addEventListener("click", () => {
  const centre = pilot.stick.centre; // reset the feel, keep the controller's calibration
  pilot.stick = { ...structuredClone(DEFAULTS), centre };
  saveSettings(pilot.stick);
  renderStickRows();
  renderDeadzone();
});

populateSources([]);
if (document.fonts) document.fonts.ready.then(() => (dirty = true));
connect();
requestAnimationFrame(frame);
