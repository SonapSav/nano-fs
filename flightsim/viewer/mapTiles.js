// Terrain background for the moving map: tiles from mapTileWorker.js (cached, nearest
// first, coarser levels shown while finer ones load) plus villages and roads as vectors.
// Drawn in world metres through the map's rotation and scale.

import { LEVELS_MPP, TILE_PX, levelFor } from "./mapTerrain.js";
import { VILLAGE_CELL_M, villageCentre } from "./terrainCore.js";
import { roadSegments } from "./roadNet.js";

const MAX_TILES = 160; // cached images (256 kB each)
const IN_FLIGHT = 2;

export class MapBackground {
  constructor(onReady) {
    this.onReady = onReady; // called when a tile arrives (redraw)
    this.tiles = new Map(); // key -> ImageBitmap, in least-recently-used order
    this.inFlight = new Set();
    this.wanted = [];
    this.roadCache = { key: null, segments: [] };
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

  // Draw under the map. `x`, `z`: the aircraft's world position (east, south); `angle`: the
  // view's rotation (radians, map bearing at the top); `mPerPx`: the view's scale; w, h.
  draw(ctx, { w, h, cx, cy, x, z, angle, mPerPx }) {
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
            this.wanted.push({ key, mpp, ix: i, iz: k, d });
          }
        }
      }
    }
    this.wanted.sort((a, b) => a.d - b.d);
    this._pump();

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
}
