// Cumulus clouds: seeded fields of soft billboard puffs, streamed in 10 km tiles around the
// camera (3 x 3, the haze hides the rest). Visual only.
//
// Each tile is one instanced mesh (one draw call): every puff is a camera-facing quad whose
// outline is gradient noise (as the sky's own cloud layer, three's Sky addon, so near and far
// clouds look alike: lumpy, each puff different, billowing slowly), lit as a sphere from the
// sun's direction (a bright side, a shaded side, a darker base), with a bright rim looking
// toward the sun; lighter on top of the cloud and greyer underneath, and thinning out when
// the camera comes very close. Cloud bases are around
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

// Gradient noise and fbm: three's Sky addon (vendor/addons/objects/Sky.js, MIT), so the
// puffs share the sky layer's look.
const NOISE_GLSL = `
vec2 cloudGradient(vec2 i) {
  vec3 p = fract(i.xyx * vec3(0.1031, 0.1030, 0.0973));
  p += dot(p, p.yzx + 33.33);
  return fract((p.xx + p.yz) * p.zy) * 2.0 - 1.0;
}
float cloudNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  float a = dot(cloudGradient(i), f);
  float b = dot(cloudGradient(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0));
  float c = dot(cloudGradient(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0));
  float d = dot(cloudGradient(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y) * 1.6;
}
float cloudFbm(vec2 p, float drift) {
  float r = 0.0, amp = 1.0;
  for (int i = 0; i < 4; i++) {
    r += amp * cloudNoise(p);
    amp *= 0.5;
    p = p * 2.0 + drift;
  }
  return r;
}`;

// Shape and light (project choices, by eye): the puff's edge where the disc's falloff plus
// noise crosses 0; ambient and direct shares of the sunlit colour.
export const PUFF = { noise: 0.55, scale: 1.6, drift: 0.004, ambient: 0.74, direct: 0.5, base: 0.76, rim: 0.35 };

function puffMaterial() {
  const material = new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false, fog: true, toneMapped: false });
  material.userData.uniforms = { cloudSun: { value: new THREE.Vector3(0, 1, 0) }, cloudTime: { value: 0 } };
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, material.userData.uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nuniform vec3 cloudSun;\nvarying vec2 vPuff;\nvarying float vSeed;\nvarying vec3 vSunV;\nvarying vec3 vUpV;\nvarying float vNear;")
      .replace(
        "#include <project_vertex>",
        `vec4 mvPosition = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
float puffR = length(instanceMatrix[0].xyz);
mvPosition.xy += position.xy * puffR;
gl_Position = projectionMatrix * mvPosition;
vPuff = position.xy;
vSeed = fract(dot(instanceMatrix[3].xyz, vec3(0.00123, 0.00071, 0.00091))) * 97.0;
vSunV = normalize((viewMatrix * vec4(cloudSun, 0.0)).xyz);
vUpV = normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
vNear = smoothstep(0.6 * puffR, 1.8 * puffR, -mvPosition.z); // thinning out when very close`,
      );
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\nuniform float cloudTime;\nvarying vec2 vPuff;\nvarying float vSeed;\nvarying vec3 vSunV;\nvarying vec3 vUpV;\nvarying float vNear;\n${NOISE_GLSL}`)
      .replace(
        "#include <map_fragment>",
        `{
  float r2 = dot(vPuff, vPuff);
  if (r2 > 1.0) discard;
  float n = cloudFbm(vPuff * ${PUFF.scale} + vSeed, vSeed + cloudTime * ${PUFF.drift});
  float shape = (1.0 - r2) * 1.25 - 0.45 + ${PUFF.noise} * n;
  float a = smoothstep(0.0, 0.32, shape) * vNear;
  if (a < 0.01) discard;
  // A sphere's normal in view space, bent by the noise (lumps catch the light).
  vec3 nrm = normalize(vec3(vPuff + 0.35 * vec2(n, cloudNoise(vPuff * 3.1 + vSeed)), sqrt(max(0.0, 1.0 - r2)) + 0.25));
  float sun = clamp(dot(nrm, vSunV) * 0.5 + 0.5, 0.0, 1.0);
  float base = mix(${PUFF.base}, 1.0, smoothstep(-0.7, 0.35, dot(nrm, vUpV))); // darker underneath
  float rim = pow(clamp(-vSunV.z, 0.0, 1.0), 4.0) * (1.0 - smoothstep(0.0, 0.5, shape)) * ${PUFF.rim}; // looking toward the sun: bright edges
  float light = (${PUFF.ambient} + ${PUFF.direct} * sun) * base * (0.92 + 0.08 * n) + rim;
  diffuseColor = vec4(diffuseColor.rgb * light, a * diffuseColor.a);
}`,
      );
  };
  material.customProgramCacheKey = () => "cloud-puff";
  return material;
}

export class CloudField {
  constructor(scene) {
    this.scene = scene;
    this.material = puffMaterial();
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

  // The sun's (or moon's) direction (world, unit), lighting the puffs.
  setSun(dir) {
    this.material.userData.uniforms.cloudSun.value.copy(dir);
  }

  update(x, z) {
    this.material.userData.uniforms.cloudTime.value = (performance.now() / 1000) % 100000;
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
