// Terrain background for the moving map: tiles from mapTileWorker.js (cached, nearest
// first, coarser levels shown while finer ones load) plus villages and roads as vectors.
// Over a real-world region (the flight's world.scenery): the region's land cover and
// relief, its OpenStreetMap roads (minor roads only zoomed in) and all its runways.
// Drawn in world metres through the map's rotation and scale.

import { LEVELS_MPP, TILE_PX, levelFor } from "./mapTerrain.js";
import { VILLAGE_CELL_M, villageCentre } from "./terrainCore.js";
import { roadSegments } from "./roadNet.js";
import { runwayDescriptor } from "./runwayGeometry.js";
import { TILE_SIZE_M } from "./demCore.js";

const sceneryFile = (s, f) => `scenery/${encodeURIComponent(s.name)}/${f}?h=${s.hash.slice(0, 16)}`;
// Road classes shown by scale (metres per screen pixel): motorways always, minor roads close in.
const ROAD_STYLE = {
  motorway: [1e9, "rgba(214, 150, 92, 0.95)", 2.6], trunk: [1e9, "rgba(214, 170, 110, 0.95)", 2.2], primary: [60, "rgba(226, 200, 140, 0.95)", 1.8],
  secondary: [30, "rgba(235, 225, 190, 0.95)", 1.5], tertiary: [15, "rgba(240, 236, 220, 0.9)", 1.3],
  unclassified: [6, "rgba(245, 245, 240, 0.85)", 1.1], residential: [6, "rgba(245, 245, 240, 0.85)", 1.1],
};  // fmt: skip

const MAX_TILES = 160; // cached images (256 kB each)
const IN_FLIGHT = 2;

export class MapBackground {
  constructor(onReady) {
    this.onReady = onReady; // called when a tile arrives (redraw)
    this.tiles = new Map(); // key -> ImageBitmap, in least-recently-used order
    this.inFlight = new Set();
    this.wanted = [];
    this.roadCache = { key: null, segments: [] };
    this.scenery = null; // a real-world region: {scenery, manifest, runways, features: Map}
    this.worker = new Worker(new URL("./mapTileWorker.js", import.meta.url), { type: "module" });
    this.worker.onmessage = async ({ data }) => {
      this.inFlight.delete(data.key);
      const img = await createImageBitmap(new ImageData(data.pixels, TILE_PX, TILE_PX));
      this.tiles.set(data.key, img);
      while (this.tiles.size > MAX_TILES) {
        const oldest = this.tiles.keys().next().value;
        this.tiles.get(oldest).close();
        this.tiles.delete(oldest);
      }
      this._pump();
      this.onReady();
    };
  }

  _pump() {
    while (this.inFlight.size < IN_FLIGHT && this.wanted.length) {
      const t = this.wanted.shift();
      if (this.tiles.has(t.key) || this.inFlight.has(t.key)) continue;
      this.inFlight.add(t.key);
      this.worker.postMessage(t);
    }
  }

  // The flight's real-world region (stream world.scenery), or null: the procedural world.
  setScenery(scenery) {
    if ((scenery?.hash ?? null) === (this.scenery?.scenery.hash ?? null)) return;
    for (const img of this.tiles.values()) img.close();
    this.tiles.clear();
    this.inFlight.clear();
    this.wanted = [];
    this.scenery = scenery ? { scenery, manifest: null, runways: [], features: new Map() } : null;
    if (!scenery) return;
    const s = this.scenery;
    Promise.all(["manifest.json", "airfields.json"].map((f) => fetch(sceneryFile(scenery, f)).then((r) => r.json())))
      .then(([manifest, airfields]) => {
        s.manifest = manifest;
        s.runways = (airfields.runways ?? []).map(runwayDescriptor);
        this.onReady();
      })
      .catch(() => {});
  }

  // The region's data credit for the map's corner (empty for the procedural world).
  get credit() {
    return this.scenery?.manifest?.credit_short ?? "";
  }

  // A region tile's features (roads), loaded on demand.
  _features(ix, iz) {
    const s = this.scenery, key = `${ix},${iz}`, t = s.manifest.tiles;
    if (ix < t.ix_min || ix > t.ix_max || iz < t.iz_min || iz > t.iz_max) return null;
    if (!s.features.has(key)) {
      s.features.set(key, null);
      fetch(sceneryFile(s.scenery, `tiles/f_${ix}_${iz}.json`)).then((r) => r.json()).then((f) => {
        if (this.scenery === s) {
          s.features.set(key, f.roads ?? {});
          this.onReady();
        }
      }).catch(() => {});  // fmt: skip
    }
    return s.features.get(key);
  }

  // Draw under the map. `x`, `z`: the aircraft's world position (east, south); `angle`: the
  // view's rotation (radians, map bearing at the top); `mPerPx`: the view's scale; w, h.
  draw(ctx, { w, h, cx, cy, x, z, angle, mPerPx }) {
    if (this.scenery && !this.scenery.manifest) return; // the region's files are on their way
    const radius = (Math.hypot(w, h) / 2) * mPerPx;
    const want = levelFor(mPerPx);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-angle);
    ctx.scale(1 / mPerPx, 1 / mPerPx);
    ctx.translate(-x, -z);
    ctx.imageSmoothingEnabled = true;
    // Coarse to fine: cached coarser tiles fill in while the wanted level loads.
    this.wanted = [];
    for (const mpp of [...LEVELS_MPP].reverse()) {
      if (mpp < want) break;
      const span = TILE_PX * mpp;
      const i0 = Math.floor((x - radius) / span), i1 = Math.floor((x + radius) / span);
      const k0 = Math.floor((z - radius) / span), k1 = Math.floor((z + radius) / span);
      for (let k = k0; k <= k1; k++) {
        for (let i = i0; i <= i1; i++) {
          const key = `${mpp}:${i}:${k}`, img = this.tiles.get(key);
          if (img) {
            this.tiles.delete(key); // most recently used last
            this.tiles.set(key, img);
            ctx.drawImage(img, i * span, k * span, span + mpp, span + mpp); // one pixel of overlap: no seams from the smoothing at the edges
          } else if (mpp === want) {
            const d = Math.hypot((i + 0.5) * span - x, (k + 0.5) * span - z);
            const region = this.scenery ? { scenery: this.scenery.scenery, tiles: this.scenery.manifest.tiles } : {};
            this.wanted.push({ key, mpp, ix: i, iz: k, d, ...region });
          }
        }
      }
    }
    this.wanted.sort((a, b) => a.d - b.d);
    this._pump();
    if (this.scenery) {
      this._drawRegion(ctx, x, z, radius, mPerPx);
      ctx.restore();
      return;
    }

    // Villages (about 500 m across) and roads, as vectors.
    const reach = Math.min(12, Math.ceil(radius / VILLAGE_CELL_M) + 1);
    const ci = Math.floor(x / VILLAGE_CELL_M), cj = Math.floor(z / VILLAGE_CELL_M);
    const rk = `${ci},${cj},${reach}`;
    if (this.roadCache.key !== rk) {
      const villages = [];
      for (let i = ci - reach; i <= ci + reach; i++) for (let j = cj - reach; j <= cj + reach; j++) {
        const v = villageCentre(i, j);
        if (v) villages.push(v);
      }
      this.roadCache = { key: rk, segments: roadSegments(ci, cj, reach), villages };
    }
    ctx.strokeStyle = "rgba(120, 120, 116, 0.95)";
    ctx.lineWidth = Math.max(7, 1.6 * mPerPx);
    ctx.lineCap = "round";
    ctx.beginPath();
    for (const [[x0, z0], [x1, z1]] of this.roadCache.segments) {
      ctx.moveTo(x0, z0);
      ctx.lineTo(x1, z1);
    }
    ctx.stroke();
    ctx.fillStyle = "rgba(196, 160, 140, 0.85)";
    for (const v of this.roadCache.villages) {
      ctx.beginPath();
      ctx.arc(v[0], v[1], Math.max(220, 4 * mPerPx), 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.restore();
  }

  // A region's roads (by class and scale) and runways, in world metres.
  _drawRegion(ctx, x, z, radius, mPerPx) {
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const reach = Math.ceil(Math.min(radius, 40000) / TILE_SIZE_M);
    const ci = Math.floor(x / TILE_SIZE_M), cj = Math.floor(z / TILE_SIZE_M);
    for (const [cls, [maxMpp, colour, px]] of Object.entries(ROAD_STYLE).reverse()) {
      if (mPerPx > maxMpp) continue;
      ctx.strokeStyle = colour;
      ctx.lineWidth = px * mPerPx;
      ctx.beginPath();
      for (let k = cj - reach; k <= cj + reach; k++) {
        for (let i = ci - reach; i <= ci + reach; i++) {
          for (const line of this._features(i, k)?.[cls] ?? []) {
            ctx.moveTo(line[0], line[1]);
            for (let p = 2; p < line.length; p += 2) ctx.lineTo(line[p], line[p + 1]);
          }
        }
      }
      ctx.stroke();
    }
    ctx.fillStyle = "rgba(70, 72, 76, 0.95)";
    for (const d of this.scenery.runways) {
      const [[ax, az], [bx, bz]] = d.pavement;
      const len = Math.hypot(bx - ax, bz - az), lx = (bz - az) / len, lz = -(bx - ax) / len;
      const hw = Math.max(d.widthM / 2, 1.5 * mPerPx);
      ctx.beginPath();
      ctx.moveTo(ax + lx * hw, az + lz * hw);
      ctx.lineTo(bx + lx * hw, bz + lz * hw);
      ctx.lineTo(bx - lx * hw, bz - lz * hw);
      ctx.lineTo(ax - lx * hw, az - lz * hw);
      ctx.fill();
    }
  }
}
