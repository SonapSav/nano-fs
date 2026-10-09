// Moving-map tile images of a real-world region (as mapTerrain.js for the procedural
// world): land cover colours (a map palette for WorldCover classes, sea blue) with relief
// shading from the region's heights (demCore.js). Pure; run in mapTileWorker.js, which
// fetches the region tiles a map tile needs.

import { TILE_SIZE_M, heightAt, landcoverAt } from "./demCore.js";
import { TILE_PX } from "./mapTerrain.js";

// Map palette (sRGB; project choices, a muted chart style): WorldCover class -> colour.
const PALETTE = {
  10: [96, 128, 80], 20: [182, 172, 128], 30: [176, 182, 124], 40: [150, 170, 104], 50: [196, 176, 168], 60: [222, 208, 172],
  70: [245, 245, 245], 80: [108, 150, 182], 90: [150, 170, 140], 95: [80, 120, 90], 100: [190, 190, 170],
};  // fmt: skip
const OUTSIDE = [200, 196, 184]; // beyond the region: nothing known

// Region tiles (demCore.js tile indices) a map tile (mpp, ix, iz) needs, with a margin.
export function regionTilesFor(mpp, ix, iz) {
  const span = TILE_PX * mpp;
  const out = [];
  for (let k = Math.floor((iz * span - mpp) / TILE_SIZE_M); k <= Math.floor(((iz + 1) * span + mpp) / TILE_SIZE_M); k++) {
    for (let i = Math.floor((ix * span - mpp) / TILE_SIZE_M); i <= Math.floor(((ix + 1) * span + mpp) / TILE_SIZE_M); i++) out.push([i, k]);
  }
  return out;
}

// RGBA pixels of a map tile; `tiles` as demCore.js. Outside the region: a plain grey.
export function regionTilePixels(mpp, ix, iz, tiles) {
  const n = TILE_PX, span = n * mpp, x0 = ix * span, z0 = iz * span;
  const px = new Uint8ClampedArray(n * n * 4);
  const d = Math.max(mpp, 15);
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const x = x0 + (i + 0.5) * mpp, z = z0 + (j + 0.5) * mpp;
      const cls = landcoverAt(tiles, x, z);
      let c = cls ? PALETTE[cls] ?? PALETTE[60] : OUTSIDE;
      if (cls !== 80 && cls !== 0) {
        // Relief: light from the north-west, slopes exaggerated x6 (the region is flat).
        const dx = ((heightAt(tiles, x + d, z) - heightAt(tiles, x - d, z)) / (2 * d)) * 6;
        const dz = ((heightAt(tiles, x, z + d) - heightAt(tiles, x, z - d)) / (2 * d)) * 6;
        const shade = Math.max(0.5, (dx * 0.5 + dz * 0.5 + 0.7071) / Math.sqrt(dx * dx + dz * dz + 1)) / 0.7071;
        c = c.map((v) => v * (0.6 + 0.4 * shade));
      }
      const o = (j * n + i) * 4;
      px[o] = c[0];
      px[o + 1] = c[1];
      px[o + 2] = c[2];
      px[o + 3] = 255;
    }
  }
  return px;
}
