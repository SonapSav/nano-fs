// Procedural terrain: height, land cover and the data of terrain tiles, without three.js,
// so the tile builder can run in a Web Worker (terrainWorker.js) as well as on the page.
// terrain.js re-exports the public parts and turns tile data into meshes.
//
// The height is shared with the physics: flightsim/world/terrain.py is a bit-identical
// port, used by tasks with `terrain: procedural`. World frame: x = east, y = up, z = south.

export const WORLD_SEED = 172;
export const TILE_SIZE_M = 4000;
export const AIRFIELD = { x: 0, z: 0, lengthM: 1000, widthM: 30, flatRadiusM: 1400 };
export const WATER_LEVEL_M = -0.5; // just below 0 m, so dry valley floors at 0 m stay dry

// --- Deterministic noise --------------------------------------------------------------

export function hash2(ix, iz, seed = WORLD_SEED) {
  // Integer hash -> [0, 1). Same inputs always give the same value, in any browser.
  let h = (ix * 374761393 + iz * 668265263 + seed * 2147483647) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

function valueNoise(x, z, seed) {
  const ix = Math.floor(x), iz = Math.floor(z);
  const fx = x - ix, fz = z - iz;
  const sx = fx * fx * (3 - 2 * fx), sz = fz * fz * (3 - 2 * fz);
  const a = hash2(ix, iz, seed), b = hash2(ix + 1, iz, seed);
  const c = hash2(ix, iz + 1, seed), d = hash2(ix + 1, iz + 1, seed);
  return a + (b - a) * sx + (c - a) * sz + (a - b - c + d) * sx * sz;
}

function fbm(x, z, octaves, seed) {
  let sum = 0, amp = 0.5, freq = 1, norm = 0;
  for (let o = 0; o < octaves; o++) {
    sum += amp * valueNoise(x * freq, z * freq, seed + o * 101);
    norm += amp;
    amp *= 0.5;
    freq *= 2.03;
  }
  return sum / norm; // ~[0, 1]
}

const smoothstep = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

// --- Terrain height and land cover ----------------------------------------------------

// 0 on dry land, rising to 1 in the middle of a lake.
export function lakeness(x, z) {
  return 1 - smoothstep(0.24, 0.31, fbm(x / 3000, z / 3000, 3, 7));
}

// The physics lands on this same terrain: flightsim/world/terrain.py is an exact port.
// Keep the two in step, using only operations that give identical results in Python and
// JavaScript (sqrt, not ** or hypot); tests/test_world_terrain.py compares them.
export function height(x, z) {
  const n = fbm(x / 7000, z / 7000, 5, 1);
  const t = Math.max(0, (n - 0.42) / 0.58);
  let h = t * Math.sqrt(t) * 350; // valleys at 0, hills up to ~350 m
  h -= lakeness(x, z) * 25; // lakes dip below the water plane (just under 0 m)
  const dx = x - AIRFIELD.x, dz = z - AIRFIELD.z;
  const r = Math.sqrt(dx * dx + dz * dz);
  return h * smoothstep(AIRFIELD.flatRadiusM, AIRFIELD.flatRadiusM + 1200, r); // flat airfield at 0 m
}

export function isForest(x, z, h) {
  return h > 2 && fbm(x / 1800, z / 1800, 3, 3) > 0.55;
}

// How forested a point looks (0..1): soft edges around isForest's threshold, so forest
// borders are not stepped along the terrain mesh grid. Trees still follow isForest.
function forestness(x, z, h) {
  return h > 2 ? smoothstep(0.53, 0.57, fbm(x / 1800, z / 1800, 3, 3)) : 0;
}

export const VILLAGE_CELL_M = 2000;

// The village of a 2 km cell ([x, z] of its centre), or null: about one cell in three, on
// low, dry, open land (valley floors too, clear of lakes). Houses (tileObjectsData) and
// roads (roads.js) use the same list.
export function villageCentre(ci, cj) {
  if (hash2(ci, cj, 31) > 0.35) return null;
  const x = (ci + 0.5) * VILLAGE_CELL_M, z = (cj + 0.5) * VILLAGE_CELL_M;
  const h = height(x, z);
  if (h > 120 || lakeness(x, z) > 0.02 || isForest(x, z, h)) return null;
  if (Math.sqrt(x * x + z * z) < AIRFIELD.flatRadiusM + 600) return null; // not on the airfield
  return [x, z];
}

// Land colours in linear RGB, converted from sRGB exactly as three.js does for a
// THREE.Color(hex) (the tiles' vertex colours are in the renderer's linear working space).
const srgbToLinear = (c) => (c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4));
const linear = (hex) => [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255].map((v) => srgbToLinear(v / 255));
const FIELD_BASE = linear(0x7e8c53); // vertex colour under fields (seen only at their blurred edges)
const FOREST = linear(0x3f5a33);
const ROCK = linear(0x8a8172);
const SAND = linear(0xb9ad86);
const GRASS = linear(0x6f8a4c);
const lerp3 = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];

// Returns how much of the field patchwork shows here (0..1) and sets `out` (3 numbers) to
// the underlying land colour.
function landColour(x, z, h, slope, out) {
  if (h < 3 && lakeness(x, z) > 0.05) {
    out.set(SAND); // shoreline
    return 0;
  }
  const forest = forestness(x, z, h);
  const rocky = Math.min(1, smoothstep(0.35, 0.7, slope) * 0.8 + smoothstep(230, 330, h) * 0.5);
  // Airfield grass fades into the fields over a few hundred metres.
  const r = Math.hypot(x - AIRFIELD.x, z - AIRFIELD.z);
  const grass = 1 - smoothstep(AIRFIELD.flatRadiusM - 500, AIRFIELD.flatRadiusM + 300, r);
  out.set(lerp3(lerp3(lerp3(FIELD_BASE, ROCK, rocky), GRASS, grass), FOREST, forest));
  return (1 - rocky) * (1 - grass) * (1 - forest);
}


// --- Tiles ----------------------------------------------------------------------------

// Tile mesh data: a grid plus a "skirt" ring hanging 40 m down, hiding cracks between tiles
// of different detail. Typed arrays (transferable from a worker) and whether it has lakes.
export function tileGeometryData(tx, tz, segments) {
  const n = segments + 1;
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M, step = TILE_SIZE_M / segments;
  const pos = [], col = [], fld = [], nrm = [], idx = [];
  let minHeight = Infinity;
  const c = new Float64Array(3);
  const grid = (i, j) => i * n + j;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const x = x0 + j * step, z = z0 + i * step;
      const h = height(x, z);
      minHeight = Math.min(minHeight, h);
      // Normals from the height function itself (central differences), not from the tile's
      // triangles, so shading matches exactly across tile edges.
      const dhdx = (height(x + 10, z) - height(x - 10, z)) / 20;
      const dhdz = (height(x, z + 10) - height(x, z - 10)) / 20;
      const slope = Math.hypot(dhdx, dhdz);
      const inv = 1 / Math.sqrt(dhdx * dhdx + 1 + dhdz * dhdz);
      nrm.push(-dhdx * inv, inv, -dhdz * inv);
      pos.push(x, h, z);
      fld.push(landColour(x, z, h, slope, c));
      col.push(c[0], c[1], c[2]);
    }
  }
  for (let i = 0; i < segments; i++) {
    for (let j = 0; j < segments; j++) {
      const a = grid(i, j), b = grid(i, j + 1), d = grid(i + 1, j), e = grid(i + 1, j + 1);
      idx.push(a, d, b, b, d, e);
    }
  }
  const edge = [];
  for (let j = 0; j < n; j++) edge.push(grid(0, j));
  for (let i = 1; i < n; i++) edge.push(grid(i, n - 1));
  for (let j = n - 2; j >= 0; j--) edge.push(grid(n - 1, j));
  for (let i = n - 2; i > 0; i--) edge.push(grid(i, 0));
  const base = pos.length / 3;
  for (const v of edge) {
    pos.push(pos[3 * v], pos[3 * v + 1] - 40, pos[3 * v + 2]);
    col.push(col[3 * v], col[3 * v + 1], col[3 * v + 2]);
    fld.push(fld[v]);
    nrm.push(nrm[3 * v], nrm[3 * v + 1], nrm[3 * v + 2]);
  }
  for (let k = 0; k < edge.length; k++) {
    const a = edge[k], b = edge[(k + 1) % edge.length], sa = base + k, sb = base + ((k + 1) % edge.length);
    idx.push(a, b, sa, b, sb, sa, a, sa, b, b, sa, sb); // both windings: visible from any side
  }
  return {
    position: new Float32Array(pos), color: new Float32Array(col), fieldness: new Float32Array(fld),
    normal: new Float32Array(nrm), index: new Uint32Array(idx), hasWater: minHeight < WATER_LEVEL_M,
  };
}

function seededRandom(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
}

// Trees in forests and villages on low, flat open land, for one (near) tile, as numbers:
// trees [x, h, z, size]..., houses [x, h, z, yaw, width, depth]..., landmarks (one per
// village: a church or a water tower) [x, h, z, kind]... `far`: only sparser trees (the
// second ring of tiles, so distant hills are not bare).
export function tileObjectsData(tx, tz, maxTreesNear, far = false) {
  const rnd = seededRandom(hash2(tx, tz, 23) * 4294967296);
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M;
  const trees = [];
  const maxTrees = far ? Math.round(maxTreesNear / 3) : maxTreesNear;
  let count = 0;
  for (let k = 0; k < 2500 && count < maxTrees; k++) {
    const x = x0 + rnd() * TILE_SIZE_M, z = z0 + rnd() * TILE_SIZE_M, h = height(x, z);
    if (isForest(x, z, h)) {
      trees.push(x, h, z, 0.7 + rnd() * 0.7);
      count++;
    }
  }
  const houses = [], landmarks = [];
  if (!far) {
    const cells = 2; // two 2-km village cells per tile side
    for (let ci = 0; ci < cells; ci++) {
      for (let cj = 0; cj < cells; cj++) {
        const centre = villageCentre(Math.floor((x0 + (cj + 0.5) * (TILE_SIZE_M / cells)) / VILLAGE_CELL_M), Math.floor((z0 + (ci + 0.5) * (TILE_SIZE_M / cells)) / VILLAGE_CELL_M));
        if (!centre) continue;
        const [cx, cz] = centre;
        landmarks.push(cx, height(cx, cz), cz, hash2(Math.floor(cx), Math.floor(cz), 41));
        const n = 15 + Math.floor(rnd() * 35);
        for (let k = 0; k < n; k++) {
          const x = cx + (rnd() - 0.5) * 500, z = cz + (rnd() - 0.5) * 500, h = height(x, z);
          if (lakeness(x, z) < 0.02 && !isForest(x, z, h)) houses.push(x, h, z, rnd() * Math.PI, 8 + rnd() * 10, 6 + rnd() * 6);
        }
      }
    }
  }
  return { trees: new Float64Array(trees), houses: new Float64Array(houses), landmarks: new Float64Array(landmarks) };
}
