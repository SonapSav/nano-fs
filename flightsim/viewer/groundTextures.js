// Close-up detail for a real-world region's ground, blended into its satellite imagery
// (terrain.js regionMaterial): below a metre the imagery is a smooth blur, so the ground
// gets fine texture by what the imagery shows there, judged from its colour per pixel:
// sand (grain and wind ripples), asphalt and concrete (aggregate speckle and stains),
// vegetation (clumps of foliage). Visual only; no image files: the textures are made here
// once, seeded, tileable.
//
// One RGBA texture holds the patterns (R sand, G asphalt, B vegetation, A a slow
// variation that breaks up the repeats), sampled at two scales. Each modulates the
// imagery's brightness around 1; the strengths and sizes are project choices, by eye.

import * as THREE from "three";
import { groundDetailStrength } from "./groundDetail.js";

export const DETAIL_PX = 512;
const NEAR_M = 6.0; // the fine pattern's repeat (m): 1.2 cm a texel
const FAR_M = 41.0; // the coarse one's (not a multiple of NEAR_M, so repeats do not line up)

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Value noise on a cells x cells lattice that wraps (tileable), smoothly interpolated, at
// size x size pixels.
function periodicNoise(size, cells, rand) {
  const lat = new Float32Array(cells * cells).map(() => rand());
  const out = new Float32Array(size * size);
  const s = (t) => t * t * (3 - 2 * t);
  for (let y = 0; y < size; y++) {
    const fy = (y / size) * cells, y0 = Math.floor(fy), ty = s(fy - y0), y1 = (y0 + 1) % cells;
    for (let x = 0; x < size; x++) {
      const fx = (x / size) * cells, x0 = Math.floor(fx), tx = s(fx - x0), x1 = (x0 + 1) % cells;
      const a = lat[y0 * cells + x0], b = lat[y0 * cells + x1], c = lat[y1 * cells + x0], d = lat[y1 * cells + x1];
      out[y * size + x] = (a + (b - a) * tx) * (1 - ty) + (c + (d - c) * tx) * ty;
    }
  }
  return out;
}

function fbm(size, cellsList, weights, rand) {
  const out = new Float32Array(size * size);
  cellsList.forEach((cells, i) => {
    const n = periodicNoise(size, cells, rand);
    for (let k = 0; k < out.length; k++) out[k] += n[k] * weights[i];
  });
  return out;
}

// Mean 0.5, the given spread (standard deviation), clipped to 0..1.
function normalize(a, spread) {
  let m = 0, v = 0;
  for (const x of a) m += x;
  m /= a.length;
  for (const x of a) v += (x - m) ** 2;
  const sd = Math.sqrt(v / a.length) || 1;
  return a.map((x) => Math.min(1, Math.max(0, 0.5 + ((x - m) / sd) * spread)));
}

// The four patterns, each DETAIL_PX^2 in 0..1 (mean 0.5).
export function detailPatterns(size = DETAIL_PX, seed = 1) {
  const rand = rng(seed);
  // Sand: grain plus, in patches, faint wind ripples (about 17 cm apart over the 6 m
  // repeat), their crests wandering (a warped phase).
  const grain = fbm(size, [128, 256], [0.5, 0.5], rand);
  const warp = fbm(size, [4, 8], [0.7, 0.3], rand);
  const patch = periodicNoise(size, 4, rand);
  const sand = new Float32Array(size * size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const k = y * size + x;
      const ripple = Math.sin(2 * Math.PI * (36 * (y / size) + 3 * (x / size) + 7 * warp[k]));
      const where = Math.max(0, patch[k] - 0.45) * 1.8; // ripples in patches only
      sand[k] = 0.8 * grain[k] + 0.2 * (0.5 + 0.5 * ripple) * where;
    }
  }
  // Asphalt: fine aggregate, a few light stones, darker stains.
  const fine = periodicNoise(size, 256, rand);
  const stains = fbm(size, [6, 12], [0.6, 0.4], rand);
  const asphalt = new Float32Array(size * size);
  for (let k = 0; k < asphalt.length; k++) asphalt[k] = 0.55 * fine[k] + (rand() < 0.012 ? 0.6 : 0) + 0.45 * stains[k];
  // Vegetation: clumps (leaves and gaps) at two sizes.
  const clumps = fbm(size, [24, 48, 96], [0.45, 0.35, 0.2], rand);
  const veg = clumps.map((v) => Math.min(1, Math.max(0, (v - 0.35) * 2.2)));
  const slow = fbm(size, [2, 4], [0.6, 0.4], rand);
  return { sand: normalize(sand, 0.2), asphalt: normalize(asphalt, 0.18), veg: normalize(veg, 0.24), slow: normalize(slow, 0.25) };
}

let texture = null;

export function detailTexture() {
  if (texture) return texture;
  const p = detailPatterns();
  const data = new Uint8Array(DETAIL_PX * DETAIL_PX * 4);
  for (let k = 0; k < DETAIL_PX * DETAIL_PX; k++) {
    data[4 * k] = Math.round(p.sand[k] * 255);
    data[4 * k + 1] = Math.round(p.asphalt[k] * 255);
    data[4 * k + 2] = Math.round(p.veg[k] * 255);
    data[4 * k + 3] = Math.round(p.slow[k] * 255);
  }
  texture = new THREE.DataTexture(data, DETAIL_PX, DETAIL_PX);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.anisotropy = 8;
  texture.needsUpdate = true;
  return texture;
}

export const detailUniforms = {
  groundDetailStrength, // the quality setting's (groundDetail.js): 0 turns it off
  detailMap: { value: null }, // set on first use (detailTexture)
};

export const DETAIL_GLSL = `
uniform sampler2D detailMap;
uniform float groundDetailStrength;
// A pattern repeat's texture coordinates near the camera (world x, z less a whole number of
// repeats around the camera: small numbers, full precision far from the origin).
vec2 detailUv(vec2 p, float repeatM) {
  return (p - floor(cameraPosition.xz / repeatM) * repeatM) / repeatM;
}
// The imagery's colour (linear) with fine detail by what it shows: sand, paving or plants.
vec3 groundDetail(vec3 c, vec2 p, float dist, float land) {
  float fade = (1.0 - smoothstep(${(60).toFixed(1)}, ${(900).toFixed(1)}, dist)) * groundDetailStrength * land;
  if (fade < 0.002) return c;
  vec4 n = texture2D(detailMap, detailUv(p, ${NEAR_M.toFixed(1)}));
  vec4 f = texture2D(detailMap, detailUv(p.yx, ${FAR_M.toFixed(1)})); // swapped axes: the coarse repeat runs across the fine one
  // What the colour says: green over red and blue (plants); grey and dark (asphalt,
  // concrete is grey and light: half way); otherwise sand.
  float lum = dot(c, vec3(0.2126, 0.7152, 0.0722));
  float sat = (max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b))) / max(max(c.r, max(c.g, c.b)), 1e-3);
  float veg = smoothstep(0.0, 0.025, c.g - max(c.r, c.b) * 0.92);
  float paved = (1.0 - veg) * (1.0 - smoothstep(0.12, 0.30, sat)) * (1.0 - 0.5 * smoothstep(0.15, 0.45, lum));
  float sand = max(0.0, 1.0 - veg - paved);
  float d = sand * (0.75 * n.r + 0.25 * f.r) + paved * (0.8 * n.g + 0.2 * f.g) + veg * (0.6 * n.b + 0.4 * f.b);
  d += (f.a - 0.5) * 0.4; // slow variation over the coarse repeat
  float amp = sand * 0.45 + paved * 0.45 + veg * 0.8;
  return c * (1.0 + amp * (d - 0.5) * 2.0 * fade);
}`;
