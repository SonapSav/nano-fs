// The map window (map.html): a moving map of the flight, e.g. full screen on another
// monitor. It listens to the flight viewer window on the instruments channel (panel.js
// protocol: "state", "frame"), asks it for the current state ("sync"), and forwards keys
// it does not use itself, so flying with the keyboard keeps working. It never says
// "alive": the viewer keeps its own instrument panel while only the map is open.
//
// Keys here: + / - zoom, N north up / track up; the mouse wheel zooms; K the layout: the
// map, the map and the belly camera, the camera alone (camsink.js; it needs this window to
// be opened from the viewer). Remembered in this browser.

import { PANEL_CHANNEL, PANEL_TIMEOUT_MS } from "./panel.js";
import { MapPane } from "./mapPane.js";
import { CameraSink } from "./camsink.js";

const canvas = document.getElementById("map");
const status = document.getElementById("status");
const channel = new BroadcastChannel(PANEL_CHANNEL);
const LAYOUTS = ["map", "split", "camera"];
const map = new MapPane(canvas, "flightsim.map", { layout: "map" });
const settings = map.settings; // range, north up and the layout
if (!LAYOUTS.includes(settings.layout)) settings.layout = "map";
const save = () => map.save();
let heardAt = 0;

channel.addEventListener("message", (e) => {
  heardAt = performance.now();
  if (e.data.type === "close-windows") window.close(); // the combo panel opened (combowindow.js)
  else map.message(e.data);
});
channel.postMessage({ type: "sync" });
setInterval(() => {
  status.textContent = performance.now() - heardAt < PANEL_TIMEOUT_MS ? "" : "Waiting for the flight viewer window…";
}, 1000);

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
  map.dirty = true;
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
    if (!e.repeat) map.zoom(e.key === "+" || e.key === "=" || e.code === "NumpadAdd" ? -1 : 1);
    return;
  }
  if (e.code === "KeyN") {
    if (!e.repeat) map.toggleNorthUp();
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
window.addEventListener("resize", () => (map.dirty = true));

function frame() {
  map.draw();
  requestAnimationFrame(frame);
}
frame();
