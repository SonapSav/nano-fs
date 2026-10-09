// The map window (map.html): a moving map of the flight, e.g. full screen on another
// monitor. It listens to the flight viewer window on the instruments channel (panel.js
// protocol: "state", "frame"), asks it for the current state ("sync"), and forwards keys
// it does not use itself, so flying with the keyboard keeps working. It never says
// "alive": the viewer keeps its own instrument panel while only the map is open.
//
// Keys here: + / - zoom, N north up / track up; the mouse wheel zooms; K the layout: the
// map, the map and the belly camera, the camera alone (camsink.js; it needs this window to
// be opened from the viewer). Remembered in this browser.

import { Geodesy } from "./geo.js";
import { PANEL_CHANNEL, PANEL_TIMEOUT_MS } from "./panel.js";
import { RANGES_NM, Track, drawMap } from "./map.js";
import { MapBackground } from "./mapTiles.js";
import { NavTracker } from "./nav.js";
import { CameraSink } from "./camsink.js";

const canvas = document.getElementById("map");
const status = document.getElementById("status");
const channel = new BroadcastChannel(PANEL_CHANNEL);
const KEY = "flightsim.map";
const LAYOUTS = ["map", "split", "camera"];
let settings = { rangeIndex: 2, northUp: true, layout: "map" };
try {
  settings = { ...settings, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
} catch {
  // defaults
}
settings.rangeIndex = Math.max(0, Math.min(RANGES_NM.length - 1, settings.rangeIndex | 0));
if (!LAYOUTS.includes(settings.layout)) settings.layout = "map";
const save = () => {
  try {
    localStorage.setItem(KEY, JSON.stringify(settings));
  } catch {
    // not remembered
  }
};

let hello = null, row = null, geodesy = new Geodesy(null), heardAt = 0, dirty = true;
let navTracker = null, nav = null; // a route's navigation (nav.js), fed every frame
let camera = null; // what the belly camera sees (app.js camShape), drawn on the map
const track = new Track();
let background = null;
try {
  background = new MapBackground(() => (dirty = true));
} catch {
  background = null; // no module workers: the plain map
}

function addToTrack(r) {
  const [n, e] = geodesy.toMap(r.lat_rad, r.lon_rad);
  track.add(r.t_s, n, e);
  nav = navTracker ? navTracker.update(r) : null;
}

channel.addEventListener("message", (e) => {
  const m = e.data;
  heardAt = performance.now();
  if (m.type === "state") {
    if (m.hello?.run_id !== hello?.run_id || m.hello?.source !== hello?.source) track.clear();
    hello = m.hello;
    geodesy = new Geodesy(hello?.world ?? null);
    navTracker = hello?.route ? new NavTracker(hello.route, geodesy) : null;
    nav = null;
    row = m.row;
    if (row) addToTrack(row);
    dirty = true;
  } else if (m.type === "camera") {
    camera = m.camera;
    dirty = true;
  } else if (m.type === "frame") {
    row = m.row;
    addToTrack(row);
    dirty = true;
  }
});
channel.postMessage({ type: "sync" });
setInterval(() => {
  status.textContent = performance.now() - heardAt < PANEL_TIMEOUT_MS ? "" : "Waiting for the flight viewer window…";
}, 1000);

function zoom(dir) {
  settings.rangeIndex = Math.max(0, Math.min(RANGES_NM.length - 1, settings.rangeIndex + dir));
  save();
  dirty = true;
}
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  zoom(e.deltaY > 0 ? 1 : -1);
}, { passive: false });
const cameraCanvas = document.getElementById("camera");
const cameraSink = new CameraSink(cameraCanvas, { onStatus: (t) => (document.getElementById("camera-status").textContent = t) });
function applyLayout() {
  document.body.classList.toggle("split", settings.layout === "split");
  canvas.hidden = settings.layout === "camera";
  cameraCanvas.hidden = settings.layout === "map";
  if (settings.layout === "map") {
    cameraSink.disable();
    document.getElementById("camera-status").textContent = "";
  } else cameraSink.enable();
  dirty = true;
}
applyLayout();

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.altKey || e.metaKey || /^F\d+$/.test(e.key)) return;
  if (e.code === "KeyK") {
    e.preventDefault();
    if (!e.repeat) {
      settings.layout = LAYOUTS[(LAYOUTS.indexOf(settings.layout) + 1) % LAYOUTS.length];
      save();
      applyLayout();
    }
    return;
  }
  if (settings.layout !== "map" && cameraSink.key(e.code, true)) {
    e.preventDefault();
    return;
  }
  if (["+", "=", "-", "_"].includes(e.key) || e.code === "NumpadAdd" || e.code === "NumpadSubtract") {
    e.preventDefault();
    if (!e.repeat) zoom(e.key === "+" || e.key === "=" || e.code === "NumpadAdd" ? -1 : 1);
    return;
  }
  if (e.code === "KeyN") {
    if (!e.repeat) {
      settings.northUp = !settings.northUp;
      save();
      dirty = true;
    }
    return;
  }
  e.preventDefault();
  channel.postMessage({ type: "key", event: "down", code: e.code, key: e.key, shiftKey: e.shiftKey, repeat: e.repeat });
});
document.addEventListener("keyup", (e) => {
  if (e.ctrlKey || e.altKey || e.metaKey || /^F\d+$/.test(e.key) || e.code === "KeyN" || e.code === "KeyK" || ["+", "=", "-", "_"].includes(e.key)) return;
  if (cameraSink.key(e.code, false)) return;
  channel.postMessage({ type: "key", event: "up", code: e.code, key: e.key, shiftKey: e.shiftKey, repeat: false });
});
window.addEventListener("resize", () => (dirty = true));

function frame() {
  if (dirty) {
    dirty = false;
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (w && h) drawMap(ctx, w, h, { hello, row, geodesy, track, rangeNm: RANGES_NM[settings.rangeIndex], northUp: settings.northUp, background, nav, camera });
  }
  requestAnimationFrame(frame);
}
frame();
