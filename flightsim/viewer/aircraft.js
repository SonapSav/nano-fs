// Cessna 172P model for the viewer, built in code (no external assets).
//
// Dimensions: POH Section 1 (length 26 ft 11 in, height 8 ft 9.5 in, wing area 174 sq ft,
// 75 in two-blade fixed-pitch propeller, 11.25 in propeller ground clearance) and the
// JSBSim c172p geometry (gear contact points, wing reference point, eye point, span
// 35.8 ft). Shapes between those points (cross sections, planform, tail) are approximate.
//
// Parts are placed in the aircraft's structural frame (inches; x aft of the firewall,
// y right, z up) and converted to body axes (metres; x forward, y right, z down, origin
// at the CG), the frame the scene's aircraft matrix expects.
//
// update(row) moves the control surfaces to their logged positions (positive = trailing
// edge down for elevator, ailerons and flaps; trailing edge left for the rudder) and
// shows the propeller as a disc when it turns.

import * as THREE from "three";

const IN = 0.0254;
const CG = { x: 40.9, z: 36.6 }; // structural position of the body origin (matches EYE_BODY in scene.js)
const PROP_AXIS_Z = 29.25; // nose tyre bottom (-19.5) + 11.25 in clearance + 37.5 in radius
const DIHEDRAL = Math.tan((1.73 * Math.PI) / 180);
const BEACON_PERIOD_S = 60 / 45;
const BEACON_FLASH_S = 0.15;

export const toBody = (x, y, z) => new THREE.Vector3((CG.x - x) * IN, y * IN, (CG.z - z) * IN);

// --- Materials --------------------------------------------------------------------------

const WHITE = 0xf2f2ee;
const STRIPE = 0x1f3a6b; // dark blue cheat line
const STRIPE2 = 0xb3262e; // red pinstripe
const GLASS = 0x1b2630;
const REG_COLOUR = 0x1f2933;

export const REGISTRATION = "SX-PAN"; // painted on both sides of the rear fuselage
// Registration area on the tail cone (structural inches): letters about 10 in (25 cm)
// high, aft of where the cheat line has swept up, so they sit clear below it.
const REG = { x0: 160, x1: 218, z0: 25, z1: 35 };

function registrationTexture(text) {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = Math.round((1024 * (REG.z1 - REG.z0)) / (REG.x1 - REG.x0));
  const g = c.getContext("2d");
  g.fillStyle = "#fff";
  g.textAlign = "center";
  g.textBaseline = "middle";
  let size = c.height * 0.92; // fit the height, then shrink to fit the width
  g.font = `bold ${size}px "Helvetica Neue", Arial, sans-serif`;
  const w = g.measureText(text).width;
  if (w > c.width * 0.96) {
    size *= (c.width * 0.96) / w;
    g.font = `bold ${size}px "Helvetica Neue", Arial, sans-serif`;
  }
  g.fillText(text, c.width / 2, c.height / 2 + size * 0.04);
  const tex = new THREE.CanvasTexture(c);
  tex.anisotropy = 4;
  return tex;
}

function paintedMaterial(registration) {
  // Fuselage paint from structural coordinates: cheat line, pinstripe, windows, windscreen,
  // registration.
  const m = new THREE.MeshLambertMaterial({ color: WHITE, side: THREE.DoubleSide });
  m.onBeforeCompile = (shader) => {
    shader.uniforms.regTex = { value: registrationTexture(registration) }; // created on first render (needs a document)
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec4 aPaint;\nvarying vec4 vPaint;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvPaint = aPaint;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>
varying vec4 vPaint; // structural x, y, z (inches) and ring position (0 bottom, 0.5 top)
uniform sampler2D regTex;
float box(vec2 p, vec2 lo, vec2 hi, float r) {
  vec2 c = (lo + hi) * 0.5, h = (hi - lo) * 0.5 - r;
  vec2 d = abs(p - c) - h;
  return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - r;
}`)
      .replace("#include <color_fragment>", `#include <color_fragment>
{
  float x = vPaint.x, ay = abs(vPaint.y), z = vPaint.z, a = vPaint.w;
  float aa = fwidth(a) + 1e-4, ax = fwidth(x) + 1e-3;
  // Cheat line rising toward the tail, with a red pinstripe above it.
  float lift = 0.12 * smoothstep(105.0, 200.0, x);
  float band = smoothstep(0.205 + lift - aa, 0.205 + lift, a) - smoothstep(0.245 + lift, 0.245 + lift + aa, a);
  float pin = smoothstep(0.255 + lift - aa, 0.255 + lift, a) - smoothstep(0.264 + lift, 0.264 + lift + aa, a);
  float tail = step(-34.0, x);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(${new THREE.Color(STRIPE).toArray().join(",")}), band * tail);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(${new THREE.Color(STRIPE2).toArray().join(",")}), pin * tail);
  // Windows: door windows split by the door post, rear side window, windscreen, rear top window.
  float side = smoothstep(14.0, 16.0, ay);
  float w = 1e9;
  w = min(w, box(vec2(x, z), vec2(31.0, 46.0), vec2(64.0, 59.5), 3.0));
  w = min(w, box(vec2(x, z), vec2(68.0, 46.0), vec2(98.0, 59.5), 3.0));
  w = min(w, box(vec2(x, z), vec2(102.0, 48.0), vec2(118.0, 57.5), 4.0));
  float glass = (1.0 - smoothstep(-ax, ax, w)) * side;
  float screen = smoothstep(8.0, 9.5, x) * (1.0 - smoothstep(28.5, 30.0, x)) * smoothstep(45.5, 47.0, z);
  float rear = smoothstep(93.0, 95.0, x) * (1.0 - smoothstep(126.0, 128.0, x)) * (1.0 - smoothstep(12.0, 14.0, ay)) * smoothstep(51.0, 52.5, z);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(${new THREE.Color(GLASS).toArray().join(",")}), clamp(glass + screen + rear, 0.0, 1.0));
  // Registration: reads left to right for a viewer on either side (nose to tail on the
  // left side, tail to nose on the right).
  if (x > ${REG.x0.toFixed(1)} && x < ${REG.x1.toFixed(1)} && z > ${REG.z0.toFixed(1)} && z < ${REG.z1.toFixed(1)} && ay > 2.0) {
    float u = (x - ${REG.x0.toFixed(1)}) / ${(REG.x1 - REG.x0).toFixed(1)};
    if (vPaint.y > 0.0) u = 1.0 - u;
    float ink = texture2D(regTex, vec2(u, (z - ${REG.z0.toFixed(1)}) / ${(REG.z1 - REG.z0).toFixed(1)})).r;
    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(${new THREE.Color(REG_COLOUR).toArray().join(",")}), ink);
  }
}`);
  };
  return m;
}

// Greek flag on both sides of the fin (structural inches, 2:3): nine equal stripes, blue
// first, and a blue canton five stripes high with a white cross one stripe wide. Both
// sides read as the flag (canton at the viewer's upper left): forward on the left side,
// aft on the right. Painted on the fixed fin, clear of the leading edge and the rudder hinge.
const FLAG = { x0: 235, x1: 254.5, z0: 57, z1: 70 };
const FLAG_BLUE = 0x0d5eaf;

function finMaterial(material) {
  const m = material.clone();
  m.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vStruct;")
      .replace("#include <begin_vertex>", `#include <begin_vertex>
vStruct = vec3(${CG.x.toFixed(2)} - position.x / ${IN}, position.y / ${IN}, ${CG.z.toFixed(2)} - position.z / ${IN});`);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vStruct;")
      .replace("#include <color_fragment>", `#include <color_fragment>
{
  // Flag units: 13.5 wide (from the hoist, forward) by 9 high (from the top).
  float u = (vStruct.x - ${FLAG.x0.toFixed(1)}) / ${(FLAG.x1 - FLAG.x0).toFixed(2)} * 13.5;
  float v = (${FLAG.z1.toFixed(1)} - vStruct.z) / ${(FLAG.z1 - FLAG.z0).toFixed(2)} * 9.0;
  if (vStruct.y > 0.0) u = 13.5 - u; // right side: hoist aft
  if (u > 0.0 && u < 13.5 && v > 0.0 && v < 9.0 && abs(vStruct.y) > 0.3) {
    float blue = 1.0 - mod(floor(v), 2.0); // stripes: blue, white, ... blue
    if (u < 5.0 && v < 5.0) blue = (abs(u - 2.5) < 0.5 || abs(v - 2.5) < 0.5) ? 0.0 : 1.0; // canton
    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(${new THREE.Color(FLAG_BLUE).toArray().map((c) => c.toFixed(4)).join(",")}), blue);
  }
}`);
  };
  return m;
}

// --- Geometry helpers -------------------------------------------------------------------

function catmullRom(p0, p1, p2, p3, t) {
  const t2 = t * t, t3 = t2 * t;
  return 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3);
}

// Fuselage: smooth loft through stations {x, w (half width), zb, zt, n (squareness)}.
function fuselage(stations, material, steps = 6, ring = 48) {
  const rows = [];
  for (let i = 0; i < stations.length - 1; i++) {
    for (let s = 0; s < steps; s++) {
      const t = s / steps;
      const p = (k) => catmullRom(...[i - 1, i, i + 1, i + 2].map((j) => stations[Math.max(0, Math.min(stations.length - 1, j))][k]), t);
      rows.push({ x: p("x"), w: p("w"), zb: p("zb"), zt: p("zt"), n: p("n") });
    }
  }
  rows.push(stations[stations.length - 1]);
  const pos = [], paint = [], index = [];
  for (const r of rows) {
    const zc = (r.zt + r.zb) / 2, h = (r.zt - r.zb) / 2, e = 2 / r.n;
    for (let k = 0; k <= ring; k++) {
      const th = (k / ring) * 2 * Math.PI; // 0 bottom, pi/2 right, pi top
      const s = Math.sin(th), c = Math.cos(th);
      const y = r.w * Math.sign(s) * Math.abs(s) ** e, z = zc - h * Math.sign(c) * Math.abs(c) ** e;
      pos.push(...toBody(r.x, y, z).toArray());
      const a = k / ring;
      paint.push(r.x, y, z, a <= 0.5 ? a : 1 - a);
    }
  }
  for (let i = 0; i < rows.length - 1; i++) {
    for (let k = 0; k < ring; k++) {
      const a = i * (ring + 1) + k, b = a + ring + 1;
      index.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  // Close both ends with a fan around the centre.
  // (The end fans have their own vertices, so they do not bend the skin's normals.)
  for (const [i, flip] of [[0, true], [rows.length - 1, false]]) {
    const r = rows[i], c = pos.length / 3;
    pos.push(...toBody(r.x, 0, (r.zt + r.zb) / 2).toArray());
    paint.push(r.x, 0, (r.zt + r.zb) / 2, 0.25);
    for (let k = 0; k <= ring; k++) {
      const a = i * (ring + 1) + k;
      pos.push(pos[a * 3], pos[a * 3 + 1], pos[a * 3 + 2]);
      paint.push(paint[a * 4], paint[a * 4 + 1], paint[a * 4 + 2], paint[a * 4 + 3]);
    }
    for (let k = 0; k < ring; k++) index.push(...(flip ? [c, c + 2 + k, c + 1 + k] : [c, c + 1 + k, c + 2 + k]));
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("aPaint", new THREE.Float32BufferAttribute(paint, 4));
  g.setIndex(index);
  g.computeVertexNormals();
  return new THREE.Mesh(g, material);
}

// NACA 4-digit section: upper and lower surface at chord fraction f.
function naca(f, t, m, p) {
  const yt = 5 * t * (0.2969 * Math.sqrt(f) - 0.126 * f - 0.3516 * f ** 2 + 0.2843 * f ** 3 - 0.1036 * f ** 4);
  const yc = m === 0 ? 0 : f < p ? (m / p ** 2) * (2 * p * f - f * f) : (m / (1 - p) ** 2) * (1 - 2 * p + 2 * p * f - f * f);
  return [yc + yt, yc - yt];
}

// Lifting surface between chord fractions f0..f1, lofted through sections
// {le: [x, y, z], chord}; chord runs along +x (aft), thickness along `normal`.
// Returns geometry in body axes relative to `origin` (a structural point, e.g. a hinge).
function surface(sections, { f0 = 0, f1 = 1, t = 0.12, m = 0.02, p = 0.4, normal = [0, 0, 1], origin = null, n = 14 }) {
  const fs = [];
  for (let i = 0; i <= n; i++) fs.push(f0 + (f1 - f0) * (0.5 - 0.5 * Math.cos((Math.PI * i) / n)));
  const o = origin ? toBody(...origin) : new THREE.Vector3();
  const pos = [], index = [];
  const per = 2 * fs.length;
  for (const s of sections) {
    const pts = [...fs.map((f) => [f, 0]), ...[...fs].reverse().map((f) => [f, 1])];
    for (const [f, lower] of pts) {
      const off = naca(f, t, m, p)[lower] * s.chord;
      const x = s.le[0] + f * s.chord + normal[0] * off, y = s.le[1] + normal[1] * off, z = s.le[2] + normal[2] * off;
      pos.push(...toBody(x, y, z).sub(o).toArray());
    }
  }
  for (let i = 0; i < sections.length - 1; i++) {
    for (let k = 0; k < per - 1; k++) {
      const a = i * per + k, b = a + per;
      index.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  // End caps get their own vertices, so their (sideways) normals are not averaged into
  // the skin's: shared vertices made the skin near one tip shade as if curving inward.
  // Both caps are wound to face outward (away from the other end).
  for (const [i, outward] of [[0, false], [sections.length - 1, true]]) {
    const base = pos.length / 3;
    for (let k = 0; k < per; k++) pos.push(pos[(i * per + k) * 3], pos[(i * per + k) * 3 + 1], pos[(i * per + k) * 3 + 2]);
    for (let k = 0; k < fs.length - 1; k++) {
      // Quads between the upper and lower point at each chord station.
      const u0 = base + k, u1 = u0 + 1, l0 = base + per - 1 - k, l1 = l0 - 1;
      index.push(...(outward ? [u0, l0, u1, u1, l0, l1] : [u0, u1, l0, u1, l1, l0]));
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(index);
  g.computeVertexNormals();
  g.userData = { ringSize: per, sections: sections.length }; // skin vertices come first (tests)
  return g;
}

// A control surface on a pivot at its hinge line (from section 0 to the last section).
function hinged(sections, frac, opts, material, axisSign) {
  const a = sections[0], b = sections[sections.length - 1];
  const hingeA = [a.le[0] + frac * a.chord, a.le[1], a.le[2]];
  const hingeB = [b.le[0] + frac * b.chord, b.le[1], b.le[2]];
  const pivot = new THREE.Group();
  pivot.position.copy(toBody(...hingeA));
  pivot.add(new THREE.Mesh(surface(sections, { ...opts, f0: frac, f1: 1, origin: hingeA }), material));
  const axis = toBody(...hingeB).sub(toBody(...hingeA)).normalize();
  if (axis.dot(axisSign) < 0) axis.negate();
  pivot.userData.axis = axis;
  return pivot;
}

// Soft round glow for lights (computed, no canvas, so the model also builds outside a browser).
let glowTexture = null;
function glow() {
  if (!glowTexture) {
    const n = 64, data = new Uint8Array(n * n * 4);
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        const r = Math.hypot(i - n / 2 + 0.5, j - n / 2 + 0.5) / (n / 2);
        const a = Math.max(0, 1 - r) ** 2.2;
        data.set([255, 255, 255, Math.round(255 * a)], (j * n + i) * 4);
      }
    }
    glowTexture = new THREE.DataTexture(data, n, n);
    glowTexture.needsUpdate = true;
  }
  return glowTexture;
}

// A light: a half-sphere lens on a dark bezel, facing `outward` (body axes), with a glow.
function lamp(position, outward, colour, radiusIn, glowM, length = 1) {
  const g = new THREE.Group();
  g.position.copy(position);
  g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), outward.clone().normalize());
  const lens = new THREE.Mesh(
    new THREE.SphereGeometry(radiusIn * IN, 20, 10, 0, 2 * Math.PI, 0, Math.PI / 2), // dome along +y
    new THREE.MeshBasicMaterial({ color: colour }),
  );
  lens.scale.y = length; // > 1: a teardrop-like lens standing proud of the surface
  const bezel = new THREE.Mesh(new THREE.CylinderGeometry(radiusIn * 1.15 * IN, radiusIn * 1.15 * IN, 0.25 * IN, 20), new THREE.MeshLambertMaterial({ color: 0x3a3d40 }));
  bezel.position.y = -0.12 * IN;
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow(), color: colour, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false }));
  halo.scale.setScalar(glowM);
  halo.position.y = radiusIn * length * 0.6 * IN;
  g.add(lens, bezel, halo);
  g.userData = { lens, halo };
  return g;
}

function beam(a, b, width, thick, material) {
  // A strut or leg from structural point a to b; flat side faces sideways.
  const pa = toBody(...a), pb = toBody(...b);
  const len = pa.distanceTo(pb);
  const m = new THREE.Mesh(new THREE.BoxGeometry(width * IN, len, thick * IN), material);
  m.position.copy(pa).add(pb).multiplyScalar(0.5);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), pb.clone().sub(pa).normalize());
  return m;
}

function ellipsoid(center, rx, ry, rz, material) {
  const m = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 14), material);
  m.position.copy(toBody(...center));
  m.scale.set(rx * IN, ry * IN, rz * IN);
  return m;
}

function wheel(center, radius, width, material) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(radius * IN, radius * IN, width * IN, 20), material); // axis y (axle)
  m.position.copy(toBody(...center));
  return m;
}

// --- The aircraft -----------------------------------------------------------------------

export function buildC172({ registration = REGISTRATION } = {}) {
  const group = new THREE.Group();
  const white = new THREE.MeshLambertMaterial({ color: WHITE, side: THREE.DoubleSide });
  const grey = new THREE.MeshLambertMaterial({ color: 0x5c6166 });
  const dark = new THREE.MeshLambertMaterial({ color: 0x2a2d31 });
  const tyre = new THREE.MeshLambertMaterial({ color: 0x1c1c1c });
  const red = new THREE.MeshLambertMaterial({ color: STRIPE2 });

  // Fuselage: cowling, cabin under the wing, tail cone.
  group.add(fuselage([
    { x: -34, w: 14, zb: 17, zt: 40, n: 2.4 },
    { x: -27, w: 18, zb: 11, zt: 42, n: 2.8 },
    { x: -12, w: 20, zb: 7, zt: 43.5, n: 3.2 },
    { x: 0, w: 21, zb: 5, zt: 44.5, n: 3.4 },
    { x: 10, w: 21.5, zb: 4, zt: 47, n: 3.6 },
    { x: 20, w: 21.5, zb: 3.5, zt: 55, n: 3.6 },
    { x: 30, w: 21.5, zb: 3.5, zt: 61.5, n: 3.6 },
    { x: 60, w: 21.5, zb: 3.5, zt: 62.5, n: 3.6 },
    { x: 92, w: 21, zb: 4.5, zt: 62.5, n: 3.4 },
    { x: 118, w: 18.5, zb: 9, zt: 58, n: 3.0 },
    { x: 145, w: 14.5, zb: 14, zt: 50, n: 2.6 },
    { x: 175, w: 11, zb: 19, zt: 45, n: 2.4 },
    { x: 205, w: 8, zb: 23, zt: 41.5, n: 2.3 },
    { x: 235, w: 5.5, zb: 26, zt: 39, n: 2.2 },
    { x: 255, w: 3.8, zb: 28.5, zt: 37, n: 2.2 },
    { x: 272, w: 1.5, zb: 30.5, zt: 35.5, n: 2.0 },
  ], paintedMaterial(registration)));

  // Spinner (red) ahead of the cowling, on the thrust line.
  const spinnerProfile = [];
  for (let i = 0; i <= 12; i++) {
    const u = i / 12;
    spinnerProfile.push(new THREE.Vector2(7.2 * Math.sqrt(1 - u * u) * IN, u * 12 * IN));
  }
  const spinner = new THREE.Mesh(new THREE.LatheGeometry(spinnerProfile, 24), red);
  spinner.position.copy(toBody(-35, 0, PROP_AXIS_Z));
  spinner.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(1, 0, 0)); // lathe axis -> forward
  group.add(spinner);

  // Propeller: two blades, or a translucent disc when turning.
  const prop = new THREE.Group();
  prop.position.copy(toBody(-37, 0, PROP_AXIS_Z));
  for (const s of [1, -1]) {
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.8 * IN, 4.5 * IN, 34 * IN), dark);
    blade.position.set(0, 0, s * 20.5 * IN);
    blade.rotation.z = 0.35 * s;
    prop.add(blade);
    const tip = new THREE.Mesh(new THREE.BoxGeometry(0.85 * IN, 4.2 * IN, 3 * IN), red);
    tip.position.set(0, 0, s * 36 * IN);
    prop.add(tip);
  }
  group.add(prop);
  const disc = new THREE.Mesh(
    new THREE.CircleGeometry(37.5 * IN, 48),
    new THREE.MeshBasicMaterial({ color: 0x3a3d40, transparent: true, opacity: 0.16, depthWrite: false, side: THREE.DoubleSide }),
  );
  disc.position.copy(toBody(-37, 0, PROP_AXIS_Z));
  disc.rotation.y = Math.PI / 2; // circle faces forward
  disc.visible = false;
  group.add(disc);

  // Wing: constant-chord inner panels, tapered outer panels, 1.73 deg dihedral.
  const wingZ = (y) => 65 + Math.abs(y) * DIHEDRAL; // chord line; the lower surface sits on the cabin roof
  const wingLE = (y) => (Math.abs(y) <= 100 ? 26 : 26 + ((Math.abs(y) - 100) / 114) * 4);
  const wingChord = (y) => (Math.abs(y) <= 100 ? 64 : 64 - ((Math.abs(y) - 100) / 114) * 20);
  const ws = (y) => ({ le: [wingLE(y), y, wingZ(y)], chord: wingChord(y) });
  const span = (a, b, k = 3) => Array.from({ length: k + 1 }, (_, i) => ws(a + ((b - a) * i) / k));
  const wingOpts = { t: 0.12, m: 0.02, p: 0.4 };
  const pivots = { flapL: null, flapR: null, aileronL: null, aileronR: null, elevator: null, rudder: null };
  for (const side of [1, -1]) {
    const yy = (v) => side * v;
    group.add(new THREE.Mesh(surface(side > 0 ? span(0, 22, 1) : span(-22, 0, 1), wingOpts), white));
    group.add(new THREE.Mesh(surface(side > 0 ? span(22, 110, 1) : span(-110, -22, 1), { ...wingOpts, f1: 0.7 }), white));
    group.add(new THREE.Mesh(surface(side > 0 ? span(110, 113, 1) : span(-113, -110, 1), wingOpts), white));
    group.add(new THREE.Mesh(surface(side > 0 ? span(113, 196) : span(-196, -113), { ...wingOpts, f1: 0.75 }), white));
    group.add(new THREE.Mesh(surface(side > 0 ? span(196, 214, 2) : span(-214, -196, 2), wingOpts), white));
    const flap = hinged(side > 0 ? span(22, 110, 1) : span(-110, -22, 1), 0.7, wingOpts, white, new THREE.Vector3(0, 1, 0));
    const aileron = hinged(side > 0 ? span(113, 196) : span(-196, -113), 0.75, wingOpts, white, new THREE.Vector3(0, 1, 0));
    group.add(flap, aileron);
    pivots[side > 0 ? "flapR" : "flapL"] = flap;
    pivots[side > 0 ? "aileronR" : "aileronL"] = aileron;
    // Strut, main gear leg, wheel and fairing.
    group.add(beam([48, yy(20), 7], [45, yy(102), wingZ(102) - 3], 4.5, 1.6, white));
    group.add(beam([58, yy(18), 5], [58, yy(41), -7], 4, 1.2, grey));
    group.add(wheel([58.2, yy(43), -8], 7.5, 5, tyre));
    group.add(ellipsoid([60, yy(43), -6], 16, 5, 8.5, white));
    // Navigation lights on the wingtips at their thickest point (about 30% chord; the tip
    // is ~5 in thick), facing outward and a little forward: red left, green right.
    group.add(lamp(toBody(43, yy(214), wingZ(214) + 0.7), new THREE.Vector3(0.35, side, 0), side > 0 ? 0x2bff5a : 0xff2a2a, 1.3, 0.35, 1.8));
  }

  // Horizontal tail with elevator (symmetric section).
  const ht = (y) => ({ le: [222 + Math.abs(y) * 0.12, y, 31], chord: 52 - Math.abs(y) * 0.18 });
  const tailOpts = { t: 0.09, m: 0 };
  const htSpan = [-68, -34, 0, 34, 68].map(ht);
  group.add(new THREE.Mesh(surface(htSpan, { ...tailOpts, f1: 0.58 }), white));
  pivots.elevator = hinged(htSpan, 0.58, tailOpts, white, new THREE.Vector3(0, 1, 0));
  group.add(pivots.elevator);

  // Vertical fin with dorsal fillet and rudder (thickness along y).
  // Rudder trailing edge at x 277: with the spinner tip at -47 the length is the POH's 26 ft 11 in.
  const fin = [{ le: [205, 0, 37], chord: 72 }, { le: [226, 0, 62], chord: 51 }, { le: [246, 0, 87], chord: 31 }];
  const finOpts = { t: 0.09, m: 0, normal: [0, 1, 0] };
  group.add(new THREE.Mesh(surface(fin, { ...finOpts, f1: 0.62 }), finMaterial(white)));
  // Dorsal fillet: rooted inside the tail cone (whose top slopes down from z 50 to 41 over
  // x 145-205), its leading edge leaves the fuselage top near x 170 and runs into the fin's
  // leading edge; the trailing part overlaps the fin root, so there is no gap anywhere.
  group.add(new THREE.Mesh(surface([{ le: [140, 0, 38], chord: 78 }, { le: [172, 0, 46], chord: 41 }, { le: [210, 0, 52], chord: 10 }], { ...finOpts, t: 0.08 }), white));
  pivots.rudder = hinged(fin, 0.62, finOpts, white, new THREE.Vector3(0, 0, 1)); // +z body = down: + rotation moves the trailing edge left
  group.add(pivots.rudder);
  // Flashing red beacon on top of the fin; white position light at the tail facing aft.
  // (The fin tip is ~2.8 in thick and the tail cone ends ~3 in wide: lenses fit inside.)
  const beacon = lamp(toBody(255, 0, 87.1), new THREE.Vector3(0, 0, -1), 0xff3a2a, 1.1, 0.6, 1.4);
  group.add(beacon);
  group.add(lamp(toBody(272.3, 0, 33), new THREE.Vector3(-1, 0, 0), 0xffffff, 0.8, 0.3));

  // Nose gear: strut, wheel and fairing.
  group.add(beam([-4, 0, 10], [-6.8, 0, -13], 3, 3, grey));
  group.add(wheel([-6.8, 0, -14], 5.5, 4, tyre));
  group.add(ellipsoid([-5, 0, -12], 12, 4, 7, white));

  const q = new THREE.Quaternion();
  const set = (pivot, angle) => pivot.quaternion.copy(q.setFromAxisAngle(pivot.userData.axis, angle));
  let propAngle = 0;

  return {
    group,
    pivots,
    update(row, dtS = 0) {
      set(pivots.elevator, row.elevator_pos_rad ?? 0);
      set(pivots.aileronL, row.aileron_left_pos_rad ?? 0);
      set(pivots.aileronR, row.aileron_right_pos_rad ?? 0);
      set(pivots.flapL, row.flap_pos_rad ?? 0);
      set(pivots.flapR, row.flap_pos_rad ?? 0);
      set(pivots.rudder, row.rudder_pos_rad ?? 0);
      // Beacon: a short flash about 45 times a minute, timed by the flight clock (so a
      // replay flashes the same way).
      const on = ((row.t_s ?? 0) % BEACON_PERIOD_S) < BEACON_FLASH_S;
      beacon.userData.halo.visible = on;
      beacon.userData.lens.material.color.setHex(on ? 0xff3a2a : 0x5a1410);
      const rpm = row.engine_rpm ?? 0;
      const spinning = rpm > 400; // a blur disc above idle-ish speeds; separate blades below
      disc.visible = spinning;
      prop.visible = !spinning;
      if (!spinning) {
        propAngle += (rpm / 60) * 2 * Math.PI * dtS;
        prop.rotation.x = propAngle;
      }
    },
  };
}
