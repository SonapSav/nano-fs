// The camera window (camera.html): the belly camera's picture, e.g. full screen (F11) on
// another monitor. The flight viewer renders it and draws it here (camsink.js); this
// window points it (mouse, numpad) and forwards other keys to the viewer on the
// instruments channel, so flying with the keyboard keeps working.

import { PANEL_CHANNEL } from "./panel.js";
import { CameraSink } from "./camsink.js";

const status = document.getElementById("status");
const sink = new CameraSink(document.getElementById("camera"), { onStatus: (t) => (status.textContent = t) });
sink.enable();
const channel = new BroadcastChannel(PANEL_CHANNEL);

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.altKey || e.metaKey || /^F\d+$/.test(e.key)) return;
  e.preventDefault();
  if (sink.key(e.code, true)) return;
  channel.postMessage({ type: "key", event: "down", code: e.code, key: e.key, shiftKey: e.shiftKey, repeat: e.repeat });
});
document.addEventListener("keyup", (e) => {
  if (e.ctrlKey || e.altKey || e.metaKey || /^F\d+$/.test(e.key)) return;
  if (sink.key(e.code, false)) return;
  channel.postMessage({ type: "key", event: "up", code: e.code, key: e.key, shiftKey: e.shiftKey, repeat: false });
});
window.addEventListener("blur", () => {
  for (const code of ["Numpad4", "Numpad6", "Numpad8", "Numpad2", "NumpadAdd", "NumpadSubtract"]) sink.key(code, false);
});
