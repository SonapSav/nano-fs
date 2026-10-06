// Cumulus clouds: seeded fields of soft billboard puffs, streamed in 10 km tiles around the
// camera (3 x 3, the haze hides the rest). Visual only.
//
// Each tile is one instanced mesh (one draw call): every puff is a camera-facing quad with a
// soft round texture, lighter on top and greyer underneath. Cloud bases are around
// 3000-3600 ft with tops up to ~5000 ft, so the circuit (1000 ft) flies below them and the
// cruise task (5000 ft) at their tops. Amounts: clear, few, scattered, broken.

import * as THREE from "three";

export const CLOUD_TILE_M = 10000;
const CLOUDS_PER_TILE = { clear: 0, few: 2, scattered: 7, broken: 16 };
const BASE_M = 950; // ~3100 ft

// Small deterministic PRNG (mulberry32) and a tile hash: the same seed and tile always give
// the same clouds, so a replay shows the sky of the live flight.
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const tileSeed = (seed, tx, tz) => (Math.imul(tx, 73856093) ^ Math.imul(tz, 19349663) ^ Math.imul(seed + 1, 83492791)) >>> 0;

function puffTexture() {
  const size = 128, c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  const r = rng(172);
  // A few overlapping soft blobs: a lumpy, round puff with a fading edge.
  for (let k = 0; k < 7; k++) {
    const x = size / 2 + (r() - 0.5) * 30, y = size / 2 + (r() - 0.5) * 30, rad = 30 + r() * 26;
    const grad = g.createRadialGradient(x, y, 0, x, y, rad);
    grad.addColorStop(0, "rgba(255,255,255,0.7)");
    grad.addColorStop(0.6, "rgba(255,255,255,0.32)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Clouds of one tile: [x, y, z, radius, shade] per puff.
export function tilePuffs(seed, tx, tz, amount) {
  const r = rng(tileSeed(seed, tx, tz));
  const puffs = [];
  for (let c = 0; c < CLOUDS_PER_TILE[amount]; c++) {
    const cx = (tx + r()) * CLOUD_TILE_M, cz = (tz + r()) * CLOUD_TILE_M;
    const base = BASE_M + r() * 200, width = 500 + r() * 900, height = 250 + r() * 300 + width * 0.25;
    const n = 10 + Math.floor(r() * 14);
    for (let p = 0; p < n; p++) {
      const u = r(), v = r(), w = r();
      const x = cx + (u - 0.5) * width, z = cz + (v - 0.5) * width * 0.8;
      const up = Math.pow(w, 1.5) * height * (1 - Math.abs(u - 0.5)); // taller in the middle
      const radius = 170 + r() * 230;
      // Shade: white sunlit tops, light grey bases (not tone mapped: these are display colours).
      puffs.push([x, base + radius * 0.4 + up, z, radius, 0.8 + 0.2 * Math.min(1, up / height + 0.1)]);
    }
  }
  return puffs;
}

function billboard(material) {
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader.replace(
      "#include <project_vertex>",
      `vec4 mvPosition = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
mvPosition.xy += position.xy * length(instanceMatrix[0].xyz);
gl_Position = projectionMatrix * mvPosition;`,
    );
  };
  return material;
}

export class CloudField {
  constructor(scene) {
    this.scene = scene;
    this.material = billboard(new THREE.MeshBasicMaterial({ map: puffTexture(), transparent: true, depthWrite: false, fog: true, toneMapped: false }));
    this.quad = new THREE.PlaneGeometry(2, 2);
    this.tiles = new Map();
    this.seed = 0;
    this.amount = "few";
    this.centre = null;
  }

  // Cloud amount and layout seed; rebuilds the tiles when either changes.
  set(amount, seed) {
    amount = amount in CLOUDS_PER_TILE ? amount : "few";
    if (amount === this.amount && seed === this.seed) return;
    this.amount = amount;
    this.seed = seed;
    for (const key of [...this.tiles.keys()]) this._drop(key);
    this.centre = null;
  }

  // Tint for the time of day (sunlit colour of the puffs).
  setTint(color) {
    this.material.color.set(color);
  }

  update(x, z) {
    const cx = Math.floor(x / CLOUD_TILE_M), cz = Math.floor(z / CLOUD_TILE_M);
    if (this.centre && this.centre[0] === cx && this.centre[1] === cz) return;
    this.centre = [cx, cz];
    const wanted = new Set();
    for (let dz = -1; dz <= 1; dz++) for (let dx = -1; dx <= 1; dx++) wanted.add(`${cx + dx},${cz + dz}`);
    for (const key of [...this.tiles.keys()]) if (!wanted.has(key)) this._drop(key);
    for (const key of wanted) {
      if (this.tiles.has(key)) continue;
      const [tx, tz] = key.split(",").map(Number);
      const puffs = tilePuffs(this.seed, tx, tz, this.amount);
      if (!puffs.length) {
        this.tiles.set(key, null);
        continue;
      }
      const mesh = new THREE.InstancedMesh(this.quad, this.material, puffs.length);
      const m = new THREE.Matrix4(), col = new THREE.Color();
      puffs.forEach(([px, py, pz, radius, shade], i) => {
        mesh.setMatrixAt(i, m.makeScale(radius, radius, radius).setPosition(px, py, pz));
        mesh.setColorAt(i, col.setScalar(shade));
      });
      mesh.frustumCulled = false; // billboards: the instance bounds do not cover the quads
      this.scene.add(mesh);
      this.tiles.set(key, mesh);
    }
  }

  _drop(key) {
    const mesh = this.tiles.get(key);
    if (mesh) {
      this.scene.remove(mesh);
      mesh.dispose();
    }
    this.tiles.delete(key);
  }
}
