// A moving map on a canvas, fed from the instruments channel (panel.js protocol: "state",
// "frame", "camera"): the map window's and the combo panel's. Range and north up are
// remembered in this browser under `key`.

import { Geodesy } from "./geo.js";
import { RANGES_NM, Track, drawMap } from "./map.js";
import { MapBackground } from "./mapTiles.js";
import { NavTracker } from "./nav.js";

export class MapPane {
  constructor(canvas, key, defaults = {}) {
    this.canvas = canvas;
    this.key = key;
    this.settings = { rangeIndex: 2, northUp: true, ...defaults };
    try {
      this.settings = { ...this.settings, ...JSON.parse(localStorage.getItem(key) ?? "{}") };
    } catch {
      // defaults
    }
    this.settings.rangeIndex = Math.max(0, Math.min(RANGES_NM.length - 1, this.settings.rangeIndex | 0));
    this.hello = null;
    this.row = null;
    this.geodesy = new Geodesy(null);
    this.navTracker = null;
    this.nav = null; // a route's navigation (nav.js), fed every frame
    this.camera = null; // what the belly camera sees (app.js camShape), drawn on the map
    this.traffic = []; // the region's moving traffic now (traffic.js positions)
    this.track = new Track();
    this.dirty = true;
    try {
      this.background = new MapBackground(() => (this.dirty = true));
    } catch {
      this.background = null; // no module workers: the plain map
    }
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.zoom(e.deltaY > 0 ? 1 : -1);
    }, { passive: false });
  }

  save() {
    try {
      localStorage.setItem(this.key, JSON.stringify(this.settings));
    } catch {
      // not remembered
    }
  }

  // A message on the instruments channel.
  message(m) {
    if (m.type === "state") {
      if (m.hello?.run_id !== this.hello?.run_id || m.hello?.source !== this.hello?.source) this.track.clear();
      this.hello = m.hello;
      this.geodesy = new Geodesy(this.hello?.world ?? null);
      this.navTracker = this.hello?.route ? new NavTracker(this.hello.route, this.geodesy) : null;
      this.nav = null;
      this.row = m.row;
      if (this.row) this._addToTrack(this.row);
      this.dirty = true;
    } else if (m.type === "camera") {
      this.camera = m.camera;
      this.dirty = true;
    } else if (m.type === "frame") {
      this.row = m.row;
      this.traffic = m.traffic ?? [];
      this._addToTrack(this.row);
      this.dirty = true;
    }
  }

  _addToTrack(r) {
    const [n, e] = this.geodesy.toMap(r.lat_rad, r.lon_rad);
    this.track.add(r.t_s, n, e);
    this.nav = this.navTracker ? this.navTracker.update(r) : null;
  }

  // dir -1: closer in (a shorter range), +1: further out.
  zoom(dir) {
    this.settings.rangeIndex = Math.max(0, Math.min(RANGES_NM.length - 1, this.settings.rangeIndex + dir));
    this.save();
    this.dirty = true;
  }

  toggleNorthUp() {
    this.settings.northUp = !this.settings.northUp;
    this.save();
    this.dirty = true;
  }

  // Draws when something changed (call every animation frame).
  draw() {
    if (!this.dirty) return;
    this.dirty = false;
    const c = this.canvas, dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
    }
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (w && h) {
      const { hello, row, geodesy, track, nav, camera, background, traffic } = this;
      drawMap(ctx, w, h, { hello, row, geodesy, track, rangeNm: RANGES_NM[this.settings.rangeIndex], northUp: this.settings.northUp, background, nav, camera, traffic, credit: true });
    }
  }
}
