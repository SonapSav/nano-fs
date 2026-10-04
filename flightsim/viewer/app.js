// Viewer: consumes the state stream (protocol 1) and draws it. Rendering never affects
// the physics. The only message that does is pilot input during a manual flight, sent
// as stick/pedal/throttle values that the server applies as policy actions.

import { FlightScene } from "./scene.js";
import { drawAll, units } from "./gauges.js";
import { HANDLED_KEYS, PilotInput } from "./input.js";
import { AXES, DEFAULTS, saveSettings } from "./stick.js";

const $ = (id) => document.getElementById(id);
const els = {
  source: $("source"), seed: $("seed"), seedLabel: $("seed-label"), record: $("record"), recordLabel: $("record-label"),
  play: $("play"), pause: $("pause"), stop: $("stop"), speed: $("speed"), fill: $("progress-fill"), clock: $("clock"),
  message: $("message"), run: $("run"), hint: $("hint"),
};
const gauges = { asi: $("asi"), ai: $("ai"), alt: $("alt"), tc: $("tc"), hi: $("hi"), vsi: $("vsi"), tach: $("tach"), controls: $("controls") };
const readout = { alt: $("r-alt"), talt: $("r-talt"), hdg: $("r-hdg"), thdg: $("r-thdg"), kias: $("r-kias"), aoa: $("r-aoa"), g: $("r-g") };

const LIVE = "live";
const MANUAL = "manual";
const INPUT_SEND_HZ = 30;
const VIEW_HINT = "Drag to look around, scroll to zoom, space to pause";
const FLY_HINT = "Arrows pitch and roll; Z and X rudder; W and S throttle; hold Shift for full deflection; or use a gamepad";

const scene = new FlightScene($("view"));
const pilot = new PilotInput();
let ws = null;
let session = null; // hello message of the current playback
let latest = null; // latest frame row
let paused = false;
let dirty = true;
let inputTimer = null;

// Optional URL parameters: ?source=live|manual|<log path>&seed=3&speed=5&autoplay=1
const params = new URLSearchParams(location.search);
let autoplay = params.get("autoplay") === "1";
if (params.has("seed")) els.seed.value = params.get("seed");
if (params.has("speed")) els.speed.value = params.get("speed");
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const deg360 = (rad) => ((rad * units.DEG) % 360 + 360) % 360;
const flying = () => session?.source === MANUAL && !els.stop.disabled;

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

function populateSources(logs) {
  const current = els.source.value;
  els.source.replaceChildren();
  els.source.add(new Option("Fly it yourself", MANUAL));
  els.source.add(new Option("Watch the PID autopilot", LIVE));
  for (const [label, filter] of [["Your demonstrations", (l) => l.path.startsWith("demos/")], ["Recorded flights", (l) => !l.path.startsWith("demos/")]]) {
    const items = logs.filter(filter);
    if (!items.length) continue;
    const group = document.createElement("optgroup");
    group.label = label;
    for (const log of items) group.append(new Option(`${log.path} (${(log.rows / 120 / 60).toFixed(1)} min)`, log.path));
    els.source.add(group);
  }
  if ([...els.source.options].some((o) => o.value === current)) els.source.value = current;
  updateSourceOptions();
}

function updateSourceOptions() {
  const v = els.source.value;
  els.seed.hidden = els.seedLabel.hidden = v !== LIVE && v !== MANUAL;
  els.record.hidden = els.recordLabel.hidden = v !== MANUAL;
  els.speed.disabled = v === MANUAL; // manual flights run in real time
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
      scene.reset();
      scene.setTargets(msg.targets);
      els.run.textContent = `${{ live: "Autopilot", manual: "You are flying", replay: "Replay" }[msg.source]} ${msg.run_id}`;
      say(msg.source === MANUAL ? "Fly to the magenta altitude and heading bugs." : "");
      setPlaying(true);
      break;
    case "frame":
      if (session?.source === MANUAL && latest === null) {
        // Start from the trimmed throttle so the aircraft keeps flying level.
        pilot.reset(msg.row.cmd_throttle_norm ?? 0.7);
        els.hint.textContent = FLY_HINT;
        startInput();
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
  if (v === MANUAL) send({ type: "play", source: MANUAL, seed, record: els.record.checked });
  else if (v === LIVE) send({ type: "play", source: LIVE, seed, speed });
  else send({ type: "play", source: "replay", path: v, speed });
  document.activeElement?.blur(); // so the arrow keys fly instead of changing the menu
}

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
els.source.addEventListener("focus", () => send({ type: "list" }));
document.addEventListener("keydown", (e) => {
  const inForm = ["INPUT", "SELECT", "BUTTON"].includes(document.activeElement?.tagName);
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
}

function frame() {
  if (dirty) {
    drawAll(gauges, latest, session?.targets);
    updateReadout(latest);
    if (latest && session?.duration_s) {
      els.fill.style.width = `${Math.min(100, (latest.t_s / session.duration_s) * 100)}%`;
      els.clock.textContent = `${fmtTime(latest.t_s)} / ${fmtTime(session.duration_s)}`;
    }
    dirty = false;
  }
  scene.render();
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
$("stick-open").addEventListener("click", () => {
  renderStickRows();
  $("stick-dialog").showModal();
});
$("stick-reset").addEventListener("click", () => {
  pilot.stick = structuredClone(DEFAULTS);
  saveSettings(pilot.stick);
  renderStickRows();
});

populateSources([]);
if (document.fonts) document.fonts.ready.then(() => (dirty = true));
connect();
requestAnimationFrame(frame);
