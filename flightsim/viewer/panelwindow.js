// The instruments window (panel.html): the instrument panel on its own, e.g. full screen
// on a second monitor. It mirrors the flight viewer window (protocol in panel.js) and
// sends nothing to the stream server.

import { InstrumentPanel, PANEL_CHANNEL, PANEL_TIMEOUT_MS } from "./panel.js";

const panel = new InstrumentPanel(document.getElementById("panel"));
const status = document.getElementById("status");
const channel = new BroadcastChannel(PANEL_CHANNEL);
let row = null;
let dirty = true;
let heardAt = 0;

channel.addEventListener("message", (e) => {
  const m = e.data;
  heardAt = performance.now();
  if (m.type === "state") {
    panel.setSession(m.hello);
    panel.setRunText(m.run ?? "");
    row = m.row;
    dirty = true;
  } else if (m.type === "frame") {
    row = m.row;
    dirty = true;
  }
});

const alive = () => channel.postMessage({ type: "alive" });
alive();
channel.postMessage({ type: "sync" });
setInterval(() => {
  alive();
  const connected = performance.now() - heardAt < PANEL_TIMEOUT_MS;
  status.textContent = connected ? "" : "Waiting for the flight viewer window…";
}, 1000);
window.addEventListener("pagehide", () => channel.postMessage({ type: "closed" }));
window.addEventListener("resize", () => (dirty = true));

// Keys typed here fly the aircraft as if typed in the viewer window. Browser shortcuts
// (with Ctrl, Alt or Meta, and the F keys such as F11 for full screen) stay with the browser.
for (const event of ["keydown", "keyup"]) {
  document.addEventListener(event, (e) => {
    if (e.ctrlKey || e.altKey || e.metaKey || /^F\d+$/.test(e.key)) return;
    e.preventDefault(); // no scrolling with the arrows or space
    channel.postMessage({ type: "key", event: event === "keydown" ? "down" : "up", code: e.code, key: e.key, shiftKey: e.shiftKey, repeat: e.repeat });
  });
}

function frame() {
  if (dirty) {
    panel.draw(row);
    dirty = false;
  }
  requestAnimationFrame(frame);
}
frame();
