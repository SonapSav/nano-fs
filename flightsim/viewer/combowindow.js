// The combo panel (combo.html): the belly camera top left, the moving map top right and the
// instruments across the bottom, in one window (e.g. full screen on a second monitor). It
// stands in for the instruments, map and camera windows: on opening it asks them to close
// ("close-windows"), and while it is open (its "combo" heartbeat) the viewer greys out their
// buttons. The viewer renders the camera picture (camsink.js; so this window must be opened
// from the viewer) and mirrors the instruments (panel.js protocol).
//
// The mouse picks the pane: over the camera drag points it, the wheel zooms, double-click
// re-centres; over the map the wheel changes the range. Keys follow the mouse too: + / -
// zoom the pane under it (over the instruments they go to the viewer), N toggles the map's
// north up over the map. The numpad camera keys always point the camera; other keys fly
// the aircraft as if typed in the viewer.

import { InstrumentPanel, PANEL_CHANNEL, PANEL_TIMEOUT_MS } from "./panel.js";
import { MapPane } from "./mapPane.js";
import { CameraSink } from "./camsink.js";

const channel = new BroadcastChannel(PANEL_CHANNEL);
const status = document.getElementById("status");
const panel = new InstrumentPanel(document.getElementById("panel"));
const map = new MapPane(document.getElementById("map"), "flightsim.combo.map");
const sink = new CameraSink(document.getElementById("camera"), { onStatus: (t) => (document.getElementById("camera-status").textContent = t) });
sink.enable();
let row = null, panelDirty = true, heardAt = 0;

channel.addEventListener("message", (e) => {
  const m = e.data;
  heardAt = performance.now();
  map.message(m);
  if (m.type === "state") {
    panel.setSession(m.hello);
    panel.setRunText(m.run ?? "");
    row = m.row;
    panelDirty = true;
  } else if (m.type === "frame") {
    row = m.row;
    panelDirty = true;
  }
});

// "alive": the viewer hides its own instruments (as for the instruments window); "combo":
// it greys out the window buttons.
const beat = () => {
  channel.postMessage({ type: "alive" });
  channel.postMessage({ type: "combo" });
};
channel.postMessage({ type: "close-windows" });
beat();
channel.postMessage({ type: "sync" });
setInterval(() => {
  beat();
  status.textContent = performance.now() - heardAt < PANEL_TIMEOUT_MS ? "" : "Waiting for the flight viewer window…";
}, 1000);
window.addEventListener("pagehide", () => {
  channel.postMessage({ type: "closed" });
  channel.postMessage({ type: "combo-closed" });
});

// The pane under the mouse ("camera", "map", "panel").
let pane = "panel";
document.addEventListener("pointermove", (e) => {
  pane = e.target.closest?.("[data-pane]")?.dataset.pane ?? pane;
});

const forward = (event, e) => channel.postMessage({ type: "key", event, code: e.code, key: e.key, shiftKey: e.shiftKey, repeat: event === "down" && e.repeat });
const isPlus = (e) => e.key === "+" || e.key === "=";
const isMinus = (e) => e.key === "-" || e.key === "_";
const held = new Map(); // key code -> what its key-down did ("camera:<numpad code>", "map", "viewer")

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.altKey || e.metaKey || /^F\d+$/.test(e.key)) return;
  e.preventDefault(); // no scrolling with the arrows or space
  if (sink.key(e.code, true)) return;
  if (isPlus(e) || isMinus(e)) {
    if (pane === "camera") {
      const code = isPlus(e) ? "NumpadAdd" : "NumpadSubtract";
      held.set(e.code, `camera:${code}`);
      sink.key(code, true);
    } else if (pane === "map") {
      held.set(e.code, "map");
      if (!e.repeat) map.zoom(isPlus(e) ? -1 : 1);
    } else {
      held.set(e.code, "viewer");
      forward("down", e);
    }
    return;
  }
  if (e.code === "KeyN" && pane === "map") {
    held.set(e.code, "map");
    if (!e.repeat) map.toggleNorthUp();
    return;
  }
  forward("down", e);
});
document.addEventListener("keyup", (e) => {
  if (e.ctrlKey || e.altKey || e.metaKey || /^F\d+$/.test(e.key)) return;
  if (sink.key(e.code, false)) return;
  const did = held.get(e.code);
  held.delete(e.code);
  if (did?.startsWith("camera:")) sink.key(did.slice(7), false);
  else if (did !== "map") forward("up", e);
});
window.addEventListener("blur", () => {
  for (const code of ["Numpad4", "Numpad6", "Numpad8", "Numpad2", "NumpadAdd", "NumpadSubtract"]) sink.key(code, false);
  held.clear();
});
window.addEventListener("resize", () => {
  map.dirty = true;
  panelDirty = true;
});

function frame() {
  map.draw();
  if (panelDirty) {
    panel.draw(row);
    panelDirty = false;
  }
  requestAnimationFrame(frame);
}
frame();
