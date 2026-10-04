// Viewer: consumes the state stream (protocol 1) and draws it. It only ever sends
// playback requests; nothing here can influence the physics.

import { FlightScene } from "./scene.js";
import { drawAll, units } from "./gauges.js";

const $ = (id) => document.getElementById(id);
const els = {
  source: $("source"), seed: $("seed"), seedLabel: $("seed-label"), play: $("play"), pause: $("pause"),
  stop: $("stop"), speed: $("speed"), fill: $("progress-fill"), clock: $("clock"), message: $("message"),
  run: $("run"),
};
const gauges = { asi: $("asi"), ai: $("ai"), alt: $("alt"), tc: $("tc"), hi: $("hi"), vsi: $("vsi"), tach: $("tach"), controls: $("controls") };
const readout = { alt: $("r-alt"), talt: $("r-talt"), hdg: $("r-hdg"), thdg: $("r-thdg"), kias: $("r-kias"), aoa: $("r-aoa"), g: $("r-g") };

const scene = new FlightScene($("view"));
let ws = null;
let session = null; // hello message of the current playback
let latest = null; // latest frame row
let paused = false;
let dirty = true;

const LIVE = "live";
// Optional URL parameters: ?source=live|<log path>&seed=3&speed=5&autoplay=1
const params = new URLSearchParams(location.search);
let autoplay = params.get("autoplay") === "1";
if (params.has("seed")) els.seed.value = params.get("seed");
if (params.has("speed")) els.speed.value = params.get("speed");
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const deg360 = (rad) => ((rad * units.DEG) % 360 + 360) % 360;

function say(text) {
  els.message.textContent = text;
}

function setPlaying(playing) {
  els.pause.disabled = !playing;
  els.stop.disabled = !playing;
  els.pause.textContent = "Pause";
  paused = false;
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function populateSources(logs) {
  const current = els.source.value;
  els.source.replaceChildren();
  const live = new Option("Live: PID autopilot flight", LIVE);
  els.source.add(live);
  if (logs.length) {
    const group = document.createElement("optgroup");
    group.label = "Recorded flights";
    for (const log of logs) group.append(new Option(`${log.path} (${(log.rows / 120 / 60).toFixed(1)} min)`, log.path));
    els.source.add(group);
  }
  if ([...els.source.options].some((o) => o.value === current)) els.source.value = current;
  updateSeedVisibility();
}

function updateSeedVisibility() {
  const live = els.source.value === LIVE;
  els.seed.hidden = els.seedLabel.hidden = !live;
}

function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.addEventListener("open", () => {
    say("Pick a recorded flight, or fly a live autopilot episode, then press Play.");
    send({ type: "list" });
  });
  ws.addEventListener("close", () => {
    setPlaying(false);
    say("Lost the connection to the stream server. Retrying…");
    setTimeout(connect, 2000);
  });
  ws.addEventListener("message", (ev) => handle(JSON.parse(ev.data)));
}

function handle(msg) {
  switch (msg.type) {
    case "logs":
      populateSources(msg.logs);
      if (params.has("source")) els.source.value = params.get("source");
      updateSeedVisibility();
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
      els.run.textContent = `${msg.source === "live" ? "Live" : "Replay"} ${msg.run_id}`;
      say("");
      setPlaying(true);
      break;
    case "frame":
      latest = msg.row;
      scene.update(latest);
      dirty = true;
      break;
    case "end":
      setPlaying(false);
      if (msg.reason === "finished") say("Flight finished. Press Play to watch it again.");
      else if (msg.reason.startsWith("terminated:")) say(`The episode ended early: ${msg.reason.slice(11).replace("_", " ")} limit exceeded.`);
      break;
    case "error":
      say(msg.message);
      break;
  }
}

function play() {
  const speed = Number(els.speed.value);
  if (els.source.value === LIVE) send({ type: "play", source: "live", seed: Number(els.seed.value) || 0, speed });
  else send({ type: "play", source: "replay", path: els.source.value, speed });
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
els.source.addEventListener("change", updateSeedVisibility);
els.source.addEventListener("focus", () => send({ type: "list" }));
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !["INPUT", "SELECT", "BUTTON"].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    togglePause();
  }
});
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

populateSources([]);
if (document.fonts) document.fonts.ready.then(() => (dirty = true));
connect();
requestAnimationFrame(frame);
