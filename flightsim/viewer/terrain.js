// Procedural terrain: visual only. The physics flies over flat ground at sea level, so
// the terrain is shaped to agree with it where it matters: the airfield, lakes and
// valley floors sit at 0 m and hills rise above (at most ~350 m, far below cruise).
//
// Everything is a pure function of world position and a fixed seed, so every viewer and
// every flight sees the same world. World frame: x = east, y = up, z = south (metres).

import * as THREE from "three";

export const WORLD_SEED = 172;
export const TILE_SIZE_M = 4000;
export const AIRFIELD = { x: 0, z: 0, lengthM: 1000, widthM: 30, flatRadiusM: 1400 };

// --- Deterministic noise --------------------------------------------------------------

function hash2(ix, iz, seed = WORLD_SEED) {
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
function lakeness(x, z) {
  return 1 - smoothstep(0.24, 0.31, fbm(x / 3000, z / 3000, 3, 7));
}

export function height(x, z) {
  const n = fbm(x / 7000, z / 7000, 5, 1);
  let h = Math.max(0, (n - 0.42) / 0.58) ** 1.5 * 350; // valleys at 0, hills up to ~350 m
  h -= lakeness(x, z) * 25; // lakes dip below the water plane (just under 0 m)
  const r = Math.hypot(x - AIRFIELD.x, z - AIRFIELD.z);
  return h * smoothstep(AIRFIELD.flatRadiusM, AIRFIELD.flatRadiusM + 1200, r); // flat airfield at 0 m
}

export function isForest(x, z, h) {
  return h > 2 && fbm(x / 1800, z / 1800, 3, 3) > 0.55;
}

// Field crops; the patchwork itself is drawn per pixel in the terrain shader (fieldMaterial).
const FIELD_COLOURS = [0x7c8b55, 0x8e9a5a, 0x6f8248, 0xa59b62, 0x8b8a4e, 0x74874d, 0x9aa56a].map((c) => new THREE.Color(c));
const FIELD_BASE = new THREE.Color(0x7e8c53); // vertex colour under fields (seen only at their blurred edges)
const FOREST = new THREE.Color(0x3f5a33);
const ROCK = new THREE.Color(0x8a8172);
const SAND = new THREE.Color(0xb9ad86);
const GRASS = new THREE.Color(0x6f8a4c);

// Returns how much of the field patchwork shows here (0..1) and sets `out` to the
// underlying land colour.
function landColour(x, z, h, slope, out) {
  if (h < 3 && lakeness(x, z) > 0.05) {
    out.copy(SAND); // shoreline
    return 0;
  }
  if (isForest(x, z, h)) {
    out.copy(FOREST);
    return 0;
  }
  const rocky = Math.min(1, smoothstep(0.35, 0.7, slope) * 0.8 + smoothstep(230, 330, h) * 0.5);
  // Airfield grass fades into the fields over a few hundred metres.
  const r = Math.hypot(x - AIRFIELD.x, z - AIRFIELD.z);
  const grass = 1 - smoothstep(AIRFIELD.flatRadiusM - 500, AIRFIELD.flatRadiusM + 300, r);
  out.copy(FIELD_BASE).lerp(ROCK, rocky).lerp(GRASS, grass);
  return (1 - rocky) * (1 - grass);
}

// Lambert material plus a per-pixel field patchwork: ~450 m cells on a slightly rotated
// grid, one crop colour per cell, darker hedgerows along the edges. Crisp at any range.
function fieldMaterial() {
  const material = new THREE.MeshLambertMaterial({ vertexColors: true });
  material.onBeforeCompile = (shader) => {
    shader.uniforms.fieldColours = { value: FIELD_COLOURS };
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float fieldness;\nvarying float vFieldness;\nvarying vec2 vWorldXZ;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFieldness = fieldness;\nvWorldXZ = (modelMatrix * vec4(position, 1.0)).xz;");
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
uniform vec3 fieldColours[${FIELD_COLOURS.length}];
varying float vFieldness;
varying vec2 vWorldXZ;
float fieldHash(vec2 c) { return fract(sin(dot(c, vec2(12.9898, 78.233))) * 43758.5453); }`,
      )
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
{
  vec2 uv = vec2(vWorldXZ.x * 0.97 + vWorldXZ.y * 0.24, vWorldXZ.y * 0.97 - vWorldXZ.x * 0.24) / 450.0;
  vec2 cell = floor(uv);
  int k = int(fieldHash(cell) * ${FIELD_COLOURS.length}.0);
  vec3 crop = fieldColours[0];
  for (int i = 1; i < ${FIELD_COLOURS.length}; i++) if (i == k) crop = fieldColours[i];
  vec2 f = fract(uv);
  float edge = min(min(f.x, 1.0 - f.x), min(f.y, 1.0 - f.y)) * 450.0; // metres to the cell edge
  crop *= mix(0.72, 1.0, smoothstep(2.0, 7.0, edge)); // hedgerow
  diffuseColor.rgb = mix(diffuseColor.rgb, crop, clamp(vFieldness, 0.0, 1.0));
}`,
      );
  };
  return material;
}

// --- Tiles ----------------------------------------------------------------------------

function buildTileGeometry(tx, tz, segments) {
  // Grid plus a "skirt" ring hanging 40 m down, hiding cracks between tiles of different detail.
  const n = segments + 1;
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M, step = TILE_SIZE_M / segments;
  const pos = [], col = [], fld = [], nrm = [], idx = [];
  let minHeight = Infinity;
  const c = new THREE.Color();
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
      col.push(c.r, c.g, c.b);
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
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  g.setAttribute("fieldness", new THREE.Float32BufferAttribute(fld, 1));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(nrm, 3));
  g.setIndex(idx);
  g.userData.hasWater = minHeight < WATER_LEVEL_M;
  return g;
}

export const WATER_LEVEL_M = -0.5; // just below 0 m, so dry valley floors at 0 m stay dry
const WATER = new THREE.MeshLambertMaterial({ color: 0x3f6b8c });
const WATER_QUAD = new THREE.PlaneGeometry(TILE_SIZE_M, TILE_SIZE_M).rotateX(-Math.PI / 2);

function seededRandom(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
}

// Trees in forests and villages on low, flat open land, for one (near) tile.
function buildTileObjects(tx, tz, shared) {
  const group = new THREE.Group();
  const rnd = seededRandom(hash2(tx, tz, 23) * 4294967296);
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M;
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);

  const trees = [];
  for (let k = 0; k < 2500 && trees.length < 900; k++) {
    const x = x0 + rnd() * TILE_SIZE_M, z = z0 + rnd() * TILE_SIZE_M, h = height(x, z);
    if (isForest(x, z, h)) trees.push([x, h, z, 0.7 + rnd() * 0.7]);
  }
  if (trees.length) {
    const crowns = new THREE.InstancedMesh(shared.crown, shared.crownMat, trees.length);
    trees.forEach(([x, h, z, k], i) => crowns.setMatrixAt(i, m.compose(p.set(x, h + 7 * k, z), q.identity(), s.set(k, k, k))));
    group.add(crowns);
  }

  const houses = [];
  const cells = 2; // two 2-km village cells per tile side
  for (let ci = 0; ci < cells; ci++) {
    for (let cj = 0; cj < cells; cj++) {
      const cx = x0 + (cj + 0.5) * (TILE_SIZE_M / cells), cz = z0 + (ci + 0.5) * (TILE_SIZE_M / cells);
      if (hash2(Math.floor(cx / 2000), Math.floor(cz / 2000), 31) > 0.3) continue;
      const hc = height(cx, cz);
      if (hc < 2 || hc > 120 || isForest(cx, cz, hc)) continue;
      const count = 15 + Math.floor(rnd() * 35);
      for (let k = 0; k < count; k++) {
        const x = cx + (rnd() - 0.5) * 500, z = cz + (rnd() - 0.5) * 500, h = height(x, z);
        if (h > 1.5 && !isForest(x, z, h)) houses.push([x, h, z, rnd() * Math.PI, 8 + rnd() * 10, 6 + rnd() * 6]);
      }
    }
  }
  if (houses.length) {
    const walls = new THREE.InstancedMesh(shared.box, shared.wallMat, houses.length);
    const roofs = new THREE.InstancedMesh(shared.roof, shared.roofMat, houses.length);
    houses.forEach(([x, h, z, yaw, w, d], i) => {
      q.setFromAxisAngle(up, yaw);
      walls.setMatrixAt(i, m.compose(p.set(x, h + 2.5, z), q, s.set(w, 5, d)));
      roofs.setMatrixAt(i, m.compose(p.set(x, h + 5 + 1.5, z), q, s.set(w * 1.05, 3, d * 1.05)));
    });
    group.add(walls, roofs);
  }
  return group;
}

// --- Streaming manager ----------------------------------------------------------------

const RINGS = [
  { maxRing: 1, segments: 96, objects: true }, // the 3 x 3 tiles around the aircraft
  { maxRing: 2, segments: 48, objects: false },
  { maxRing: 5, segments: 16, objects: false }, // out to ~22 km, hidden in haze beyond
];

export class Terrain {
  constructor(scene) {
    this.scene = scene;
    this.tiles = new Map(); // key -> { mesh, objects, segments }
    this.queue = [];
    this.material = fieldMaterial();
    this.shared = {
      crown: new THREE.ConeGeometry(4, 14, 6),
      crownMat: new THREE.MeshLambertMaterial({ color: 0x2f4a2a }),
      box: new THREE.BoxGeometry(1, 1, 1),
      wallMat: new THREE.MeshLambertMaterial({ color: 0xd9d4c5 }),
      roof: (() => {
        const g = new THREE.CylinderGeometry(0, 0.75, 1, 4, 1);
        g.rotateY(Math.PI / 4);
        return g;
      })(),
      roofMat: new THREE.MeshLambertMaterial({ color: 0x8f3b2f }),
    };
    this.centre = null;
  }

  _wanted(cx, cz) {
    const wanted = new Map();
    const R = RINGS[RINGS.length - 1].maxRing;
    for (let dz = -R; dz <= R; dz++) {
      for (let dx = -R; dx <= R; dx++) {
        const ring = Math.max(Math.abs(dx), Math.abs(dz));
        const spec = RINGS.find((r) => ring <= r.maxRing);
        wanted.set(`${cx + dx},${cz + dz}`, { tx: cx + dx, tz: cz + dz, ring, ...spec });
      }
    }
    return wanted;
  }

  // Call every frame with the camera position. Builds tiles (nearest first) until about
  // `budgetMs` of this frame is used, at least one: a near tile takes ~20 ms, a far one < 1 ms.
  update(x, z, budgetMs = 8) {
    const cx = Math.floor(x / TILE_SIZE_M), cz = Math.floor(z / TILE_SIZE_M);
    if (!this.centre || this.centre[0] !== cx || this.centre[1] !== cz) {
      this.centre = [cx, cz];
      const wanted = this._wanted(cx, cz);
      for (const [key, tile] of this.tiles) {
        const want = wanted.get(key);
        if (!want || want.segments !== tile.segments || want.objects !== Boolean(tile.objects)) this._drop(key);
      }
      this.queue = [...wanted.values()].filter((w) => !this.tiles.has(`${w.tx},${w.tz}`)).sort((a, b) => a.ring - b.ring);
    }
    const start = performance.now();
    while (this.queue.length && (performance.now() - start < budgetMs || budgetMs <= 0)) {
      const w = this.queue.shift();
      const geometry = buildTileGeometry(w.tx, w.tz, w.segments);
      const mesh = new THREE.Mesh(geometry, this.material);
      if (geometry.userData.hasWater) {
        // Water only where this tile has lakes; elsewhere the land fallback shows through gaps.
        const water = new THREE.Mesh(WATER_QUAD, WATER);
        water.position.set((w.tx + 0.5) * TILE_SIZE_M, WATER_LEVEL_M, (w.tz + 0.5) * TILE_SIZE_M);
        mesh.add(water);
      }
      this.scene.add(mesh);
      const objects = w.objects ? buildTileObjects(w.tx, w.tz, this.shared) : null;
      if (objects) this.scene.add(objects);
      this.tiles.set(`${w.tx},${w.tz}`, { mesh, objects, segments: w.segments });
      if (budgetMs <= 0) break; // budget 0: exactly one tile (tests)
    }
  }

  get pending() {
    return this.queue.length;
  }

  _drop(key) {
    const t = this.tiles.get(key);
    this.scene.remove(t.mesh); // its water quad (a child) shares geometry and material
    t.mesh.geometry.dispose();
    if (t.objects) {
      this.scene.remove(t.objects);
      t.objects.traverse((o) => o.isInstancedMesh && o.dispose());
    }
    this.tiles.delete(key);
  }
}
