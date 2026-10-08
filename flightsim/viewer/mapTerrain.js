// Map images of the procedural world for the moving map: shaded relief tinted by height,
// lakes, forests and rivers, computed from the same deterministic terrain as the 3D view
// (terrainCore.js) and the river of the terrain shader (terrain.js, ported below). Pure
// functions (tested with Node); run in mapTileWorker.js. Villages and roads are drawn by
// the map as vectors on top.
//
// Tiles: TILE_PX x TILE_PX pixels of `mpp` metres each, tile (ix, iz) covering world
// x (east) from ix * span and z (south) from iz * span, span = TILE_PX * mpp.

import { WATER_LEVEL_M, height, isForest } from "./terrainCore.js";

export const TILE_PX = 256;
export const LEVELS_MPP = [4, 8, 16, 32, 64]; // metres per pixel of the tile levels

const smoothstep = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

// --- River: a port of the terrain shader's rvHash / rvNoise / rvField (GLSL, float32;
// Math.fround keeps the hash close to the GPU's, so the river lines up with the 3D view).
const f = Math.fround;
const fract = (x) => f(x - Math.floor(x));
const gmod = (x, y) => f(x - y * Math.floor(x / y));
function rvHash(px, py) {
  px = gmod(px, 1024);
  py = gmod(py, 1024);
  let a = fract(f(px * f(0.1031))), b = fract(f(py * f(0.1031))), c = fract(f(px * f(0.1031)));
  const d = f(f(a * f(b + f(33.33))) + f(b * f(c + f(33.33))) + f(c * f(a + f(33.33))));
  a = f(a + d);
  b = f(b + d);
  c = f(c + d);
  return fract(f(f(a + b) * c));
}
function rvNoise(px, py) {
  const ix = Math.floor(px), iy = Math.floor(py), fx = px - ix, fy = py - iy;
  const ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
  const a = rvHash(ix, iy), b = rvHash(ix + 1, iy), c = rvHash(ix, iy + 1), d = rvHash(ix + 1, iy + 1);
  const ab = a + (b - a) * ux, cd = c + (d - c) * ux;
  return ab + (cd - ab) * uy;
}
export function rvField(x, z) {
  const px = x / 3200 + 41, py = z / 3200 + 41;
  return 0.65 * rvNoise(px, py) + 0.35 * rvNoise(px * 2.3 + 7, py * 2.3 + 7);
}
// Metres to the river's centre line where it can run (valley floors, off the airfield), else Infinity.
export function riverDistanceM(x, z, h) {
  const valley = (1 - smoothstep(0.4, 2.0, h)) * smoothstep(1800, 2400, Math.sqrt(x * x + z * z));
  if (valley < 0.5) return Infinity;
  const n = rvField(x, z);
  const gx = (rvField(x + 4, z) - n) / 4, gz = (rvField(x, z + 4) - n) / 4;
  return Math.abs(n - 0.5) / Math.max(Math.hypot(gx, gz), 1e-6);
}

// --- Colours (sRGB) ---------------------------------------------------------------------
const WATER = [63, 107, 140];
const FOREST = [46, 74, 42];
// Hypsometric tint: height (m) -> colour, valley green to hilltop tan.
const TINT = [[0, [104, 132, 76]], [80, [128, 142, 82]], [180, [150, 140, 92]], [280, [150, 124, 92]], [380, [170, 160, 150]]];
function tint(h) {
  if (h <= TINT[0][0]) return TINT[0][1];
  for (let i = 1; i < TINT.length; i++) {
    const [h1, c1] = TINT[i];
    if (h <= h1) {
      const [h0, c0] = TINT[i - 1], k = (h - h0) / (h1 - h0);
      return [0, 1, 2].map((j) => c0[j] + (c1[j] - c0[j]) * k);
    }
  }
  return TINT[TINT.length - 1][1];
}

// RGBA pixels (Uint8ClampedArray, TILE_PX^2 * 4) of a tile. Relief shading: light from the
// north-west, 45 deg up, slopes exaggerated x3 so the gentle hills read on a map.
export function mapTilePixels(mpp, ix, iz) {
  const n = TILE_PX, span = n * mpp, x0 = ix * span, z0 = iz * span;
  // Heights on an (n + 2)^2 grid of pixel centres, one pixel of margin for the gradients.
  const H = new Float64Array((n + 2) * (n + 2));
  for (let j = 0; j < n + 2; j++) for (let i = 0; i < n + 2; i++) H[j * (n + 2) + i] = height(x0 + (i - 0.5) * mpp, z0 + (j - 0.5) * mpp);
  const px = new Uint8ClampedArray(n * n * 4);
  const lx = -Math.SQRT1_2 * 0.7071, lz = -Math.SQRT1_2 * 0.7071, ly = 0.7071; // towards the light: north-west (x west, z north) and up
  const riverHalfWidth = Math.max(10.5, 0.6 * mpp); // at least about a pixel wide at small scales
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const g = (j + 1) * (n + 2) + (i + 1), h = H[g];
      const x = x0 + (i + 0.5) * mpp, z = z0 + (j + 0.5) * mpp;
      let c;
      if (h < WATER_LEVEL_M) c = WATER;
      else {
        const dx = ((H[g + 1] - H[g - 1]) / (2 * mpp)) * 3, dz = ((H[g + n + 2] - H[g - n - 2]) / (2 * mpp)) * 3;
        const inv = 1 / Math.sqrt(dx * dx + dz * dz + 1);
        const shade = Math.max(0.35, (-dx * lx - dz * lz + ly) * inv) / 0.7071; // 1 on flat ground
        const base = isForest(x, z, h) ? FOREST : tint(h);
        c = base.map((v) => v * (0.55 + 0.45 * shade));
        if (riverDistanceM(x, z, h) < riverHalfWidth) c = WATER;
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

// The level for a view's metres per pixel: the finest whose pixels are not much smaller
// than the screen's (powers of two, clamped to LEVELS_MPP).
export function levelFor(viewMpp) {
  return LEVELS_MPP.find((m) => m >= viewMpp * 0.75) ?? LEVELS_MPP[LEVELS_MPP.length - 1];
}
