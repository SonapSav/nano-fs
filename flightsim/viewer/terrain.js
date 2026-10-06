// Procedural terrain. Its height is shared with the physics: flightsim/world/terrain.py is
// a bit-identical port, used by tasks with `terrain: procedural` (other tasks fly over
// flat ground at 0 m). The airfield, valley floors and lake surfaces sit at about 0 m and
// hills rise above (at most ~350 m).
//
// Everything is a pure function of world position and a fixed seed, so every viewer and
// every flight sees the same world. World frame: x = east, y = up, z = south (metres).

import * as THREE from "three";
import { addGroundDetail } from "./groundDetail.js";

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
// low, dry, open land (valley floors too, clear of lakes). Houses (buildTileObjects) and
// roads (roads.js) use the same list.
export function villageCentre(ci, cj) {
  if (hash2(ci, cj, 31) > 0.35) return null;
  const x = (ci + 0.5) * VILLAGE_CELL_M, z = (cj + 0.5) * VILLAGE_CELL_M;
  const h = height(x, z);
  if (h > 120 || lakeness(x, z) > 0.02 || isForest(x, z, h)) return null;
  if (Math.sqrt(x * x + z * z) < AIRFIELD.flatRadiusM + 600) return null; // not on the airfield
  return [x, z];
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
  const forest = forestness(x, z, h);
  const rocky = Math.min(1, smoothstep(0.35, 0.7, slope) * 0.8 + smoothstep(230, 330, h) * 0.5);
  // Airfield grass fades into the fields over a few hundred metres.
  const r = Math.hypot(x - AIRFIELD.x, z - AIRFIELD.z);
  const grass = 1 - smoothstep(AIRFIELD.flatRadiusM - 500, AIRFIELD.flatRadiusM + 300, r);
  out.copy(FIELD_BASE).lerp(ROCK, rocky).lerp(GRASS, grass).lerp(FOREST, forest);
  return (1 - rocky) * (1 - grass) * (1 - forest);
}

// Lambert material plus a per-pixel field patchwork: ~450 m cells on a slightly rotated
// grid, one crop colour per cell, darker hedgerows along the edges. Crisp at any range.
function fieldMaterial() {
  const material = new THREE.MeshLambertMaterial({ vertexColors: true });
  material.onBeforeCompile = (shader) => {
    shader.uniforms.fieldColours = { value: FIELD_COLOURS };
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float fieldness;\nvarying float vFieldness;\nvarying vec2 vWorldXZ;\nvarying float vHeight;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFieldness = fieldness;\nvWorldXZ = (modelMatrix * vec4(position, 1.0)).xz;\nvHeight = position.y;");
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
uniform vec3 fieldColours[${FIELD_COLOURS.length}];
varying float vFieldness;
varying vec2 vWorldXZ;
varying float vHeight;
float fieldHash(vec2 c) { return fract(sin(dot(c, vec2(12.9898, 78.233))) * 43758.5453); }
// River: a contour of a smooth noise field (cells wrap every 1024: small numbers only).
float rvHash(vec2 p) { p = mod(p, 1024.0); vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float rvNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(rvHash(i), rvHash(i + vec2(1.0, 0.0)), u.x), mix(rvHash(i + vec2(0.0, 1.0)), rvHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float rvField(vec2 xz) { vec2 p = xz / 3200.0 + 41.0; return 0.65 * rvNoise(p) + 0.35 * rvNoise(p * 2.3 + 7.0); }`,
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
}
{
  // River on the valley floors (dry land at ~0 m): the 0.5 contour of rvField, its width
  // kept in metres by dividing by the field's gradient; grassy banks either side.
  float valley = (1.0 - smoothstep(0.4, 2.0, vHeight)) * smoothstep(1800.0, 2400.0, length(vWorldXZ)); // not on the airfield
  if (valley > 0.0) {
    float n = rvField(vWorldXZ);
    vec2 grad = vec2(rvField(vWorldXZ + vec2(4.0, 0.0)) - n, rvField(vWorldXZ + vec2(0.0, 4.0)) - n) / 4.0;
    float metres = abs(n - 0.5) / max(length(grad), 1e-6);
    float water = (1.0 - smoothstep(9.0, 12.0, metres)) * valley;
    float bank = (1.0 - smoothstep(12.0, 22.0, metres)) * valley;
    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.30, 0.40, 0.25), bank * 0.6);
    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.17, 0.30, 0.40), water);
  }
}`,
      );
  };
  return addGroundDetail(material); // close-up texture on top of the patchwork
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

// Trees in forests and villages on low, flat open land, for one (near) tile. `far`: only
// sparser, simpler trees (the second ring of tiles, so distant hills are not bare).
function buildTileObjects(tx, tz, shared, far = false) {
  const group = new THREE.Group();
  const rnd = seededRandom(hash2(tx, tz, 23) * 4294967296);
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M;
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);

  const trees = [];
  const maxTrees = far ? Math.round(shared.maxTrees / 3) : shared.maxTrees;
  for (let k = 0; k < 2500 && trees.length < maxTrees; k++) {
    const x = x0 + rnd() * TILE_SIZE_M, z = z0 + rnd() * TILE_SIZE_M, h = height(x, z);
    if (isForest(x, z, h)) trees.push([x, h, z, 0.7 + rnd() * 0.7]);
  }
  if (trees.length) {
    const crowns = new THREE.InstancedMesh(far ? shared.farCrown : shared.crown, shared.crownMat, trees.length);
    trees.forEach(([x, h, z, k], i) => crowns.setMatrixAt(i, m.compose(p.set(x, h + 7 * k, z), q.identity(), s.set(k, k, k))));
    group.add(crowns);
  }
  if (far) return group;

  const houses = [];
  const landmarks = []; // one per village: a church or a water tower, seen from the circuit
  const cells = 2; // two 2-km village cells per tile side
  for (let ci = 0; ci < cells; ci++) {
    for (let cj = 0; cj < cells; cj++) {
      const centre = villageCentre(Math.floor((x0 + (cj + 0.5) * (TILE_SIZE_M / cells)) / VILLAGE_CELL_M), Math.floor((z0 + (ci + 0.5) * (TILE_SIZE_M / cells)) / VILLAGE_CELL_M));
      if (!centre) continue;
      const [cx, cz] = centre;
      landmarks.push([cx, height(cx, cz), cz, hash2(Math.floor(cx), Math.floor(cz), 41)]);
      const count = 15 + Math.floor(rnd() * 35);
      for (let k = 0; k < count; k++) {
        const x = cx + (rnd() - 0.5) * 500, z = cz + (rnd() - 0.5) * 500, h = height(x, z);
        if (lakeness(x, z) < 0.02 && !isForest(x, z, h)) houses.push([x, h, z, rnd() * Math.PI, 8 + rnd() * 10, 6 + rnd() * 6]);
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
  for (const [x, h, z, kind] of landmarks) {
    if (kind < 0.6) {
      // Church: nave, tower and spire, ~35 m tall.
      const nave = new THREE.Mesh(shared.box, shared.wallMat);
      nave.scale.set(10, 9, 22);
      nave.position.set(x, h + 4.5, z);
      const tower = new THREE.Mesh(shared.box, shared.wallMat);
      tower.scale.set(5, 22, 5);
      tower.position.set(x, h + 11, z - 13);
      const spire = new THREE.Mesh(shared.spire, shared.roofMat);
      spire.position.set(x, h + 22 + 7, z - 13);
      group.add(nave, tower, spire);
    } else {
      // Water tower: a tank on a column, ~30 m tall.
      const column = new THREE.Mesh(shared.column, shared.towerMat);
      column.position.set(x, h + 12, z);
      const tank = new THREE.Mesh(shared.tank, shared.towerMat);
      tank.position.set(x, h + 27, z);
      group.add(column, tank);
    }
  }
  return group;
}

// --- Streaming manager ----------------------------------------------------------------

// Quality presets: terrain rings (tile mesh resolution and how far tiles reach), trees per
// near tile, haze distances, pixel ratio and the close-up ground detail. "high" is the
// original setting.
export const QUALITY = {
  low: {
    rings: [{ maxRing: 1, segments: 48, objects: true }, { maxRing: 3, segments: 12, objects: false }],
    maxTrees: 250, fog: [5000, 12000], pixelRatio: 1, groundDetail: 0,
  },
  medium: {
    rings: [{ maxRing: 1, segments: 64, objects: true }, { maxRing: 2, segments: 32, objects: false, farTrees: true }, { maxRing: 4, segments: 12, objects: false }],
    maxTrees: 550, fog: [7000, 17000], pixelRatio: 1.5, groundDetail: 1,
  },
  high: {
    rings: [
      { maxRing: 1, segments: 96, objects: true }, // the 3 x 3 tiles around the aircraft
      { maxRing: 2, segments: 48, objects: false, farTrees: true },
      { maxRing: 5, segments: 16, objects: false }, // out to ~22 km, hidden in haze beyond
    ],
    maxTrees: 900, fog: [9000, 21000], pixelRatio: 2, groundDetail: 1,
  },
}; // fmt: skip

export class Terrain {
  constructor(scene, quality = "high") {
    this.scene = scene;
    this.rings = QUALITY[quality].rings;
    this.tiles = new Map(); // key -> { mesh, objects, segments }
    this.queue = [];
    this.material = fieldMaterial();
    this.shared = {
      crown: new THREE.ConeGeometry(4, 14, 6),
      farCrown: new THREE.ConeGeometry(4.5, 14, 4), // far ring: fewer faces
      spire: new THREE.ConeGeometry(3.4, 14, 4).rotateY(Math.PI / 4),
      column: new THREE.CylinderGeometry(1.5, 2, 24, 8),
      tank: new THREE.CylinderGeometry(6, 5, 7, 12),
      towerMat: new THREE.MeshLambertMaterial({ color: 0xbfc4c7 }),
      crownMat: new THREE.MeshLambertMaterial({ color: 0x2f4a2a }),
      box: new THREE.BoxGeometry(1, 1, 1),
      wallMat: new THREE.MeshLambertMaterial({ color: 0xd9d4c5 }),
      roof: (() => {
        const g = new THREE.CylinderGeometry(0, 0.75, 1, 4, 1);
        g.rotateY(Math.PI / 4);
        return g;
      })(),
      roofMat: new THREE.MeshLambertMaterial({ color: 0x8f3b2f }),
      maxTrees: QUALITY[quality].maxTrees,
    };
    this.centre = null;
  }

  // Rebuild every tile for a quality preset (QUALITY).
  setQuality(quality) {
    this.rings = QUALITY[quality].rings;
    this.shared.maxTrees = QUALITY[quality].maxTrees;
    for (const key of [...this.tiles.keys()]) this._drop(key);
    this.queue = [];
    this.centre = null;
  }

  _wanted(cx, cz) {
    const wanted = new Map();
    const R = this.rings[this.rings.length - 1].maxRing;
    for (let dz = -R; dz <= R; dz++) {
      for (let dx = -R; dx <= R; dx++) {
        const ring = Math.max(Math.abs(dx), Math.abs(dz));
        const spec = this.rings.find((r) => ring <= r.maxRing);
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
        if (!want || want.segments !== tile.segments || want.objects !== tile.near || Boolean(want.farTrees) !== tile.far) this._drop(key);
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
      const objects = w.objects || w.farTrees ? buildTileObjects(w.tx, w.tz, this.shared, !w.objects) : null;
      if (objects) this.scene.add(objects);
      this.tiles.set(`${w.tx},${w.tz}`, { mesh, objects, segments: w.segments, near: Boolean(w.objects), far: Boolean(w.farTrees) });
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
