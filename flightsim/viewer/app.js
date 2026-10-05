// Viewer: consumes the state stream (protocol 1) and draws it. Rendering never affects
// the physics. The only message that does is pilot input during a manual flight, sent
// as stick/pedal/throttle values that the server applies as policy actions.

import { FlightScene } from "./scene.js";
import { drawAll, units } from "./gauges.js";
import { HANDLED_KEYS, PilotInput } from "./input.js";
import { AXES, CONTROLS, DEFAULTS, MAX_CALIBRATION_SPREAD, MAX_CENTRE, controlValue, defaultProfile, detectAxis, saveSettings } from "./stick.js";
import { groupLogs } from "./flightlist.js";
import { FlightSound } from "./sound.js";

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
const LIVE_APPROACH = "live_approach";
const isLive = (v) => v === LIVE || v === LIVE_LQR || v === LIVE_APPROACH;
const LIVE_AUTOPILOT = { [LIVE]: "pid", [LIVE_LQR]: "lqr", [LIVE_APPROACH]: "approach" };
const MANUAL = "manual"; // calm air
const MANUAL_WIND = "manual_wind";
const MANUAL_APPROACH = "manual_approach";
const isManual = (v) => v === MANUAL || v === MANUAL_WIND || v === MANUAL_APPROACH;
const MANUAL_CONDITIONS = { [MANUAL]: "calm", [MANUAL_WIND]: "windy", [MANUAL_APPROACH]: "approach" };
// Why an approach ended (envs/approach.py failure reasons), for the message line.
const LANDING_FAILURES = {
  undershoot: "touched down short of the runway",
  off_runway: "left the runway",
  hard_landing: "hard landing (over 600 ft/min at touchdown)",
  nose_first: "touched down nose wheel first (flare: raise the nose so the main wheels touch first)",
  wing_low: "touched down with too much bank",
  tail_strike: "tail strike (nose too high)",
  wingtip_strike: "wingtip struck the ground",
  nose_strike: "propeller/nose struck the ground",
  lost_approach: "too far off the glide path or centreline",
};
const INPUT_SEND_HZ = 30;
const VIEW_HINT = "Drag to look around, scroll to zoom, space to pause, C for cockpit view, M for sound";
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
  els.source.add(new Option("Fly an approach to runway 09 and land (calm)", MANUAL_APPROACH));
  els.source.add(new Option("Watch the PID autopilot", LIVE));
  els.source.add(new Option("Watch the LQR autopilot", LIVE_LQR));
  els.source.add(new Option("Watch the approach autopilot land on runway 09", LIVE_APPROACH));
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
      scene.setApproach(msg.approach ?? null);
      showApproachRows(Boolean(msg.approach));
      const pilotName = { pid: "PID", lqr: "LQR", approach: "Approach" }[msg.pilot ?? "pid"] ?? msg.pilot;
      els.run.textContent = `${msg.source === "live" ? `${pilotName} autopilot` : { manual: "You are flying", replay: "Replay" }[msg.source]} ${msg.run_id}`;
      say(msg.source === "manual"
        ? (els.source.value === MANUAL_APPROACH ? "Follow the glide path to runway 09 (ahead), flare and land main wheels first." : "Fly to the magenta altitude and heading bugs.")
        : "");
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
        const off = pilot.offCentre();
        if (off.length) {
          const list = off.map((a) => `${a.axis} ${a.value >= 0 ? "+" : ""}${a.value.toFixed(2)}`).join(", ");
          say(`${els.message.textContent} Note: the gamepad reads ${list} at the start; if your hands are off the sticks, recalibrate in Stick settings.`);
        }
      }
      latest = msg.row;
      scene.update(latest);
      if (!paused) sound.update(latest, { view: scene.view, distanceM: scene.orbit.distance });
      dirty = true;
      break;
    case "end":
      setPlaying(false);
      sound.silence();
      if (msg.reason === "landed") {
        const td = msg.landing.touchdown;
        const zone = td.in_zone ? "in the touchdown zone" : `${Math.round(td.along_m)} m past the threshold (zone 100-400 m)`;
        say(`Landed ${zone}, ${Math.round(td.sink_mps * 196.85)} ft/min, ${Math.round(td.cas_mps * 1.94384)} kt, ` +
          `${Math.abs(td.cross_m).toFixed(1)} m ${td.cross_m >= 0 ? "right" : "left"} of the centreline${msg.landing.bounces ? `, ${msg.landing.bounces} bounce(s)` : ""}. Press Play to go again.`);
      } else if (msg.reason === "finished") say("Flight finished. Press Play to go again.");
      else if (msg.reason.startsWith("terminated:")) {
        const why = msg.reason.slice(11);
        say(LANDING_FAILURES[why] ? `The flight ended: ${LANDING_FAILURES[why]}.` : `The flight ended early: ${why.replace("_", " ")} limit exceeded.`);
      }
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
  sound.unlock(); // a click: browsers allow audio from here on
  const speed = Number(els.speed.value);
  const seed = Number(els.seed.value) || 0;
  const v = els.source.value;
  if (isManual(v)) send({ type: "play", source: "manual", conditions: MANUAL_CONDITIONS[v], seed, record: els.record.checked });
  else if (isLive(v)) send({ type: "play", source: "live", autopilot: LIVE_AUTOPILOT[v], seed, speed });
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

// Sound: engine, wind, stall horn and flap motor (sound.js); on/off and volume remembered.
const sound = new FlightSound();
try {
  const saved = JSON.parse(localStorage.getItem("flightsim.sound"));
  if (saved && typeof saved.enabled === "boolean" && Number.isFinite(saved.volume)) {
    sound.setEnabled(saved.enabled);
    sound.setVolume(saved.volume);
  }
} catch {
  // storage unavailable: defaults (on, 60%)
}
function saveSound() {
  try {
    localStorage.setItem("flightsim.sound", JSON.stringify({ enabled: sound.enabled, volume: sound.volume }));
  } catch {
    // not persisted
  }
}
function showSound() {
  $("sound-toggle").textContent = sound.enabled ? "Sound on" : "Sound off";
  $("sound-toggle").setAttribute("aria-pressed", String(sound.enabled));
  $("sound-volume").value = sound.volume;
}
function toggleSound() {
  sound.unlock();
  sound.setEnabled(!sound.enabled);
  showSound();
  saveSound();
}
$("sound-toggle").addEventListener("click", toggleSound);
$("sound-volume").addEventListener("input", (e) => {
  sound.unlock();
  sound.setVolume(Number(e.target.value));
  saveSound();
});
document.addEventListener("pointerdown", () => sound.unlock(), { once: true });
showSound();

function togglePause() {
  if (els.pause.disabled) return;
  paused = !paused;
  if (paused) sound.silence();
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
  if (e.code === "KeyM" && !inForm && !e.repeat) {
    toggleSound();
    return;
  }
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

// Approach: glide path and centreline deviations replace the cruise targets.
function showApproachRows(on) {
  $("l-talt").textContent = on ? "Glide path" : "Altitude target";
  $("l-thdg").textContent = on ? "Centreline" : "Heading target";
  $("l-dist").hidden = $("r-dist").hidden = !on;
}

function approachDeviations(row, a) {
  const R_EARTH = 6371000, h = (a.heading_deg * Math.PI) / 180;
  const dn = row.lat_rad * R_EARTH - a.threshold_north_m, de = row.lon_rad * R_EARTH - a.threshold_east_m;
  const along = dn * Math.cos(h) + de * Math.sin(h), cross = -dn * Math.sin(h) + de * Math.cos(h);
  const gp = row.alt_msl_m - (a.elevation_m + Math.max(0, a.aim_point_m - along) * Math.tan((a.glide_path_deg * Math.PI) / 180));
  return { along, cross, gp };
}

function updateReadout(row) {
  const a = session?.approach;
  if (a) {
    if (row) {
      const d = approachDeviations(row, a);
      const ft = Math.round(d.gp * units.M_TO_FT);
      readout.talt.textContent = Math.abs(ft) < 10 ? "on path" : `${Math.abs(ft)} ft ${ft > 0 ? "high" : "low"}`;
      readout.thdg.textContent = Math.abs(d.cross) < 2 ? "on centreline" : `${Math.abs(d.cross).toFixed(0)} m ${d.cross > 0 ? "right" : "left"}`;
      $("r-dist").textContent = d.along < 0 ? `${(-d.along / 1852).toFixed(2)} nm` : "over the runway";
    } else {
      readout.talt.textContent = readout.thdg.textContent = $("r-dist").textContent = "–";
    }
  }
  const t = a ? null : session?.targets;
  readout.alt.textContent = row ? `${Math.round(row.alt_msl_m * units.M_TO_FT).toLocaleString("en-US")} ft` : "–";
  if (!a) readout.talt.textContent = t ? `${Math.round(t.alt_msl_m * units.M_TO_FT).toLocaleString("en-US")} ft` : "–";
  readout.hdg.textContent = row ? `${String(Math.round(deg360(row.psi_rad)) % 360).padStart(3, "0")}°` : "–";
  if (!a) readout.thdg.textContent = t ? `${String(Math.round(deg360(t.heading_rad)) % 360).padStart(3, "0")}°` : "–";
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

// Controller axes: which axis drives each control, per device (stick.js profiles).
const CONTROL_LABELS = { roll: "Roll", pitch: "Pitch", rudder: "Rudder", throttle: "Throttle" };
const STANDARD_AXIS_NAMES = ["left stick X", "left stick Y", "right stick X", "right stick Y"];
const DETECT_PROMPTS = {
  roll: "Move the stick fully RIGHT and hold…",
  pitch: "Push the stick fully FORWARD (nose down) and hold…",
  rudder: "Push the RIGHT pedal (or twist right) and hold…",
  throttle: "Move the throttle to FULL power and hold…",
};
const signed = (v, d = 3) => (v >= 0 ? "+" : "") + (v ?? 0).toFixed(d);
const axisName = (pad, i) => `Axis ${i}${pad.mapping === "standard" && STANDARD_AXIS_NAMES[i] ? ` (${STANDARD_AXIS_NAMES[i]})` : ""}`;

function renderAxisRows() {
  const rows = $("axis-rows");
  rows.replaceChildren();
  const pad = pilot.readPad();
  $("stick-device").textContent = pad
    ? `${pad.id.replace(/\s*\(.*$/, "")}${pad.mapping === "standard" ? "" : " (no standard layout: map its axes below)"}`
    : "No gamepad detected: press a button on it to connect.";
  if (!pad) return;
  const profile = pilot.profile();
  for (const control of CONTROLS) {
    const m = profile.map[control];
    const row = document.createElement("div");
    row.className = "axis-row";
    const label = Object.assign(document.createElement("span"), { className: "axis", textContent: CONTROL_LABELS[control] });
    const select = document.createElement("select");
    select.setAttribute("aria-label", `${CONTROL_LABELS[control]} axis`);
    select.add(new Option(control === "throttle" ? "None (keys, triggers)" : control === "rudder" ? "None (keys Z/X)" : "None (keyboard)", ""));
    for (let i = 0; i < pad.axes.length; i++) select.add(new Option(axisName(pad, i), String(i)));
    select.value = m.axis === null ? "" : String(m.axis);
    select.addEventListener("change", () => {
      m.axis = select.value === "" ? null : Number(select.value);
      saveSettings(pilot.stick);
      showCalibration();
    });
    const invert = Object.assign(document.createElement("input"), { type: "checkbox", checked: m.invert });
    invert.addEventListener("change", () => {
      m.invert = invert.checked;
      saveSettings(pilot.stick);
    });
    const invLabel = document.createElement("label");
    invLabel.append(invert, "Invert");
    const detect = Object.assign(document.createElement("button"), { type: "button", textContent: "Detect" });
    detect.addEventListener("click", () => detectFor(control, row, detect));
    const live = Object.assign(document.createElement("span"), { className: "live" });
    live.dataset.control = control;
    row.append(label, select, invLabel, detect, live);
    rows.append(row);
  }
}

// Detect: the pilot moves the control in its positive direction; the axis that moves most
// is mapped, inverted if it moved negative.
function detectFor(control, row, button) {
  const pad = pilot.readPad();
  if (!pad) return;
  const baseline = [...pad.axes];
  const samples = [];
  const prompt = Object.assign(document.createElement("span"), { className: "prompt", textContent: DETECT_PROMPTS[control], role: "status" });
  row.append(prompt);
  button.disabled = true;
  const timer = setInterval(() => {
    if (pilot.readPad()) samples.push([...pilot.rawAxes]);
    if (samples.length >= 60) { // 3 s
      clearInterval(timer);
      const found = detectAxis(baseline, samples);
      if (found) {
        Object.assign(pilot.profile().map[control], found);
        saveSettings(pilot.stick);
        renderAxisRows();
        showCalibration();
      } else {
        prompt.textContent = "No movement detected: try again and move it all the way.";
        button.disabled = false;
      }
    }
  }, 50);
}

// Live values while the dialog is open, so mapping and stick drift are visible.
let liveTimer = null;
function updateLive() {
  const pad = pilot.readPad(); // raw axes only; the control input is not touched
  const el = $("stick-live");
  if (!pad) {
    $("stick-axes-raw").textContent = "";
    el.textContent = "";
    return;
  }
  const profile = pilot.profile();
  $("stick-axes-raw").textContent = "Raw axes: " + pad.axes.map((v, i) => `${i}: ${signed(v, 2)}`).join("  ");
  let drift = false;
  for (const span of document.querySelectorAll("#axis-rows .live")) {
    const v = controlValue(profile, pad.axes, span.dataset.control);
    const off = span.dataset.control !== "throttle" && v !== null && Math.abs(v) >= pilot.stick.deadzone;
    drift ||= off;
    span.textContent = v === null ? "" : span.dataset.control === "throttle" ? `${Math.round(v * 100)}%` : signed(v, 2);
    span.style.color = off ? "#e2b93b" : "";
  }
  el.textContent = drift
    ? "Hands off, a value in yellow is drift that reaches the controls: calibrate the centre, or raise the dead zone."
    : "Hands off, all mapped sticks rest inside the dead zone.";
}
$("stick-dialog").addEventListener("close", () => clearInterval(liveTimer));

function showCalibration() {
  const profile = pilot.profile();
  if (!profile) {
    $("stick-calibrate-status").textContent = "";
    return;
  }
  const parts = ["roll", "pitch", "rudder"]
    .filter((c) => profile.map[c].axis !== null && profile.centre[profile.map[c].axis] !== undefined)
    .map((c) => `${c} ${signed(profile.centre[profile.map[c].axis])}`);
  $("stick-calibrate-status").textContent = parts.length ? `Centre: ${parts.join(", ")}` : "Not calibrated";
}

// Average the mapped sticks' rest positions over one second, hands off.
$("stick-calibrate").addEventListener("click", () => {
  const button = $("stick-calibrate");
  const pad = pilot.readPad();
  if (!pad) {
    $("stick-calibrate-status").textContent = "No gamepad detected: press a button on it first.";
    return;
  }
  const profile = pilot.profile();
  const axes = [...new Set(["roll", "pitch", "rudder"].map((c) => profile.map[c].axis).filter((a) => a !== null))];
  if (!axes.length) {
    $("stick-calibrate-status").textContent = "Map the stick axes first.";
    return;
  }
  button.disabled = true;
  const sums = axes.map(() => 0), lo = axes.map(() => Infinity), hi = axes.map(() => -Infinity);
  let n = 0;
  $("stick-calibrate-status").textContent = "Measuring, keep your hands off the sticks…";
  const timer = setInterval(() => {
    if (pilot.readPad()) {
      axes.forEach((a, k) => {
        const v = pilot.rawAxes[a] ?? 0;
        sums[k] += v;
        lo[k] = Math.min(lo[k], v);
        hi[k] = Math.max(hi[k], v);
      });
      n++;
    }
    if (n >= 20) {
      clearInterval(timer);
      const centre = sums.map((s) => s / n);
      if (hi.some((h, k) => h - lo[k] > MAX_CALIBRATION_SPREAD)) {
        $("stick-calibrate-status").textContent = "A stick moved while measuring; take your hands off the sticks and try again.";
      } else if (centre.some((c) => Math.abs(c) >= MAX_CENTRE)) {
        const bad = axes.filter((a, k) => Math.abs(centre[k]) >= MAX_CENTRE).map((a) => `axis ${a} at ${signed(centre[axes.indexOf(a)], 2)}`);
        $("stick-calibrate-status").textContent = `Rests too far from centre for drift (${bad.join(", ")}): let go of it, or if it is faulty, map that control to another axis.`;
      } else {
        profile.centre = Object.fromEntries(axes.map((a, k) => [a, centre[k]]));
        saveSettings(pilot.stick);
        showCalibration();
      }
      button.disabled = false;
    }
  }, 50);
});
$("stick-calibrate-clear").addEventListener("click", () => {
  const profile = pilot.profile();
  if (!profile) return;
  profile.centre = {};
  saveSettings(pilot.stick);
  showCalibration();
});
$("stick-axes-default").addEventListener("click", () => {
  if (!pilot.readPad()) return;
  pilot.stick.devices[pilot.pad.id] = defaultProfile(pilot.pad.mapping === "standard");
  saveSettings(pilot.stick);
  renderAxisRows();
  showCalibration();
});

$("stick-open").addEventListener("click", () => {
  renderStickRows();
  renderDeadzone();
  renderAxisRows();
  showCalibration();
  updateLive();
  clearInterval(liveTimer);
  liveTimer = setInterval(updateLive, 100);
  $("stick-dialog").showModal();
});
$("stick-reset").addEventListener("click", () => {
  const devices = pilot.stick.devices; // reset the feel, keep each controller's axes and calibration
  pilot.stick = { ...structuredClone(DEFAULTS), devices };
  saveSettings(pilot.stick);
  renderStickRows();
  renderDeadzone();
});

populateSources([]);
if (document.fonts) document.fonts.ready.then(() => (dirty = true));
connect();
requestAnimationFrame(frame);
