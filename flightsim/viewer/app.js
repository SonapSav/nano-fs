// Viewer: consumes the state stream (protocol 1) and draws it. Rendering never affects
// the physics. The only message that does is pilot input during a manual flight, sent
// as stick/pedal/throttle values that the server applies as policy actions.

import { FlightScene } from "./scene.js";
import { CLIMB_KT, InstrumentPanel, PANEL_CHANNEL, PANEL_TIMEOUT_MS, ROTATE_KT } from "./panel.js";
import { drawHud } from "./hud.js";
import { FrameStats } from "./perf.js";
import { benchReport, runBench } from "./bench.js";
import { FrameBuffer } from "./smooth.js";
import { HANDLED_KEYS, PilotInput } from "./input.js";
import { AXES, BUTTONS, CONTROLS, DEFAULTS, MAX_CALIBRATION_SPREAD, MAX_CENTRE, buttonValue, controlValue, copyFeel, defaultProfile, detectAxis, detectButton, saveSettings } from "./stick.js";
import { groupLogs } from "./flightlist.js";
import { FlightSound } from "./sound.js";

const $ = (id) => document.getElementById(id);
const panel = new InstrumentPanel($("panel"));
const els = {
  source: $("source"), seed: $("seed"), seedLabel: $("seed-label"), record: $("record"), recordLabel: $("record-label"),
  play: $("play"), pause: $("pause"), stop: $("stop"), speed: $("speed"), fill: $("progress-fill"), clock: $("clock"),
  message: $("message"), hint: $("hint"),
};

const LIVE = "live"; // PID autopilot
const LIVE_LQR = "live_lqr";
const LIVE_APPROACH = "live_approach";
const LIVE_TAKEOFF = "live_takeoff";
const LIVE_CIRCUIT = "live_circuit";
const LIVE_AUTOPILOT = { [LIVE]: "pid", [LIVE_LQR]: "lqr", [LIVE_APPROACH]: "approach", [LIVE_TAKEOFF]: "takeoff", [LIVE_CIRCUIT]: "circuit" };
const isLive = (v) => v in LIVE_AUTOPILOT;
const MANUAL = "manual"; // calm air
const MANUAL_WIND = "manual_wind";
const MANUAL_APPROACH = "manual_approach";
const MANUAL_CROSSWIND = "manual_crosswind";
const MANUAL_TAKEOFF = "manual_takeoff";
const MANUAL_TAKEOFF_XW = "manual_takeoff_crosswind";
const MANUAL_CIRCUIT = "manual_circuit";
const MANUAL_CIRCUIT_XW = "manual_circuit_crosswind";
const MANUAL_CONDITIONS = {
  [MANUAL]: "calm", [MANUAL_WIND]: "windy", [MANUAL_APPROACH]: "approach", [MANUAL_CROSSWIND]: "approach_crosswind",
  [MANUAL_TAKEOFF]: "takeoff", [MANUAL_TAKEOFF_XW]: "takeoff_crosswind",
  [MANUAL_CIRCUIT]: "circuit", [MANUAL_CIRCUIT_XW]: "circuit_crosswind",
};
const isManual = (v) => v in MANUAL_CONDITIONS;
// Why an approach ended (envs/approach.py failure reasons), for the message line.
const LANDING_FAILURES = {
  undershoot: "touched down short of the runway",
  off_runway: "left the runway",
  hard_landing: "hard landing (over 600 ft/min at touchdown)",
  nose_first: "touched down nose wheel first (flare: raise the nose so the main wheels touch first)",
  wing_low: "touched down with too much bank",
  side_load: "touched down still crabbed (line the nose up with the runway using rudder just before touchdown)",
  tail_strike: "tail strike (nose too high)",
  wingtip_strike: "wingtip struck the ground",
  nose_strike: "propeller/nose struck the ground",
  lost_approach: "too far off the glide path or centreline",
  overrun: "ran off the end of the runway",
  no_stop: "did not stop on the runway in time (hold B to brake)",
  // Takeoff (envs/takeoff.py)
  no_liftoff: "did not lift off in time (full throttle, lift the nose wheel at 55 kt)",
  sank_back: "touched the ground again before climbing out (hold the attitude until climbing)",
  lost: "too far off the extended centreline",
};
const INPUT_SEND_HZ = 30;
const VIEW_HINT = "Drag to look around, scroll to zoom, R or double-click to re-centre, space to pause, C for cockpit view (H: HUD), M for sound";
const FLY_HINT = "Arrows pitch and roll; Z/X rudder and nosewheel; W/S throttle; F/V flaps; T/G trim; B brakes; hold a key to build it up, Shift for full deflection; R re-centres the view, H HUD (cockpit view). Gamepad: LB/RB flaps, D-pad trim, B brakes, Y view, X HUD (buttons: Stick settings)";

// Graphics quality (terrain.js QUALITY), remembered in this browser only.
const QUALITY_KEY = "flightsim.quality";
const savedQuality = (() => {
  try {
    return localStorage.getItem(QUALITY_KEY);
  } catch {
    return null;
  }
})();
const initialQuality = ["low", "medium", "high"].includes(savedQuality) ? savedQuality : "high";
const scene = new FlightScene($("view"), initialQuality);
$("quality").value = initialQuality;
$("quality").addEventListener("change", (e) => {
  scene.setQuality(e.target.value);
  try {
    localStorage.setItem(QUALITY_KEY, e.target.value);
  } catch {
    // storage unavailable (private window): the choice lasts for this page only
  }
});
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
  els.source.add(new Option("Fly an approach to runway 09 and land (crosswind, gusts)", MANUAL_CROSSWIND));
  els.source.add(new Option("Take off from runway 09 and climb to 1000 ft (calm)", MANUAL_TAKEOFF));
  els.source.add(new Option("Take off from runway 09 and climb to 1000 ft (crosswind, gusts)", MANUAL_TAKEOFF_XW));
  els.source.add(new Option("Fly a circuit: take off, left-hand pattern, land on 09 (calm)", MANUAL_CIRCUIT));
  els.source.add(new Option("Fly a circuit: take off, left-hand pattern, land on 09 (crosswind, gusts)", MANUAL_CIRCUIT_XW));
  els.source.add(new Option("Watch the PID autopilot", LIVE));
  els.source.add(new Option("Watch the LQR autopilot", LIVE_LQR));
  els.source.add(new Option("Watch the approach autopilot land on runway 09", LIVE_APPROACH));
  els.source.add(new Option("Watch the takeoff autopilot (wind varies by seed)", LIVE_TAKEOFF));
  els.source.add(new Option("Watch the circuit autopilot (wind varies by seed)", LIVE_CIRCUIT));
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
  requestPreview();
}

function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.addEventListener("open", () => {
    lastPreviewKey = null; // preview again after a reconnect
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
    if (pilot.requests.delete("view_center")) scene.resetView();
    if (pilot.requests.delete("hud_toggle")) setHud(!hudOn);
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

// Set the scene up for a flight (its hello message): targets, runway, pattern, sky,
// readouts and the task instructions. Used for playback and for previews.
function applyHello(msg) {
  session = msg;
  latest = null;
  frames.reset();
  pendingSeek = null;
  updateSeekable();
  scene.reset();
  scene.setTargets(msg.targets);
  scene.setApproach(msg.approach ?? null);
  scene.setPattern(msg.pattern ?? null);
  applySky();
  if (msg.takeoff) scene.windsock.setWind(msg.takeoff.wind?.from_deg ?? 0, (msg.takeoff.wind?.u20_mps ?? 0) * 1.943844);
  panel.setSession(msg);
  const pilotName = { pid: "PID", lqr: "LQR", approach: "Approach", takeoff: "Takeoff", circuit: "Circuit" }[msg.pilot ?? "pid"] ?? msg.pilot;
  panel.setRunText(`${msg.source === "live" ? `${pilotName} autopilot` : { manual: "You are flying", replay: "Replay" }[msg.source]} ${msg.run_id}`);
  say(msg.source === "manual"
    ? msg.approach?.task === "circuit"
      ? "Take off, climb straight ahead past the runway end, turn left at 700 ft, fly downwind at 1000 ft about 1 nm north, descend from abeam the threshold, turn base at 45 degrees and land on 09." + (msg.approach.wind ? " Crosswind and gusts." : "")
      : msg.approach
      ? "Follow the glide path to runway 09 (ahead), flare and land main wheels first." + (msg.approach.wind ? " Crosswind: crab on the approach, then line up with rudder and hold a wing low into the wind." : "")
      : msg.takeoff
        ? "Full throttle (W), keep the centreline with Z/X, lift the nose wheel at 55 kt and climb at 75 kt to 1000 ft." + (msg.takeoff.wind ? " Crosswind: aileron into the wind on the roll; after lift-off let the nose turn into the wind." : "")
        : "Fly to the magenta altitude and heading bugs."
    : "");
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
      applyHello(msg);
      syncPanel();
      setPlaying(true);
      break;
    case "preview":
      // The selected flight's starting position, before Play (ignored once a flight runs
      // or when a newer selection was made).
      if (msg.id !== previewId || !els.stop.disabled) break;
      applyHello(msg.hello);
      if (msg.hello.source === "replay") lastReplay = els.source.value; // seeking on the bar starts it there
      panel.setRunText(`Starting position of ${msg.hello.run_id}`);
      say(`${els.message.textContent} Press Play to start.`.trim());
      latest = msg.row;
      frames.reset();
      frames.push(latest);
      dirty = true;
      syncPanel();
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
      frameStats.message(performance.now());
      frames.push(latest); // drawn smoothly by frame()
      tellPanel({ type: "frame", row: latest });
      if (!paused) sound.update(latest, { view: scene.view, distanceM: scene.orbit.distance });
      dirty = true;
      break;
    case "end":
      setPlaying(false);
      sound.silence();
      if (msg.reason === "landed") {
        const td = msg.landing.touchdown;
        const zone = td.in_zone ? "in the touchdown zone" : `${Math.round(td.along_m)} m past the threshold (zone 100-400 m)`;
        say(`Landed ${zone}, ${Math.round(td.sink_mps * 196.85)} ft/min, ${Math.round(td.cas_mps * 1.94384)} KCAS, ` +
          `${Math.abs(td.cross_m).toFixed(1)} m ${td.cross_m >= 0 ? "right" : "left"} of the centreline${msg.landing.bounces ? `, ${msg.landing.bounces} bounce(s)` : ""}` +
          (msg.landing.rollout ? `; stopped after a ${Math.round(msg.landing.rollout.ground_roll_m)} m ground roll, ${Math.round(msg.landing.rollout.stop_along_m)} m down the runway` : "") +
          ". Press Play to go again.");
      } else if (msg.reason === "climbed") {
        const lo = msg.takeoff.liftoff, ff = msg.takeoff.fifty_ft;
        say(`Climbed to 1000 ft. Lift-off at ${Math.round(lo.cas_mps * 1.94384)} KCAS after a ${Math.round(lo.ground_roll_m)} m ground roll` +
          (ff ? `, 50 ft after ${Math.round(ff.distance_m)} m` : "") + ". Press Play to go again.");
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

// The play request for the selected flight (also sent as a preview).
function flightRequest() {
  const speed = Number(els.speed.value);
  const seed = Number(els.seed.value) || 0;
  const v = els.source.value;
  if (isManual(v)) return { source: "manual", conditions: MANUAL_CONDITIONS[v], seed, record: els.record.checked };
  if (isLive(v)) return { source: "live", autopilot: LIVE_AUTOPILOT[v], seed, speed };
  return v ? { source: "replay", path: v, speed } : null;
}

function play() {
  sound.unlock(); // a click: browsers allow audio from here on
  const req = flightRequest();
  if (!req) return;
  previewId++; // a preview still on its way is stale now
  if (req.source === "replay") lastReplay = req.path;
  if (req.source === "manual") {
    req.aids = { hud: hudInView() }; // recorded with the demonstration
    hudReported = hudInView();
  }
  send({ type: "play", ...req });
  document.activeElement?.blur(); // so the arrow keys fly instead of changing the menu
}

// Preview: show where the selected flight starts (seed included) without starting it.
let previewId = 0;
let lastPreviewKey = null;
function requestPreview() {
  if (!els.stop.disabled) return; // a flight is running
  const req = flightRequest();
  if (!req) return;
  const key = JSON.stringify([req.source, req.conditions, req.autopilot, req.path, req.source === "replay" ? null : req.seed]);
  if (key === lastPreviewKey) return;
  lastPreviewKey = key;
  send({ type: "preview", id: ++previewId, ...req });
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
els.seed.addEventListener("change", requestPreview);
sourceFilter.addEventListener("input", () => populateSources());
els.source.addEventListener("focus", () => send({ type: "list" }));
// HUD in the cockpit view (hud.js): H, the button or a controller button; remembered in
// this browser. During a manual flight the viewer reports when it comes into or leaves
// view, so the demonstration records it (protocol "aids"; never reaches the physics).
let hudOn = (() => {
  try {
    return localStorage.getItem("flightsim.hud") === "on";
  } catch {
    return false;
  }
})();
let hudReported = null;
const hudInView = () => hudOn && scene.view === "cockpit";
function reportHud() {
  const v = hudInView();
  if (v === hudReported) return;
  hudReported = v;
  if (flying()) send({ type: "aids", hud: v });
}
function setHud(on) {
  hudOn = on;
  try {
    localStorage.setItem("flightsim.hud", on ? "on" : "off");
  } catch {
    // not remembered; the HUD still switches
  }
  $("hud-toggle").textContent = on ? "HUD on" : "HUD off";
  $("hud-toggle").setAttribute("aria-pressed", String(on));
  reportHud();
}
$("hud-toggle").addEventListener("click", () => setHud(!hudOn));
setHud(hudOn);

// Chase or cockpit view (C key or the button); remembered in this browser.
function setView(view) {
  scene.setView(view);
  const inside = view === "cockpit";
  $("view-toggle").textContent = inside ? "Chase view" : "Cockpit view";
  $("view-toggle").setAttribute("aria-pressed", String(inside));
  $("hud-toggle").hidden = !inside;
  reportHud();
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

// Keys, typed here or in the instruments window (forwarded; never "in a form" there).
function keyDown(e, inForm) {
  if (e.code === "KeyM" && !inForm && !e.repeat) {
    toggleSound();
    return;
  }
  if (e.code === "KeyR" && !inForm && !e.repeat) {
    scene.resetView();
    return;
  }
  if (e.code === "KeyP" && !inForm && !e.repeat) {
    setPerf($("perf").hidden);
    return;
  }
  if (e.code === "KeyH" && !inForm && !e.repeat) {
    setHud(!hudOn);
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
}
document.addEventListener("keydown", (e) => keyDown(e, ["INPUT", "SELECT", "BUTTON"].includes(document.activeElement?.tagName)));
document.addEventListener("keyup", (e) => pilot.keyup(e));
window.addEventListener("blur", () => pilot.releaseAll());
window.addEventListener("resize", () => (dirty = true));

// Instruments window (panel.html, e.g. on a second monitor): mirrors this window's flight
// over a BroadcastChannel (protocol in panel.js). While one is open, this window hides its
// own panel so the 3D view gets the room.
const panelChannel = "BroadcastChannel" in window ? new BroadcastChannel(PANEL_CHANNEL) : null;
let panelSeenAt = -Infinity;
const tellPanel = (msg) => panelChannel?.postMessage(msg);
const syncPanel = () => tellPanel({ type: "state", hello: session, row: latest, run: panel.run.textContent });
function placePanel() {
  const away = performance.now() - panelSeenAt < PANEL_TIMEOUT_MS;
  if ($("panel").hidden !== away) {
    $("panel").hidden = away;
    dirty = true;
  }
  $("panel-window").textContent = away ? "Instruments: own window" : "Instruments window";
}
panelChannel?.addEventListener("message", (e) => {
  const m = e.data;
  if (m.type === "alive") panelSeenAt = performance.now();
  else if (m.type === "closed") panelSeenAt = -Infinity;
  else if (m.type === "sync") syncPanel();
  else if (m.type === "key") {
    const ev = { code: m.code, key: m.key, shiftKey: m.shiftKey, repeat: m.repeat, preventDefault() {} };
    if (m.event === "down") keyDown(ev, false);
    else pilot.keyup(ev);
  }
  placePanel();
});
setInterval(() => {
  tellPanel({ type: "viewer" });
  placePanel();
}, 1000);
$("panel-window").addEventListener("click", () => {
  window.open("panel.html", "flightsim-instruments", "popup=yes,width=1280,height=560");
});

// Performance readout (P), remembered in this browser: drawn frames per second, the
// slowest frame and refreshes missed over the last 10 s, the server's frame messages, and
// the GPU work and terrain tiles still to build.
const frameStats = new FrameStats(10000);
function setPerf(on) {
  $("perf").hidden = !on;
  $("bench").hidden = !on;
  try {
    localStorage.setItem("flightsim.perf", on ? "on" : "off");
  } catch {
    // not remembered
  }
}
try {
  setPerf(localStorage.getItem("flightsim.perf") === "on");
} catch {
  // storage unavailable: off
}
let perfShownAt = 0;
function showPerf(now) {
  if ($("perf").hidden || now - perfShownAt < 500) return;
  perfShownAt = now;
  const s = frameStats.summary(), info = scene.renderer.info.render;
  $("perf").textContent =
    `${s.fps.toFixed(0)} fps (refresh ${s.refreshMs.toFixed(1)} ms), slowest ${s.worstMs.toFixed(0)} ms\n` +
    `missed refreshes, last 10 s: ${s.dropped}\n` +
    (s.msgMedianMs ? `flight data every ${s.msgMedianMs.toFixed(0)} ms (95% < ${s.msgP95Ms.toFixed(0)}, max ${s.msgMaxMs.toFixed(0)})\n` : "") +
    `${info.calls} draw calls, ${(info.triangles / 1000).toFixed(0)}k triangles, tiles to build ${scene.terrain.pending}`;
}

// Performance test (bench.js), from the readout: on the approach start, not during a flight.
$("bench").addEventListener("click", async () => {
  if (!els.stop.disabled) {
    say("Stop the flight first, then run the performance test.");
    return;
  }
  const buttons = [$("bench"), els.play];
  buttons.forEach((b) => (b.disabled = true));
  try {
    els.source.value = MANUAL_APPROACH; // a fixed view: the approach start, seed 0
    els.seed.value = "0";
    updateSourceOptions();
    await new Promise((r) => setTimeout(r, 2000)); // the preview arrives and the tiles build
    scriptTimes.length = 0;
    benchActive = true;
    const result = await runBench({
      scene, setView, restoreClouds: applySky, progress: say, scriptTimes,
      setHud: (on) => (benchHud = on), setPanel: (on) => { benchPanel = on; dirty = true; },
    });
    const extra = `HUD ${hudOn ? "on" : "off"} (cockpit view); instruments window ${$("panel").hidden ? "open" : "closed"}`;
    $("bench-out").textContent = benchReport(result) + "\n" + extra;
    say("Performance test finished.");
    $("bench-dialog").showModal();
  } catch (e) {
    say(`The performance test failed: ${e.message}`);
  } finally {
    benchActive = false;
    benchHud = benchPanel = true;
    buttons.forEach((b) => (b.disabled = false));
  }
});
$("bench-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("bench-out").textContent);
    $("bench-copy").textContent = "Copied";
  } catch {
    getSelection().selectAllChildren($("bench-out")); // select it for copying by hand
  }
});

const hudCanvas = $("hud");
function drawHudLayer() {
  const dpr = window.devicePixelRatio || 1, w = hudCanvas.clientWidth, h = hudCanvas.clientHeight;
  if (hudCanvas.width !== Math.round(w * dpr) || hudCanvas.height !== Math.round(h * dpr)) {
    hudCanvas.width = Math.round(w * dpr);
    hudCanvas.height = Math.round(h * dpr);
  }
  const ctx = hudCanvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!hudInView() || !shown || !benchHud) return;
  drawHud(ctx, w, h, { camera: scene.camera, aircraftMatrix: scene.aircraft.matrix, row: shown, ...hudTask(shown) });
}

// What the HUD shows for the task: free flight its altitude and heading targets; runway
// tasks the runway, and on approach (an approach, or a circuit heading for the runway
// after climbing out) the aim point, glide path reference and approach speed; takeoffs
// (and circuits before climbing out) the rotate and climb speeds.
// Approach speed: target plus half the reported gust factor, at most 10 kt (FAA AFH
// ch. 9, docs/REFERENCES.md; as configs/approach_autopilot.yaml).
function hudTask(row) {
  const a = session?.approach, tk = session?.takeoff;
  if (!a && !tk) return { targets: session?.targets };
  const takeoffBugs = [{ kt: ROTATE_KT, label: "R" }, { kt: CLIMB_KT, label: "C" }];
  if (tk) return { runway: tk, speedBugs: takeoffBugs };
  const towardRunway = Math.cos(row.psi_rad - (a.heading_deg * Math.PI) / 180) > 0.8;
  const onApproach = a.task !== "circuit" || (panel.climbedOut && towardRunway);
  const gustKt = (a.wind?.gust_factor_mps ?? 0) * 1.943844;
  const vapp = a.target_kias != null ? [{ kt: a.target_kias + Math.min(10, 0.5 * gustKt), label: "A" }] : [];
  if (a.task === "circuit" && !panel.climbedOut) return { runway: a, speedBugs: takeoffBugs };
  return { runway: a, approach: onApproach, speedBugs: vapp };
}

// The flight as drawn: between the server's frames, a little behind the newest (smooth.js).
const frames = new FrameBuffer();
let shown = null;
let lastFrameAt = null;
const playbackSpeed = () => (session?.source === "manual" ? 1 : Number(els.speed.value) || 1);

let benchHud = true, benchPanel = true; // the performance test can switch these off
let benchActive = false, benchDirtyAt = 0; // during the test: redraw the panel 30 times a second, as in flight
const scriptTimes = []; // per-frame script time (ms), collected during the performance test

function frame(now = performance.now()) {
  const t0 = performance.now();
  if (benchActive && now - benchDirtyAt >= 1000 / 30) {
    benchDirtyAt = now;
    dirty = true;
  }
  frameStats.frame(now);
  showPerf(now);
  const dt = lastFrameAt === null ? 0 : Math.min(0.25, (now - lastFrameAt) / 1000);
  lastFrameAt = now;
  const row = frames.sample(dt, playbackSpeed(), paused);
  if (row && row !== shown) {
    shown = row;
    scene.update(shown);
  }
  if (dirty) {
    if (benchPanel) panel.draw(latest);
    if (latest && session?.duration_s && !dragging) showPosition(latest.t_s);
    dirty = false;
  }
  scene.render();
  drawHudLayer();
  const bx = scene.boresightX();
  const marker = $("boresight");
  marker.style.display = bx === null ? "none" : "block";
  if (bx !== null) marker.style.left = `${(bx * 100).toFixed(2)}%`;
  if (scriptTimes.length < 5000) scriptTimes.push(performance.now() - t0);
  requestAnimationFrame(frame);
}

// Stick settings dialog: edits pilot.stick in place, applies immediately, saves per browser.
// The feel (sensitivity, expo, dead zone) is the connected device's own.
const AXIS_LABELS = { pitch: "Pitch", roll: "Roll", rudder: "Rudder" };
const padName = (pad) => pad.id.replace(/\s*\(.*$/, "");
function renderStickRows() {
  const rows = $("stick-rows");
  rows.replaceChildren();
  const pad = pilot.readPad();
  const feel = pilot.feel();
  $("stick-feel-device").textContent = pad ? `Feel for ${padName(pad)}: each controller keeps its own.` : "No gamepad connected: these are the starting values for new controllers.";
  for (const axis of AXES) {
    const row = document.createElement("div");
    row.className = "stick-row";
    row.innerHTML = `<span class="axis">${AXIS_LABELS[axis]}</span>`;
    for (const [key, label, min] of [["sensitivity", "Sensitivity", 0.1], ["expo", "Expo", 0]]) {
      const id = `stick-${axis}-${key}`;
      const lab = document.createElement("label");
      lab.htmlFor = id;
      const input = Object.assign(document.createElement("input"), { type: "range", id, min, max: 1, step: 0.05 });
      input.value = feel[axis][key];
      input.setAttribute("aria-label", `${AXIS_LABELS[axis]} ${label.toLowerCase()}`);
      const out = document.createElement("output");
      out.htmlFor = id;
      out.textContent = Number(input.value).toFixed(2);
      input.addEventListener("input", () => {
        feel[axis][key] = Number(input.value);
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
  input.value = pilot.feel().deadzone;
  $("stick-deadzone-out").textContent = Number(input.value).toFixed(2);
}
$("stick-deadzone").addEventListener("input", (e) => {
  const feel = pilot.feel();
  feel.deadzone = Number(e.target.value);
  $("stick-deadzone-out").textContent = feel.deadzone.toFixed(2);
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
    ? `${padName(pad)}${pad.mapping === "standard" ? "" : " (no standard layout: map its axes below)"}`
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
  renderButtonRows();
}

// Controller buttons: which button (or hat direction) does each button function.
const BUTTON_LABELS = {
  flaps_up: "Flaps up", flaps_down: "Flaps down", trim_nose_down: "Trim nose down", trim_nose_up: "Trim nose up",
  brake: "Brakes", throttle_up: "Throttle up", throttle_down: "Throttle down", view_center: "Centre view", hud_toggle: "HUD on/off",
};
const bindingKey = (b) => (!b ? "" : b.button !== undefined ? `b${b.button}` : `a${b.axis}${b.dir > 0 ? "+" : "-"}`);
const bindingFromKey = (k) => (!k ? null : k[0] === "b" ? { button: Number(k.slice(1)) } : { axis: Number(k.slice(1, -1)), dir: k.endsWith("+") ? 1 : -1 });

function renderButtonRows() {
  const rows = $("button-rows");
  rows.replaceChildren();
  const pad = pilot.pad;
  if (!pad) return;
  const profile = pilot.profile();
  for (const f of BUTTONS) {
    const row = document.createElement("div");
    row.className = "axis-row";
    const label = Object.assign(document.createElement("span"), { className: "axis", textContent: BUTTON_LABELS[f] });
    const select = document.createElement("select");
    select.setAttribute("aria-label", `${BUTTON_LABELS[f]} button`);
    select.add(new Option("None (keyboard)", ""));
    for (let i = 0; i < pad.buttons.length; i++) select.add(new Option(`Button ${i}`, `b${i}`));
    for (let i = 0; i < pad.axes.length; i++) for (const d of ["+", "-"]) select.add(new Option(`Axis ${i} ${d === "+" ? "+" : "\u2212"}`, `a${i}${d}`));
    const key = bindingKey(profile.buttons[f]);
    if (key && ![...select.options].some((o) => o.value === key)) select.add(new Option(`${key} (not on this device)`, key));
    select.value = key;
    select.addEventListener("change", () => {
      profile.buttons[f] = bindingFromKey(select.value);
      saveSettings(pilot.stick);
    });
    const detect = Object.assign(document.createElement("button"), { type: "button", textContent: "Detect" });
    detect.addEventListener("click", () => detectButtonFor(f, row, detect));
    const live = Object.assign(document.createElement("span"), { className: "live" });
    live.dataset.button = f;
    row.append(label, select, document.createElement("span"), detect, live);
    rows.append(row);
  }
}

// Detect: the pilot presses the button (or hat direction) within 3 s.
function detectButtonFor(f, row, button) {
  const pad = pilot.readPad();
  if (!pad) return;
  const snapshot = (p) => ({ buttons: p.buttons.map((b) => (typeof b.value === "number" ? b.value : b.pressed ? 1 : 0)), axes: [...p.axes] });
  const baseline = snapshot(pad);
  const profile = pilot.profile();
  const exclude = CONTROLS.map((c) => profile.map[c].axis).filter((a) => a !== null);
  const prompt = Object.assign(document.createElement("span"), { className: "prompt", textContent: `Press the button for ${BUTTON_LABELS[f].toLowerCase()}…`, role: "status" });
  row.append(prompt);
  button.disabled = true;
  const samples = [];
  const timer = setInterval(() => {
    const p = pilot.readPad();
    if (p) samples.push(snapshot(p));
    const found = detectButton(baseline, samples, exclude);
    if (found || samples.length >= 60) { // 3 s
      clearInterval(timer);
      if (found) {
        pilot.profile().buttons[f] = found;
        saveSettings(pilot.stick);
        renderButtonRows();
      } else {
        prompt.textContent = "No button press detected: try again.";
        button.disabled = false;
      }
    }
  }, 50);
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
    const off = span.dataset.control !== "throttle" && v !== null && Math.abs(v) >= pilot.feel().deadzone;
    drift ||= off;
    span.textContent = v === null ? "" : span.dataset.control === "throttle" ? `${Math.round(v * 100)}%` : signed(v, 2);
    span.style.color = off ? "#e2b93b" : "";
  }
  for (const span of document.querySelectorAll("#button-rows .live")) {
    span.textContent = buttonValue(pad, profile.buttons[span.dataset.button]) > 0.5 ? "pressed" : "";
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
  // Reset the feel of the connected controller (or, with none, the template for new ones);
  // axes, buttons and calibration stay.
  const pad = pilot.readPad();
  if (pad) pilot.profile().feel = copyFeel(DEFAULTS);
  else pilot.stick = { ...structuredClone(DEFAULTS), devices: pilot.stick.devices };
  saveSettings(pilot.stick);
  renderStickRows();
  renderDeadzone();
});

populateSources([]);
if (document.fonts) document.fonts.ready.then(() => (dirty = true));
connect();
requestAnimationFrame(frame);

// --- Sky: the flight's conditions (hello "visual"), each overridable in this browser ----

const SKY_KEY = "flightsim.sky";
const SKY_DEFAULTS = { time_of_day: "afternoon", visibility: "normal", clouds: "few" };
const SKY_FIELDS = { time_of_day: "sky-time", visibility: "sky-visibility", clouds: "sky-clouds" };
let skyOverride = (() => {
  try {
    return JSON.parse(localStorage.getItem(SKY_KEY)) ?? {};
  } catch {
    return {};
  }
})();

function applySky() {
  const flight = { ...SKY_DEFAULTS, cloud_seed: 0, ...(session?.visual ?? {}) };
  const effective = { ...flight };
  for (const [key, id] of Object.entries(SKY_FIELDS)) {
    if (skyOverride[key] && skyOverride[key] !== "flight") effective[key] = skyOverride[key];
    $(id).value = skyOverride[key] ?? "flight";
    $(`${id}-flight`).textContent = `flight: ${flight[key]}`;
  }
  scene.setVisual(effective);
  dirty = true;
}

for (const [key, id] of Object.entries(SKY_FIELDS)) {
  $(id).addEventListener("change", (e) => {
    skyOverride[key] = e.target.value;
    try {
      localStorage.setItem(SKY_KEY, JSON.stringify(skyOverride));
    } catch {
      // storage unavailable: the choice lasts for this page only
    }
    applySky();
  });
}
$("sky-reset").addEventListener("click", () => {
  skyOverride = {};
  try {
    localStorage.removeItem(SKY_KEY);
  } catch {
    // storage unavailable
  }
  applySky();
});
$("sky-open").addEventListener("click", () => $("sky-dialog").showModal());
applySky();
