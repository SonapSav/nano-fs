// Procedural terrain (meshes; the height itself is in terrainCore.js). Its height is shared
// with the physics: flightsim/world/terrain.py is a bit-identical port, used by tasks with `terrain: procedural` (other tasks fly over
// flat ground at 0 m). The airfield, valley floors and lake surfaces sit at about 0 m and
// hills rise above (at most ~350 m).
//
// Everything is a pure function of world position and a fixed seed, so every viewer and
// every flight sees the same world. World frame: x = east, y = up, z = south (metres).

import * as THREE from "three";
import { addGroundDetail } from "./groundDetail.js";
import { TILE_SIZE_M, WATER_LEVEL_M, tileGeometryData, tileObjectsData } from "./terrainCore.js";

// Height, land cover and tile data live in terrainCore.js (no three.js: also used by the
// tile worker); re-exported here for the rest of the viewer.
export { AIRFIELD, TILE_SIZE_M, VILLAGE_CELL_M, WATER_LEVEL_M, WORLD_SEED, height, isForest, villageCentre } from "./terrainCore.js";

// Field crops; the patchwork itself is drawn per pixel in the terrain shader (fieldMaterial).
const FIELD_COLOURS = [0x7c8b55, 0x8e9a5a, 0x6f8248, 0xa59b62, 0x8b8a4e, 0x74874d, 0x9aa56a].map((c) => new THREE.Color(c));

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

const WATER = new THREE.MeshLambertMaterial({ color: 0x3f6b8c });
const WATER_QUAD = new THREE.PlaneGeometry(TILE_SIZE_M, TILE_SIZE_M).rotateX(-Math.PI / 2);

// --- Tiles: meshes from tile data -------------------------------------------------------

function tileGeometry(data) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(data.position, 3));
  g.setAttribute("color", new THREE.BufferAttribute(data.color, 3));
  g.setAttribute("fieldness", new THREE.BufferAttribute(data.fieldness, 1));
  g.setAttribute("normal", new THREE.BufferAttribute(data.normal, 3));
  g.setIndex(new THREE.BufferAttribute(data.index, 1));
  g.userData.hasWater = data.hasWater;
  return g;
}

function tileObjects(data, shared, far) {
  const group = new THREE.Group();
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  const { trees, houses, landmarks } = data;
  if (trees.length) {
    const crowns = new THREE.InstancedMesh(far ? shared.farCrown : shared.crown, shared.crownMat, trees.length / 4);
    for (let i = 0; i < trees.length / 4; i++) {
      const [x, h, z, k] = trees.subarray(4 * i, 4 * i + 4);
      crowns.setMatrixAt(i, m.compose(p.set(x, h + 7 * k, z), q.identity(), s.set(k, k, k)));
    }
    group.add(crowns);
  }
  if (houses.length) {
    const n = houses.length / 6;
    const walls = new THREE.InstancedMesh(shared.box, shared.wallMat, n);
    const roofs = new THREE.InstancedMesh(shared.roof, shared.roofMat, n);
    for (let i = 0; i < n; i++) {
      const [x, h, z, yaw, w, d] = houses.subarray(6 * i, 6 * i + 6);
      q.setFromAxisAngle(up, yaw);
      walls.setMatrixAt(i, m.compose(p.set(x, h + 2.5, z), q, s.set(w, 5, d)));
      roofs.setMatrixAt(i, m.compose(p.set(x, h + 5 + 1.5, z), q, s.set(w * 1.05, 3, d * 1.05)));
    }
    group.add(walls, roofs);
  }
  for (let i = 0; i < landmarks.length / 4; i++) {
    const [x, h, z, kind] = landmarks.subarray(4 * i, 4 * i + 4);
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

// Tiles are built in a Web Worker (terrainWorker.js) when the browser has one, so the
// drawing never waits for them; otherwise (Node tests, a failed worker) here, a few per
// frame. The worker returns tile data; the meshes are made here (fast).
const MAX_IN_FLIGHT = 3; // tiles asked of the worker at a time (nearest first; stays responsive when the wanted set changes)
const specKey = (w) => `${w.segments}|${w.objects ? 1 : 0}|${w.farTrees ? 1 : 0}`;

export class Terrain {
  constructor(scene, quality = "high", { worker = true } = {}) {
    this.scene = scene;
    this.wanted = new Map();
    this.inFlight = new Map(); // key -> spec key asked of the worker
    this.worker = null;
    if (worker && typeof Worker !== "undefined") {
      try {
        this.worker = new Worker(new URL("./terrainWorker.js", import.meta.url), { type: "module" });
        this.worker.onmessage = (e) => this._built(e.data);
        this.worker.onerror = () => this._noWorker();
      } catch {
        this.worker = null;
      }
    }
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
    this.inFlight.clear(); // late answers no longer match the wanted spec and are dropped
    this.centre = null;
  }

  // The worker failed (e.g. no module workers): build here from now on.
  _noWorker() {
    this.worker?.terminate();
    this.worker = null;
    this.inFlight.clear();
    this.centre = null; // recompute the queue on the next update
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

  // Call every frame with the camera position. Asks the worker for the nearest missing tiles;
  // without one, builds tiles here until about `budgetMs` of this frame is used, at least
  // one (a near tile takes ~15 ms, a far one < 1 ms).
  update(x, z, budgetMs = 8) {
    const cx = Math.floor(x / TILE_SIZE_M), cz = Math.floor(z / TILE_SIZE_M);
    if (!this.centre || this.centre[0] !== cx || this.centre[1] !== cz) {
      this.centre = [cx, cz];
      this.wanted = this._wanted(cx, cz);
      for (const [key, tile] of this.tiles) {
        const want = this.wanted.get(key);
        if (!want || want.segments !== tile.segments || want.objects !== tile.near || Boolean(want.farTrees) !== tile.far) this._drop(key);
      }
      this.queue = [...this.wanted.values()].filter((w) => !this.tiles.has(`${w.tx},${w.tz}`)).sort((a, b) => a.ring - b.ring);
    }
    if (this.worker) {
      this._pump();
      return;
    }
    const start = performance.now();
    while (this.queue.length && (performance.now() - start < budgetMs || budgetMs <= 0)) {
      const w = this.queue.shift();
      const wantsObjects = Boolean(w.objects || w.farTrees);
      this._add(w, tileGeometryData(w.tx, w.tz, w.segments), wantsObjects ? tileObjectsData(w.tx, w.tz, this.shared.maxTrees, !w.objects) : null);
      if (budgetMs <= 0) break; // budget 0: exactly one tile (tests)
    }
  }

  // Ask the worker for the next tiles (also on each answer, so it stays busy whatever the
  // frame rate).
  _pump() {
    while (this.worker && this.queue.length && this.inFlight.size < MAX_IN_FLIGHT) {
      const w = this.queue.shift(), key = `${w.tx},${w.tz}`;
      if (this.inFlight.get(key) === specKey(w) || this.tiles.has(key)) continue;
      this.inFlight.set(key, specKey(w));
      this.worker.postMessage({ key, spec: specKey(w), tx: w.tx, tz: w.tz, segments: w.segments, objects: Boolean(w.objects || w.farTrees), far: !w.objects, maxTrees: this.shared.maxTrees });
    }
  }

  // A tile from the worker: kept only if it is still wanted with the same detail.
  _built({ key, spec, geometry, objects }) {
    if (this.inFlight.get(key) === spec) this.inFlight.delete(key);
    const w = this.wanted.get(key);
    if (w && specKey(w) === spec && !this.tiles.has(key)) this._add(w, geometry, objects);
    this._pump();
  }

  _add(w, geometryData, objectsData) {
    const geometry = tileGeometry(geometryData);
    const mesh = new THREE.Mesh(geometry, this.material);
    if (geometry.userData.hasWater) {
      // Water only where this tile has lakes; elsewhere the land fallback shows through gaps.
      const water = new THREE.Mesh(WATER_QUAD, WATER);
      water.position.set((w.tx + 0.5) * TILE_SIZE_M, WATER_LEVEL_M, (w.tz + 0.5) * TILE_SIZE_M);
      mesh.add(water);
    }
    this.scene.add(mesh);
    const objects = objectsData ? tileObjects(objectsData, this.shared, !w.objects) : null;
    if (objects) this.scene.add(objects);
    this.tiles.set(`${w.tx},${w.tz}`, { mesh, objects, segments: w.segments, near: Boolean(w.objects), far: Boolean(w.farTrees) });
  }

  get pending() {
    return this.queue.length + this.inFlight.size;
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
