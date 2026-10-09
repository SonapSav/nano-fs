// Shared parts of the landmark models (landmarks.js): physically based materials (lit by
// the sun, the sky light and an environment map of the sky; Landmarks.setEnvironment),
// facade patterns drawn in shaders, and geometry helpers. World frame: x east, y up,
// z south. Visual only.
//
// Facade patterns read a per-vertex "facade" attribute (walls(), below): (along the wall m,
// up from the wall's foot m, the wall's height m, bay m). Every pattern fades to its
// average colour where its detail would be smaller than a few pixels, and darkens the
// wall's foot a little (contact shading the lighting has no other way to show).

import * as THREE from "three";

// --- Geometry -------------------------------------------------------------------------------

// Unit vector of a bearing (degrees from north, clockwise) in the world frame: [x, z].
export const along = (deg) => [Math.sin((deg * Math.PI) / 180), -Math.cos((deg * Math.PI) / 180)];

// Signed area of a ring [x0, z0, ...] (> 0: counter-clockwise in (x, z)).
export function ringArea(r) {
  let a = 0;
  for (let k = 0, n = r.length / 2; k < n; k++) {
    const j = (k + 1) % n;
    a += r[2 * k] * r[2 * j + 1] - r[2 * j] * r[2 * k + 1];
  }
  return a / 2;
}

export function ringCentre(r) {
  let x = 0, z = 0;
  for (let k = 0; k < r.length; k += 2) [x, z] = [x + r[k], z + r[k + 1]];
  return [x / (r.length / 2), z / (r.length / 2)];
}

// The oriented box of a ring along its longest edge: centre, unit axes, half extents.
export function orientedBox(r) {
  const n = r.length / 2;
  let best = 0, ux = 1, uz = 0;
  for (let k = 0; k < n; k++) {
    const j = (k + 1) % n, dx = r[2 * j] - r[2 * k], dz = r[2 * j + 1] - r[2 * k + 1], len = Math.hypot(dx, dz);
    if (len > best) [best, ux, uz] = [len, dx / len, dz / len];
  }
  const vx = -uz, vz = ux;
  let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
  for (let k = 0; k < n; k++) {
    const a = r[2 * k] * ux + r[2 * k + 1] * uz, b = r[2 * k] * vx + r[2 * k + 1] * vz;
    [a0, a1, b0, b1] = [Math.min(a0, a), Math.max(a1, a), Math.min(b0, b), Math.max(b1, b)];
  }
  const ac = (a0 + a1) / 2, bc = (b0 + b1) / 2;
  return { cx: ux * ac + vx * bc, cz: uz * ac + vz * bc, ux, uz, vx, vz, ha: (a1 - a0) / 2, hb: (b1 - b0) / 2 };
}

// Point in polygon (even-odd), ring [x0, z0, ...].
export function inside(x, z, r) {
  let ins = false;
  for (let k = 0, n = r.length / 2; k < n; k++) {
    const j = (k + 1) % n, x0 = r[2 * k], z0 = r[2 * k + 1], x1 = r[2 * j], z1 = r[2 * j + 1];
    if (z0 > z !== z1 > z && x < x0 + ((z - z0) * (x1 - x0)) / (z1 - z0)) ins = !ins;
  }
  return ins;
}

// A regular n-gon ring of radius r around (x, z), rotated by `rot` radians.
export function ngon(x, z, r, n, rot = 0) {
  const out = [];
  for (let k = 0; k < n; k++) out.push(x + r * Math.cos(rot + (2 * Math.PI * k) / n), z + r * Math.sin(rot + (2 * Math.PI * k) / n));
  return out;
}

// A ring as a three.js Shape (or Path, for holes) in the (x, -z) plane of ShapeGeometry.
export function toShape(r, C = THREE.Shape) {
  const s = new C();
  for (let k = 0; k < r.length; k += 2) (k ? s.lineTo(r[k], -r[k + 1]) : s.moveTo(r[k], -r[k + 1]));
  return s;
}

// A flat, upward-facing polygon (with holes) at height y, shifted by (dx, dz).
export function flat(ring, holes, y, dx = 0, dz = 0) {
  const shape = toShape(ring);
  for (const h of holes ?? []) shape.holes.push(toShape(h, THREE.Path));
  return new THREE.ShapeGeometry(shape).rotateX(-Math.PI / 2).translate(dx, y, dz);
}

// Walls along a ring from y0 to y1 as non-indexed triangles with the facade attribute.
// `out`: normals away from the ring's inside (an outer wall), else into it (a courtyard).
// `offset(y)` -> [dx, dz]: a leaning or curving wall, sampled every `rowM` metres. `mode`
// goes in the attribute's third component's sign (patterns read it: < 0 windows centred
// in the height, for drums and minarets).
export function walls(ring, y0, y1, bay, { out = true, offset = null, rowM = 1e9, centred = false } = {}) {
  let r = ring;
  if (ringArea(r) > 0 !== out) {
    r = [];
    for (let k = ring.length - 2; k >= 0; k -= 2) r.push(ring[k], ring[k + 1]);
  }
  const rows = Math.max(1, Math.ceil((y1 - y0) / rowM));
  const off = (y) => (offset ? offset(y) : [0, 0]);
  const pos = [], nrm = [], fac = [], H = (y1 - y0) * (centred ? -1 : 1);
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), e = new THREE.Vector3(), u = new THREE.Vector3(), n = new THREE.Vector3();
  let s = 0;
  for (let k = 0, cnt = r.length / 2; k < cnt; k++) {
    const j = (k + 1) % cnt, ax = r[2 * k], az = r[2 * k + 1], bx = r[2 * j], bz = r[2 * j + 1];
    const len = Math.hypot(bx - ax, bz - az);
    if (len < 0.01) continue;
    for (let i = 0; i < rows; i++) {
      const ya = y0 + ((y1 - y0) * i) / rows, yb = y0 + ((y1 - y0) * (i + 1)) / rows;
      const [oa, ob] = [off(ya), off(yb)];
      // Corners: a (start, low), b (end, low), c (start, high), d (end, high).
      a.set(ax + oa[0], ya, az + oa[1]);
      b.set(bx + oa[0], ya, bz + oa[1]);
      c.set(ax + ob[0], yb, az + ob[1]);
      e.subVectors(b, a);
      u.subVectors(c, a);
      n.crossVectors(u, e).normalize(); // (dz, 0, -dx) for an upright wall: outward on a counter-clockwise ring
      const ta = ya - y0, tb = yb - y0;
      const v = [[a.x, a.y, a.z, s, ta], [c.x, c.y, c.z, s, tb], [b.x, b.y, b.z, s + len, ta],
                 [b.x, b.y, b.z, s + len, ta], [c.x, c.y, c.z, s, tb], [bx + ob[0], yb, bz + ob[1], s + len, tb]];  // prettier-ignore
      for (const [x, y, z, sa, ua] of v) {
        pos.push(x, y, z);
        nrm.push(n.x, n.y, n.z);
        fac.push(sa, ua, H, bay);
      }
    }
    s += len;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(nrm, 3));
  g.setAttribute("facade", new THREE.Float32BufferAttribute(fac, 4));
  return g;
}

// One geometry from several (position, normal and, when any has it, facade).
export function merge(geos) {
  const parts = geos.map((g) => (g.index ? g.toNonIndexed() : g));
  const withFacade = parts.some((g) => g.attributes.facade);
  const count = parts.reduce((t, g) => t + g.attributes.position.count, 0);
  const pos = new Float32Array(count * 3), nrm = new Float32Array(count * 3), fac = withFacade ? new Float32Array(count * 4) : null;
  let o = 0;
  for (const g of parts) {
    pos.set(g.attributes.position.array, o * 3);
    nrm.set(g.attributes.normal.array, o * 3);
    if (fac && g.attributes.facade) fac.set(g.attributes.facade.array, o * 4);
    o += g.attributes.position.count;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
  if (fac) g.setAttribute("facade", new THREE.BufferAttribute(fac, 4));
  for (const p of parts) p.dispose();
  for (const p of geos) p.dispose();
  return g;
}

// A geometry moved to (x, y, z) and scaled (non-uniformly when sy, sz are given).
export const at = (g, x, y, z, sx, sy = sx, sz = sx) => g.applyMatrix4(new THREE.Matrix4().makeTranslation(x, y, z).multiply(new THREE.Matrix4().makeScale(sx, sy, sz)));

// A mesh that casts and receives the sun's shadow.
export function mesh(geo, material, { cast = true, receive = true } = {}) {
  const m = new THREE.Mesh(geo, material);
  m.castShadow = cast;
  m.receiveShadow = receive;
  return m;
}

// Dome profiles of unit radius and height (base at 0, tip at 1): an onion (the Grand
// Mosque's "onion-shaped crowns") and a classic raised dome. Shapes are project choices.
export const ONION = [[1.0, 0], [1.06, 0.1], [1.1, 0.22], [1.09, 0.33], [1.02, 0.45], [0.88, 0.57], [0.68, 0.69], [0.46, 0.8], [0.26, 0.89], [0.1, 0.96], [0.0, 1.0]].map(([r, y]) => new THREE.Vector2(r, y));  // prettier-ignore
export const CLASSIC = Array.from({ length: 13 }, (_, i) => {
  const t = (i / 12) * (Math.PI / 2);
  return new THREE.Vector2(Math.cos(t) * (1 - 0.06 * Math.sin(2 * t)), Math.sin(t) ** 0.92);
});

// Crescent finial of unit height: spire, ball and an upright crescent opening upward.
export function finialParts() {
  const spire = new THREE.CylinderGeometry(0.04, 0.07, 0.62, 8).translate(0, 0.31, 0);
  const ball = new THREE.SphereGeometry(0.1, 12, 8).translate(0, 0.42, 0);
  const crescent = new THREE.TorusGeometry(0.2, 0.045, 6, 20, 1.5 * Math.PI).rotateZ(0.75 * Math.PI).translate(0, 0.8, 0);
  return [spire, ball, crescent];
}

// A spire finial of unit height (ball and point).
export function spireParts() {
  return [new THREE.SphereGeometry(0.12, 12, 8).translate(0, 0.25, 0), new THREE.ConeGeometry(0.07, 0.75, 8).translate(0, 0.62, 0)];
}

// --- Materials --------------------------------------------------------------------------------

const NOISE = `
float lmHash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float lmNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(lmHash(i), lmHash(i + vec2(1.0, 0.0)), f.x), mix(lmHash(i + vec2(0.0, 1.0)), lmHash(i + vec2(1.0, 1.0)), f.x), f.y);
}`;

// A standard material with GLSL added: `head` (fragment declarations), `color` (after the
// diffuse colour, may change diffuseColor), `rough` / `metal` (after their chunks),
// `normal` (after the normal maps; may change `normal`, view space). The fragment has
// vLmWorld (world position) and, with `facade`, vFacade.
export function patched(params, { key, facade = false, uniforms = {}, head = "", color = "", rough = "", metal = "", normal = "" }) {
  const m = new THREE.MeshStandardMaterial(params);
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", `#include <common>\nvarying vec3 vLmWorld;${facade ? "\nattribute vec4 facade;\nvarying vec4 vFacade;" : ""}`)
      .replace("#include <begin_vertex>", `#include <begin_vertex>\nvLmWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;${facade ? "\nvFacade = facade;" : ""}`);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\nvarying vec3 vLmWorld;${facade ? "\nvarying vec4 vFacade;" : ""}\n${NOISE}\n${head}`)
      .replace("#include <color_fragment>", `#include <color_fragment>\n${color}`)
      .replace("#include <roughnessmap_fragment>", `#include <roughnessmap_fragment>\n${rough}`)
      .replace("#include <metalnessmap_fragment>", `#include <metalnessmap_fragment>\n${metal}`)
      .replace("#include <normal_fragment_maps>", `#include <normal_fragment_maps>\n${normal}`);
  };
  m.customProgramCacheKey = () => `landmark-${key}`;
  return m;
}

// Stone: slow brightness variation (veining, weathering) and faint cladding joints on
// walls (panels `jointM` wide, 2 m high), fading with distance.
const STONE = (jointM) => `
  diffuseColor.rgb *= 0.95 + 0.08 * lmNoise(vLmWorld.xz * 0.11 + vLmWorld.y * 0.07) + 0.03 * lmNoise(vLmWorld.xz * 1.3 + vLmWorld.y);
  #ifdef LM_FACADE
  {
    float jd = 1.0 - smoothstep(0.03, 0.12, max(fwidth(vFacade.x), fwidth(vFacade.y)));
    vec2 jp = vec2(vFacade.x / ${jointM.toFixed(2)}, vFacade.y / 2.0);
    vec2 jw = fwidth(jp) * 0.8;
    float joint = max(1.0 - smoothstep(0.0, jw.x, abs(fract(jp.x + 0.5) - 0.5)), 1.0 - smoothstep(0.0, jw.y, abs(fract(jp.y + 0.5) - 0.5)));
    diffuseColor.rgb *= 1.0 - 0.07 * joint * jd;
    diffuseColor.rgb *= mix(0.74, 1.0, smoothstep(0.0, 3.5, vFacade.y)); // contact shading at the foot
  }
  #endif`;

export const stone = (color, { roughness = 0.5, envMapIntensity = 0.3, jointM = 1.2, key = "stone" } = {}) =>
  patched({ color, roughness, metalness: 0, envMapIntensity }, { key, color: STONE(jointM) });

// Gold (gilding, gold-glass mosaic): metallic, sharp reflections of the sky.
export const gold = () => new THREE.MeshStandardMaterial({ color: 0xe0b85a, roughness: 0.28, metalness: 1, envMapIntensity: 1.1 });

// Brushed stainless steel and aluminium.
export const steel = (side = THREE.FrontSide) => new THREE.MeshStandardMaterial({ color: 0xc9ccd0, roughness: 0.32, metalness: 1, envMapIntensity: 1.0, side });

// Water in a pool: dark, glossy, with small static ripples so the sky's reflection breaks up.
export const poolWater = () =>
  patched(
    { color: 0x0b2630, roughness: 0.05, metalness: 0, envMapIntensity: 0.9, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 },
    {
      key: "pool",
      normal: `{
  vec2 p = vLmWorld.xz;
  float a = 0.014 * (1.0 - smoothstep(0.05, 0.35, fwidth(p.x))); // none where the ripples would sparkle
  vec3 wn = normalize(vec3(a * (sin(p.x * 1.7 + p.y * 0.6) + 0.6 * sin(p.y * 3.1 - p.x * 1.2)), 1.0, a * (cos(p.y * 1.9 - p.x * 0.8) + 0.6 * cos(p.x * 2.9 + p.y * 0.4))));
  normal = normalize((viewMatrix * vec4(wn, 0.0)).xyz);
}`,
    },
  );

// Facade patterns: GLSL returning 1 inside an opening (window, arch) and 0 on the wall.
const PATTERNS = {
  // Pointed (equilateral) arches, one per bay; tall arcades from near the foot, or windows
  // centred in the height when the attribute's height is negative.
  arch: `
float lmOpening(vec4 f) {
  float bay = f.w, H = abs(f.z);
  if (bay <= 0.0 || H <= 0.0) return 0.0;
  float w = 0.6 * bay;
  float tall = min(3.2 * w, 0.66 * H);
  float sill = f.z > 0.0 ? 0.12 * H : 0.5 * (H - tall);
  float apex = sill + tall;
  float hs = apex - 0.866 * w;
  if (hs < sill) { w = (apex - sill) / 0.866; hs = sill; }
  float u = mod(f.x, bay) - 0.5 * bay, v = f.y;
  if (abs(u) > 0.5 * w || v < sill || v > apex) return 0.0;
  if (v < hs) return 1.0;
  return (length(vec2(u - 0.5 * w, v - hs)) < w && length(vec2(u + 0.5 * w, v - hs)) < w) ? 1.0 : 0.0;
}`,
  // Storeys of round-arched windows (uStorey metres apart), none in the parapet.
  storeys: `
uniform float uStorey;
float lmOpening(vec4 f) {
  float bay = f.w;
  if (bay <= 0.0 || f.y > abs(f.z) - 1.8) return 0.0;
  float u = mod(f.x, bay) - 0.5 * bay;
  float v = mod(f.y, uStorey);
  float w = 0.42 * bay, sill = 0.24 * uStorey, spring = 0.66 * uStorey;
  if (abs(u) > 0.5 * w || v < sill) return 0.0;
  if (v < spring) return 1.0;
  return length(vec2(u, v - spring)) < 0.5 * w ? 1.0 : 0.0;
}`,
};

// Masonry walls with openings: `pattern` from PATTERNS; openings dark and matt (arcades:
// shade inside) or dark and glossy (`glazed`: windows reflecting the sky).
export function facadeStone(color, { pattern = "arch", glazed = false, storeyM = 4, jointM = 1.2, envMapIntensity = 0.3, roughness = 0.45 } = {}) {
  const m = patched(
    { color, roughness, metalness: 0, envMapIntensity },
    {
      key: `facade-${pattern}-${glazed}`,
      facade: true,
      uniforms: { uStorey: { value: storeyM } },
      head: `#define LM_FACADE\n${PATTERNS[pattern]}`,
      color: `${STONE(jointM)}
float lmDetail = 1.0 - smoothstep(0.06, 0.2, max(fwidth(vFacade.x), fwidth(vFacade.y)) / max(vFacade.w, 0.01));
float lmOpen = vFacade.w > 0.0 ? mix(${pattern === "arch" ? "0.3" : "0.18"}, lmOpening(vFacade), lmDetail) : 0.0;
float lmH = abs(vFacade.z);
diffuseColor.rgb = mix(diffuseColor.rgb, ${glazed ? "vec3(0.05, 0.065, 0.08)" : "vec3(0.15, 0.16, 0.18) + 0.1 * (1.0 - vFacade.y / max(lmH, 1.0))"}, lmOpen);
diffuseColor.rgb *= 1.0 - 0.22 * lmDetail * step(lmH - 2.6, vFacade.y) * step(vFacade.y, lmH - 1.8) * step(0.0, vFacade.z);`,
      rough: `roughnessFactor = mix(roughnessFactor, ${glazed ? "0.06" : "1.0"}, lmOpen);`,
    },
  );
  return m;
}

// Glass curtain walls, mirror-like (coated glass), with a structure drawn over them:
// "diagrid" (diamonds `bay` wide and uDiamond high, steel members ~0.6 m) or "curtain"
// (mullions every `bay` metres, a spandrel band every uStorey).
export function facadeGlass(kind, { color = 0x52707c, storeyM = 4, diamondM = 9 } = {}) {
  const body =
    kind === "diagrid"
      ? `vec2 dp = vec2(vFacade.x / vFacade.w, vFacade.y / uDiamond);
float d1 = abs(fract(dp.x + dp.y + 0.5) - 0.5), d2 = abs(fract(dp.x - dp.y + 0.5) - 0.5);
float lw = 0.3 / vFacade.w + fwidth(dp.x) * 0.7;
float lmFrame = max(1.0 - smoothstep(lw * 0.6, lw, d1), 1.0 - smoothstep(lw * 0.6, lw, d2));
float lmAvg = 0.12;`
      : `float mx = abs(fract(vFacade.x / vFacade.w + 0.5) - 0.5) * vFacade.w;
float my = mod(vFacade.y, uStorey);
float lmFrame = max(1.0 - smoothstep(0.06, 0.06 + fwidth(vFacade.x), mx), step(uStorey - 0.9, my));
float lmAvg = 0.28;`;
  return patched(
    { color, roughness: 0.06, metalness: 0.65, envMapIntensity: 1.0 },
    {
      key: `glass-${kind}`,
      facade: true,
      uniforms: { uStorey: { value: storeyM }, uDiamond: { value: diamondM } },
      head: "uniform float uStorey;\nuniform float uDiamond;",
      color: `${body}
float lmDetail = 1.0 - smoothstep(0.08, 0.3, max(fwidth(vFacade.x), fwidth(vFacade.y)) / max(vFacade.w, 0.01));
lmFrame = mix(lmAvg, lmFrame, lmDetail);
diffuseColor.rgb *= 0.9 + 0.2 * lmHash(floor(vec2(vFacade.x / 1.5, vFacade.y / uStorey))); // panels not quite alike
diffuseColor.rgb = mix(diffuseColor.rgb, vec3(${kind === "diagrid" ? "0.55, 0.57, 0.6" : "0.12, 0.14, 0.16"}), lmFrame);`,
      rough: "roughnessFactor = mix(roughnessFactor, 0.45, lmFrame);",
      metal: "metalnessFactor = mix(metalnessFactor, 0.3, lmFrame);",
    },
  );
}
