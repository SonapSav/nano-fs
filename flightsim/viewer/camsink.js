// A place that shows the belly camera's picture: the inset in the 3D view, the camera
// window (camera.html) or the map window's camera layout. The flight viewer renders the
// picture and draws it into every attached canvas (app.js `flightsimCamera`); other windows
// reach it through window.opener, so they must be opened from the viewer. After the viewer
// reloads they attach again by themselves.
//
// Here: drag to point the camera, the wheel to zoom, double-click to re-centre (straight
// down); keys are handled by the window (cameraKey).

import { ASPECT } from "./camera.js";

// Numpad: 4 / 6 pan, 8 / 2 tilt, + / - zoom (held), 5 re-centre, 0 follow the recording (replays).
export const CAMERA_KEYS = new Set(["Numpad4", "Numpad6", "Numpad8", "Numpad2", "NumpadAdd", "NumpadSubtract", "Numpad5", "Numpad0"]);

const viewerApi = (win) => {
  try {
    return win?.flightsimCamera ?? null;
  } catch {
    return null; // another origin
  }
};

export class CameraSink {
  // `host`: the window whose `flightsimCamera` renders the picture (this window for the
  // inset, window.opener elsewhere); `onStatus(text)`: "" when attached, else why not.
  constructor(canvas, { host = window.opener, compact = false, onStatus = () => {} } = {}) {
    this.canvas = canvas;
    this.host = host;
    this.compact = compact;
    this.onStatus = onStatus;
    this.api = null;
    this.id = null;
    this.enabled = false;
    this._bindPointer();
    this._timer = setInterval(() => this._check(), 1000);
    window.addEventListener("pagehide", () => this.disable());
  }

  enable() {
    this.enabled = true;
    this._check();
  }

  disable() {
    this.enabled = false;
    if (this.api && this.id !== null) {
      try {
        this.api.detach(this.id);
      } catch {
        // the viewer is gone
      }
    }
    this.api = null;
    this.id = null;
  }

  // Attach (again) when the viewer's camera API is there and changed (a reload).
  _check() {
    if (!this.enabled) return;
    const api = viewerApi(this.host);
    if (!api) {
      this.api = null;
      this.id = null;
      this.onStatus(this.host ? "Waiting for the flight viewer window…" : "Open this window from the flight viewer (Camera window button) to see the camera.");
      return;
    }
    if (api !== this.api) {
      this.api = api;
      this.id = api.attach(this.canvas, window, { compact: this.compact });
    }
    this.onStatus("");
  }

  // The picture's width on screen (it is fitted into the canvas at 16:9).
  pictureWidth() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    return Math.min(w, h * ASPECT);
  }

  // Window keys: held camera keys go to the viewer; true if the key was a camera key.
  key(code, down) {
    if (!CAMERA_KEYS.has(code)) return false;
    if (this.api) this.api.key(code, down);
    return true;
  }

  _bindPointer() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener("pointerdown", (e) => {
      drag = { x: e.clientX, y: e.clientY };
      c.setPointerCapture(e.pointerId);
      e.stopPropagation();
    });
    c.addEventListener("pointermove", (e) => {
      if (!drag || !this.api) return;
      this.api.drag(e.clientX - drag.x, e.clientY - drag.y, this.pictureWidth());
      drag = { x: e.clientX, y: e.clientY };
    });
    c.addEventListener("pointerup", () => (drag = null));
    c.addEventListener("pointercancel", () => (drag = null));
    c.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      this.api?.recentre();
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.api?.zoom(e.deltaY < 0 ? 1.25 : 0.8);
    }, { passive: false });
  }
}
