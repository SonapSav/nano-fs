// Landmarks of a real-world region (its landmarks.json: OSM footprint, centre, long axis,
// ground height and the dimensions from the region file; flightsim/world/scenery_build.py),
// modelled simply so the city is recognisable: a grand mosque (white base, minarets, a
// main dome toward the qibla and smaller domes), a leaning tower, a flat dome on four
// piers, a central dome on a building. World frame: x east, y up, z south. Visual only.

import * as THREE from "three";

const WHITE = new THREE.MeshLambertMaterial({ color: 0xf2f0ea });
const SAND = new THREE.MeshLambertMaterial({ color: 0xd8bb8e });
const GOLD = new THREE.MeshLambertMaterial({ color: 0xc8a24a });
const GLASS = new THREE.MeshLambertMaterial({ color: 0x7f97a8 });
const STEEL = new THREE.MeshLambertMaterial({ color: 0xc4c8cc, side: THREE.DoubleSide });

// A footprint ring [x0, z0, ...] extruded from y0 to y1, the top shifted by (sx, sz).
function prism(ring, y0, y1, material, sx = 0, sz = 0) {
  const pts = [];
  for (let k = 0; k < ring.length; k += 2) pts.push(new THREE.Vector2(ring[k], -ring[k + 1])); // shape in (x, -z)
  const geo = new THREE.ExtrudeGeometry(new THREE.Shape(pts), { depth: y1 - y0, bevelEnabled: false });
  geo.rotateX(-Math.PI / 2); // shape plane to the ground, extrusion up
  geo.translate(0, y0, 0);
  if (sx || sz) {
    const p = geo.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const k = (p.getY(i) - y0) / (y1 - y0);
      p.setX(i, p.getX(i) + sx * k);
      p.setZ(i, p.getZ(i) + sz * k);
    }
    geo.computeVertexNormals();
  }
  return new THREE.Mesh(geo, material);
}

// A dome: a drum up to `springY`, then a half-ellipsoid to `topY`, radius r, at (x, z).
function dome(x, z, r, springY, topY, baseY, material, finial = true) {
  const g = new THREE.Group();
  const drum = new THREE.Mesh(new THREE.CylinderGeometry(r, r, springY - baseY, 24), material);
  drum.position.set(x, (springY + baseY) / 2, z);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(r, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), material);
  cap.scale.set(1, (topY - springY) / r, 1);
  cap.position.set(x, springY, z);
  g.add(drum, cap);
  if (finial) {
    const f = new THREE.Mesh(new THREE.ConeGeometry(r * 0.06, r * 0.4, 8), GOLD);
    f.position.set(x, topY + r * 0.2, z);
    g.add(f);
  }
  return g;
}

// Unit vectors of a bearing (degrees from north, clockwise) in the world frame.
const along = (deg) => [Math.sin((deg * Math.PI) / 180), -Math.cos((deg * Math.PI) / 180)];

// --- Sheikh Zayed Grand Mosque ---------------------------------------------------------------
// Layout from OSM (footprint with the courtyard as its inner ring, the domes as
// building:part=dome with their heights, the pools) and the official site's figures (region
// file); forms after the official descriptions: white marble, onion-shaped domes with
// gold-glass crescent finials, minarets of square, octagonal and circular layers with
// balconies and a gilded lantern at the courtyard's corners. Proportions marked "project
// choice" were not published. Marble and gold are lit by an environment map of the sky
// (Landmarks.setEnvironment).

const ARCADE_BAY_M = 7; // arch spacing on the walls (project choice)
const BULB_RATIO = 1.25; // onion height / radius (project choice)

const marble = new THREE.MeshStandardMaterial({ color: 0xe4ddd0, roughness: 0.42, metalness: 0, envMapIntensity: 0.3 });
const gold = new THREE.MeshStandardMaterial({ color: 0xe0b85a, roughness: 0.3, metalness: 1, envMapIntensity: 1.1 });
const pool = new THREE.MeshStandardMaterial({ color: 0x0b2630, roughness: 0.06, metalness: 0, envMapIntensity: 0.9, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });

// Marble walls with openings: a pointed (equilateral) arch per bay, dark inside. The
// "facade" attribute: (along the wall m, up m, wall height m (negative: windows centred in
// the height, for drums and minarets; positive: an arcade from near the floor), bay m (0:
// plain)). Detail fades to its average with distance.
const arcade = marble.clone();
arcade.onBeforeCompile = (shader) => {
  shader.vertexShader = shader.vertexShader
    .replace("#include <common>", "#include <common>\nattribute vec4 facade;\nvarying vec4 vFacade;")
    .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFacade = facade;");
  shader.fragmentShader = shader.fragmentShader
    .replace(
      "#include <common>",
      `#include <common>
varying vec4 vFacade;
float archOpening(vec4 f) {
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
    )
    .replace(
      "#include <color_fragment>",
      `#include <color_fragment>
float mosqueDetail = 1.0 - smoothstep(0.06, 0.2, max(fwidth(vFacade.x), fwidth(vFacade.y)) / max(vFacade.w, 0.01));
float mosqueOpen = vFacade.w > 0.0 ? mix(0.3, archOpening(vFacade), mosqueDetail) : 0.0;
float mosqueH = abs(vFacade.z);
diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.15, 0.16, 0.18) + 0.1 * (1.0 - vFacade.y / max(mosqueH, 1.0)), mosqueOpen);
diffuseColor.rgb *= 1.0 - 0.22 * mosqueDetail * step(mosqueH - 2.6, vFacade.y) * step(vFacade.y, mosqueH - 1.8) * step(0.0, vFacade.z);`,
    )
    .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\nroughnessFactor = mix(roughnessFactor, 1.0, mosqueOpen);");
};
arcade.customProgramCacheKey = () => "mosque-arcade";

// The courtyard: white marble with coloured floral inlays, dense at the edges and thinning
// toward the centre (after the official description; motif sizes project choices).
function courtyardMaterial(box) {
  const m = marble.clone();
  m.roughness = 0.3;
  m.polygonOffset = true;
  m.polygonOffsetFactor = m.polygonOffsetUnits = -2;
  const uniforms = { uC: { value: new THREE.Vector2(box.cx, box.cz) }, uA: { value: new THREE.Vector2(box.ux, box.uz) }, uHalf: { value: new THREE.Vector2(box.ha, box.hb) } };
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec2 vXZ;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvXZ = (modelMatrix * vec4(transformed, 1.0)).xz;");
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
varying vec2 vXZ;
uniform vec2 uC, uA, uHalf;
vec2 floralHash(vec2 p) { return fract(sin(vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)))) * 43758.5453); }`,
      )
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
{
  vec2 d = vXZ - uC;
  vec2 q = vec2(dot(d, uA), dot(d, vec2(-uA.y, uA.x)));
  float edge = min(uHalf.x - abs(q.x), uHalf.y - abs(q.y));
  float detail = 1.0 - smoothstep(0.5, 1.6, fwidth(q.x));
  float cell = 9.0;
  vec2 id = floor(q / cell), h = floralHash(id);
  vec2 lc = (fract(q / cell) - 0.5) * cell - (h - 0.5) * 3.0;
  float prob = mix(0.9, 0.06, smoothstep(4.0, 0.45 * min(uHalf.x, uHalf.y), edge));
  float present = step(fract(h.x * 7.13), prob);
  float r = length(lc), a = atan(lc.y, lc.x);
  float petal = step(r, 2.3 * (0.5 + 0.5 * abs(cos(2.5 * a + h.x * 6.28))));
  vec2 l = vec2(cos(h.y * 6.28), sin(h.y * 6.28));
  vec2 lp = lc - 2.9 * l;
  float leaf = step(length(vec2(dot(lp, l), dot(lp, vec2(-l.y, l.x)) * 2.4)), 1.3);
  vec3 col = h.y < 0.33 ? vec3(0.6, 0.12, 0.13) : h.y < 0.66 ? vec3(0.85, 0.6, 0.17) : vec3(0.2, 0.3, 0.6);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.24, 0.42, 0.22), leaf * present * detail);
  diffuseColor.rgb = mix(diffuseColor.rgb, col, petal * present * detail);
  diffuseColor.rgb *= 1.0 - 0.12 * step(1.0, edge) * step(edge, 2.2);
}`,
      );
  };
  m.customProgramCacheKey = () => "mosque-courtyard";
  return m;
}

// Signed area of a ring [x0, z0, ...] (> 0: counter-clockwise in (x, z)).
function ringArea(r) {
  let a = 0;
  for (let k = 0, n = r.length / 2; k < n; k++) {
    const j = (k + 1) % n;
    a += r[2 * k] * r[2 * j + 1] - r[2 * j] * r[2 * k + 1];
  }
  return a / 2;
}

// The oriented box of a ring along its longest edge: centre, unit axes, half extents.
function orientedBox(r) {
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

// Walls along a ring from y0 to y1 as non-indexed triangles with the facade attribute;
// `out`: normals away from the ring's inside (an outer wall), else into it (a courtyard).
function walls(ring, y0, y1, bay, centred = false, out = true) {
  let r = ring;
  if (ringArea(r) > 0 !== out) {
    r = [];
    for (let k = ring.length - 2; k >= 0; k -= 2) r.push(ring[k], ring[k + 1]);
  }
  const pos = [], nrm = [], fac = [], H = (y1 - y0) * (centred ? -1 : 1);
  let along = 0;
  for (let k = 0, n = r.length / 2; k < n; k++) {
    const j = (k + 1) % n, ax = r[2 * k], az = r[2 * k + 1], bx = r[2 * j], bz = r[2 * j + 1];
    const len = Math.hypot(bx - ax, bz - az);
    if (len < 0.01) continue;
    const nx = (bz - az) / len, nz = -(bx - ax) / len; // outward for a counter-clockwise ring (featureGeometry.js)
    const v = [[ax, y0, az, along, 0], [ax, y1, az, along, y1 - y0], [bx, y0, bz, along + len, 0], [bx, y0, bz, along + len, 0], [ax, y1, az, along, y1 - y0], [bx, y1, bz, along + len, y1 - y0]];
    for (const [x, y, z, s, t] of v) {
      pos.push(x, y, z);
      nrm.push(nx, 0, nz);
      fac.push(s, t, H, bay);
    }
    along += len;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(nrm, 3));
  g.setAttribute("facade", new THREE.Float32BufferAttribute(fac, 4));
  return g;
}

// A regular n-gon ring of radius r around (x, z), rotated by `rot` radians.
function ngon(x, z, r, n, rot = 0) {
  const out = [];
  for (let k = 0; k < n; k++) out.push(x + r * Math.cos(rot + (2 * Math.PI * k) / n), z + r * Math.sin(rot + (2 * Math.PI * k) / n));
  return out;
}

// One geometry from several (position, normal and, when any has it, facade).
function merge(geos) {
  const parts = geos.map((g) => (g.index ? g.toNonIndexed() : g));
  const withFacade = parts.some((g) => g.attributes.facade);
  const count = parts.reduce((s, g) => s + g.attributes.position.count, 0);
  const pos = new Float32Array(count * 3), nrm = new Float32Array(count * 3), fac = withFacade ? new Float32Array(count * 4) : null;
  let o = 0;
  for (const g of parts) {
    const c = g.attributes.position.count;
    pos.set(g.attributes.position.array, o * 3);
    nrm.set(g.attributes.normal.array, o * 3);
    if (fac && g.attributes.facade) fac.set(g.attributes.facade.array, o * 4);
    o += c;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
  if (fac) g.setAttribute("facade", new THREE.BufferAttribute(fac, 4));
  for (const p of parts) p.dispose();
  return g;
}

// Onion dome of unit radius and height (base at 0, tip at 1); profile a project choice.
const ONION = [[1.0, 0], [1.06, 0.1], [1.1, 0.22], [1.09, 0.33], [1.02, 0.45], [0.88, 0.57], [0.68, 0.69], [0.46, 0.8], [0.26, 0.89], [0.1, 0.96], [0.0, 1.0]].map(([r, y]) => new THREE.Vector2(r, y));

// Crescent finial of unit height: spire, ball and an upright crescent opening upward.
function finialParts() {
  const spire = new THREE.CylinderGeometry(0.04, 0.07, 0.62, 8).translate(0, 0.31, 0);
  const ball = new THREE.SphereGeometry(0.1, 12, 8).translate(0, 0.42, 0);
  const crescent = new THREE.TorusGeometry(0.2, 0.045, 6, 20, 1.5 * Math.PI).rotateZ(0.75 * Math.PI).translate(0, 0.8, 0);
  return [spire, ball, crescent];
}

const at = (g, x, y, z, sx, sy = sx, sz = sx) => g.applyMatrix4(new THREE.Matrix4().makeTranslation(x, y, z).multiply(new THREE.Matrix4().makeScale(sx, sy, sz)));

function grandMosque(m) {
  const g = new THREE.Group(), y0 = m.ground_m, roof = y0 + m.roof_m;
  const court = (m.inner ?? []).reduce((a, r) => (Math.abs(ringArea(r)) > Math.abs(ringArea(a ?? [])) ? r : a), null);
  const wallGeos = [walls(m.ring, y0 - 0.5, roof, ARCADE_BAY_M)];
  for (const r of m.inner ?? []) wallGeos.push(walls(r, y0 - 0.5, roof, ARCADE_BAY_M, false, false));
  // Roof with the courtyard open.
  const toShape = (r, C) => {
    const s = new C();
    for (let k = 0; k < r.length; k += 2) (k ? s.lineTo(r[k], -r[k + 1]) : s.moveTo(r[k], -r[k + 1]));
    return s;
  };
  const shape = toShape(m.ring, THREE.Shape);
  for (const r of m.inner ?? []) shape.holes.push(toShape(r, THREE.Path));
  const roofGeo = new THREE.ShapeGeometry(shape).rotateX(-Math.PI / 2).translate(0, roof, 0);
  const plain = [roofGeo], golden = [];

  // Domes: OSM's parts; the largest gets the official size. Drum from the roof to the
  // onion's spring; finial on top.
  const domes = (m.domes ?? []).map((d) => [...d]);
  if (domes.length) {
    const main = domes.reduce((a, d) => (d[2] > a[2] ? d : a));
    main[2] = m.dome_diameter_m;
    main[3] = m.dome_top_m;
  }
  for (const [x, z, d, top] of domes) {
    const r = d / 2, f = Math.max(1.2, 0.3 * r), tip = y0 + top - f;
    const spring = Math.max(roof, tip - BULB_RATIO * r);
    plain.push(at(new THREE.LatheGeometry(ONION, 28), x, spring, z, r, tip - spring, r));
    if (spring - roof > 0.3) wallGeos.push(walls(ngon(x, z, r, Math.max(8, Math.round((2 * Math.PI * r) / 2.6))), roof - 0.2, spring, (2 * Math.PI * r) / Math.max(8, Math.round((2 * Math.PI * r) / 2.6)), true));
    for (const p of finialParts()) golden.push(at(p, x, tip - 0.05 * f, z, f));
  }

  // Minarets at the courtyard's corners: a square base, an octagon, a cylinder, a gilded
  // lantern and an onion with its finial; balconies between (proportions project choices).
  if (court) {
    const b = orientedBox(court), H = m.minaret_m;
    const rot = Math.atan2(b.uz, b.ux);
    for (const [sa, sb] of [[-1, -1], [-1, 1], [1, -1], [1, 1]]) {
      const x = b.cx + b.ux * sa * b.ha + b.vx * sb * b.hb, z = b.cz + b.uz * sa * b.ha + b.vz * sb * b.hb;
      const levels = [0.3, 0.6, 0.78, 0.86].map((k) => y0 + k * H);
      wallGeos.push(walls(ngon(x, z, 5 * Math.SQRT2, 4, rot + Math.PI / 4), y0 - 0.5, levels[0], 4, true));
      wallGeos.push(walls(ngon(x, z, 4.3, 8, rot + Math.PI / 8), levels[0], levels[1], 3.3, true));
      wallGeos.push(walls(ngon(x, z, 3.4, 16, rot), levels[1], levels[2], 2.6, true));
      for (const [y, rb] of [[levels[0], 6.2], [levels[1], 5.3], [levels[2], 4.4]]) {
        plain.push(at(new THREE.CylinderGeometry(rb, rb * 0.8, 1.4, 16), x, y + 0.2, z, 1));
      }
      golden.push(at(new THREE.CylinderGeometry(2.8, 2.8, levels[3] - levels[2], 12), x, (levels[2] + levels[3]) / 2, z, 1));
      const f = 3.2, tip = y0 + H - f;
      plain.push(at(new THREE.LatheGeometry(ONION, 16), x, levels[3], z, 3, tip - levels[3], 3));
      for (const p of finialParts()) golden.push(at(p, x, tip - 0.1, z, f));
    }
    const floor = new THREE.ShapeGeometry(toShape(court, THREE.Shape)).rotateX(-Math.PI / 2).translate(0, y0 + 0.35, 0);
    g.add(new THREE.Mesh(floor, courtyardMaterial(b)));
  }
  g.add(new THREE.Mesh(merge(wallGeos), arcade), new THREE.Mesh(merge(plain), marble), new THREE.Mesh(merge(golden), gold));
  if (m.pools?.length) {
    const pools = new THREE.ShapeGeometry(m.pools.map((r) => toShape(r, THREE.Shape))).rotateX(-Math.PI / 2).translate(0, y0 + 0.3, 0);
    g.add(new THREE.Mesh(pools, pool));
  }
  return g;
}

function leaningTower(m) {
  const [dx, dz] = along(m.lean_toward_deg);
  const shift = Math.tan((m.lean_deg * Math.PI) / 180) * m.height_m;
  return prism(m.ring, m.ground_m, m.ground_m + m.height_m, GLASS, dx * shift, dz * shift);
}

function flatDome(m) {
  const g = new THREE.Group(), [cx, cz] = m.centre, r = m.dome_diameter_m / 2;
  const [ux, uz] = along(m.axis_deg), [vx, vz] = [-uz, ux], h = m.pier_spacing_m / 2, top = m.ground_m + m.dome_top_m;
  // A shallow spherical cap (the sphere of radius R through the rim r: rise = 0.12 r,
  // project choice) on a 4 m deep rim, on four piers.
  const rise = 0.12 * r, R = (r * r + rise * rise) / (2 * rise), theta = Math.asin(r / R);
  const edge = top - rise;
  const shell = new THREE.Mesh(new THREE.SphereGeometry(R, 64, 8, 0, Math.PI * 2, 0, theta), STEEL);
  shell.position.set(cx, top - R, cz);
  const rim = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 4, 64, 1, true), STEEL);
  rim.position.set(cx, edge - 2, cz);
  for (const [a, b] of [[-h, -h], [-h, h], [h, -h], [h, h]]) {
    const pier = new THREE.Mesh(new THREE.BoxGeometry(5, edge - 4 - m.ground_m, 5), WHITE);
    pier.position.set(cx + ux * a + vx * b, (m.ground_m + edge - 4) / 2, cz + uz * a + vz * b);
    g.add(pier);
  }
  g.add(shell, rim);
  return g;
}

function centralDome(m) {
  const [cx, cz] = m.centre, r = m.dome_diameter_m / 2, base = m.ground_m + m.roof_m;
  return dome(cx, cz, r, m.ground_m + m.dome_top_m - r * 1.1, m.ground_m + m.dome_top_m, base, SAND);
}

const BUILDERS = { grand_mosque: grandMosque, leaning_tower: leaningTower, flat_dome: flatDome, central_dome: centralDome };

export class Landmarks {
  constructor(scene) {
    this.group = new THREE.Group();
    scene.add(this.group);
  }

  build(list) {
    this.clear();
    for (const m of list ?? []) {
      const make = BUILDERS[m.kind];
      if (make) this.group.add(make(m));
    }
  }

  clear() {
    this.group.traverse((o) => {
      o.geometry?.dispose();
      if (o.material?.customProgramCacheKey?.() === "mosque-courtyard") o.material.dispose();
    });
    this.group.clear();
  }

  // An environment map of the current sky (and a sand-coloured ground below the horizon)
  // for the marble, gold and pools; again after the time of day or the renderer changes.
  setEnvironment(renderer, sky) {
    if (!this.group.children.length) return;
    const pm = new THREE.PMREMGenerator(renderer);
    const env = new THREE.Scene();
    const dome = new THREE.Mesh(sky.geometry, sky.material);
    dome.scale.setScalar(500);
    const ground = new THREE.Mesh(new THREE.CircleGeometry(400, 32).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: 0x8a7656 }));
    ground.position.y = -20;
    env.add(dome, ground);
    const target = pm.fromScene(env, 0, 1, 1000);
    ground.geometry.dispose();
    ground.material.dispose();
    pm.dispose();
    this.envTarget?.dispose();
    this.envTarget = target;
    const mats = new Set([marble, gold, pool, arcade]);
    this.group.traverse((o) => o.material && mats.add(o.material));
    for (const m of mats) {
      if (!(m instanceof THREE.MeshStandardMaterial)) continue;
      m.envMap = target.texture;
      m.needsUpdate = true;
    }
  }
}
