// Landmarks of a real-world region (its landmarks.json: OSM footprints, OSM's 3D parts,
// centre, long axis, ground height and the dimensions from the region file;
// flightsim/world/scenery_build.py), modelled after published descriptions so the city is
// recognisable from the air: a grand mosque, a leaning diagrid tower, a perforated flat
// dome over a museum city, palaces with domes, glass towers. Materials are physically
// based and lit by an environment map of the sky; they cast and take the sun's shadows
// (sunShadows.js). Shared materials, patterns
// and geometry: landmarkKit.js. Proportions marked "project choice" were not published.
// World frame: x east, y up, z south. Visual only.

import * as THREE from "three";
import {
  along, at, CLASSIC, facadeGlass, facadeStone, finialParts, flat, gold, inside, merge, mesh, ngon, ONION,
  orientedBox, patched, poolWater, ringArea, ringCentre, spireParts, steel, stone, toShape, walls,
} from "./landmarkKit.js";  // prettier-ignore

const MARBLE = 0xe4ddd0;
const PARAPET_M = 1.6; // (project choice)

// Pools (OSM natural=water on a landmark): dark glossy water a little above the ground.
function pools(m) {
  if (!m.pools?.length) return null;
  const geo = new THREE.ShapeGeometry(m.pools.map((r) => toShape(r))).rotateX(-Math.PI / 2).translate(0, m.ground_m + 0.3, 0);
  return mesh(geo, poolWater(), { cast: false });
}

// --- Sheikh Zayed Grand Mosque ---------------------------------------------------------------
// Layout from OSM (footprint with the courtyard as its inner ring, the domes as
// building:part=dome with their heights, the pools); the official site's figures (region
// file): white marble, onion-shaped domes with gold-glass crescent finials, minarets of
// square, octagonal and circular layers with balconies and a gilded lantern at the
// courtyard's corners, a courtyard with floral marble inlays.

const ARCADE_BAY_M = 7; // arch spacing on the walls (project choice)
const BULB_RATIO = 1.25; // onion height / radius (project choice)

// The courtyard: white marble with coloured floral inlays, dense at the edges and thinning
// toward the centre (after the official description; motif sizes project choices).
function courtyardMaterial(box) {
  return patched(
    { color: 0xeae5dc, roughness: 0.3, metalness: 0, envMapIntensity: 0.3, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 },
    {
      key: "courtyard",
      uniforms: { uC: { value: new THREE.Vector2(box.cx, box.cz) }, uA: { value: new THREE.Vector2(box.ux, box.uz) }, uHalf: { value: new THREE.Vector2(box.ha, box.hb) } },
      head: "uniform vec2 uC, uA, uHalf;",
      color: `{
  vec2 d = vLmWorld.xz - uC;
  vec2 q = vec2(dot(d, uA), dot(d, vec2(-uA.y, uA.x)));
  float edge = min(uHalf.x - abs(q.x), uHalf.y - abs(q.y));
  float detail = 1.0 - smoothstep(0.5, 1.6, fwidth(q.x));
  float cell = 9.0;
  vec2 id = floor(q / cell), h = vec2(lmHash(id), lmHash(id + 17.3));
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
  diffuseColor.rgb *= 0.97 + 0.05 * lmNoise(vLmWorld.xz * 0.2);
}`,
    },
  );
}

function grandMosque(m) {
  const g = new THREE.Group(), y0 = m.ground_m, roof = y0 + m.roof_m;
  const court = (m.inner ?? []).reduce((a, r) => (Math.abs(ringArea(r)) > Math.abs(ringArea(a ?? [])) ? r : a), null);
  const wallGeos = [walls(m.ring, y0 - 0.5, roof, ARCADE_BAY_M), walls(m.ring, roof, roof + PARAPET_M, 0), walls(m.ring, roof, roof + PARAPET_M, 0, { out: false })];
  for (const r of m.inner ?? []) {
    wallGeos.push(walls(r, y0 - 0.5, roof, ARCADE_BAY_M, { out: false }), walls(r, roof, roof + PARAPET_M, 0, { out: false }), walls(r, roof, roof + PARAPET_M, 0));
  }
  const plain = [flat(m.ring, m.inner, roof)], golden = [];

  // Domes: OSM's parts; the largest gets the official size. A drum from the roof to the
  // onion's spring, with a cornice where the onion starts; the finial on top.
  const domes = (m.domes ?? []).filter((d) => d.top).map((d) => ({ ...d }));
  if (domes.length) Object.assign(domes.reduce((a, d) => (d.d > a.d ? d : a)), { d: m.dome_diameter_m, top: m.dome_top_m });
  for (const { x, z, d, top } of domes) {
    const r = d / 2, f = Math.max(1.2, 0.3 * r), tip = y0 + top - f;
    const spring = Math.max(roof, tip - BULB_RATIO * r);
    plain.push(at(new THREE.LatheGeometry(ONION, 28), x, spring, z, r, tip - spring, r));
    if (spring - roof > 0.3) {
      const n = Math.max(8, Math.round((2 * Math.PI * r) / 2.6));
      wallGeos.push(walls(ngon(x, z, r, n), roof - 0.2, spring, (2 * Math.PI * r) / n, { centred: true }));
      plain.push(at(new THREE.CylinderGeometry(r * 1.06, r * 1.02, Math.max(0.4, 0.07 * r), 28), x, spring, z, 1));
    }
    for (const p of finialParts()) golden.push(at(p, x, tip - 0.05 * f, z, f));
  }

  // Minarets at the courtyard's corners: a square base, an octagon, a cylinder, a gilded
  // lantern and an onion with its finial; balconies between (proportions project choices).
  if (court) {
    const b = orientedBox(court), H = m.minaret_m, rot = Math.atan2(b.uz, b.ux);
    for (const [sa, sb] of [[-1, -1], [-1, 1], [1, -1], [1, 1]]) {
      const x = b.cx + b.ux * sa * b.ha + b.vx * sb * b.hb, z = b.cz + b.uz * sa * b.ha + b.vz * sb * b.hb;
      const levels = [0.3, 0.6, 0.78, 0.86].map((k) => y0 + k * H);
      wallGeos.push(walls(ngon(x, z, 5 * Math.SQRT2, 4, rot + Math.PI / 4), y0 - 0.5, levels[0], 4, { centred: true }));
      wallGeos.push(walls(ngon(x, z, 4.3, 8, rot + Math.PI / 8), levels[0], levels[1], 3.3, { centred: true }));
      wallGeos.push(walls(ngon(x, z, 3.4, 16, rot), levels[1], levels[2], 2.6, { centred: true }));
      for (const [y, rb] of [[levels[0], 6.2], [levels[1], 5.3], [levels[2], 4.4]]) {
        plain.push(at(new THREE.CylinderGeometry(rb, rb * 0.8, 1.4, 16), x, y + 0.2, z, 1));
      }
      golden.push(at(new THREE.CylinderGeometry(2.8, 2.8, levels[3] - levels[2], 12), x, (levels[2] + levels[3]) / 2, z, 1));
      const f = 3.2, tip = y0 + H - f;
      plain.push(at(new THREE.LatheGeometry(ONION, 16), x, levels[3], z, 3, tip - levels[3], 3));
      for (const p of finialParts()) golden.push(at(p, x, tip - 0.1, z, f));
    }
    g.add(mesh(flat(court, [], y0 + 0.35), courtyardMaterial(b), { cast: false }));
  }
  g.add(mesh(merge(wallGeos), facadeStone(MARBLE, { pattern: "arch" })), mesh(merge(plain), stone(MARBLE, { roughness: 0.42 })), mesh(merge(golden), gold()));
  const water = pools(m);
  if (water) g.add(water);
  return g;
}

// --- Capital Gate -----------------------------------------------------------------------------
// The OSM footprint carried up 160 m: floors stacked vertically to the 12th storey, then
// leaning on a smooth curve so the top sits where an 18 degree average lean puts it (the
// curve's shape is a project choice). The facade is a diagrid of diamond glass panels:
// their count (about 700) and the facade's area give their size (two storeys high). A
// stainless steel "splash" runs from the facade down over the grandstand to the entrance
// canopy (its course and size project choices).

function leaningTower(m) {
  const g = new THREE.Group(), H = m.height_m, y0 = m.ground_m, storey = H / m.storeys, yv = m.vertical_storeys * storey;
  const shift = Math.tan((m.lean_deg * Math.PI) / 180) * H, k = shift / (H - yv) ** 2;
  const [lx, lz] = along(m.lean_toward_deg);
  const offset = (y) => {
    const t = Math.max(0, y - y0 - yv);
    return [lx * k * t * t, lz * k * t * t];
  };
  let perimeter = 0;
  for (let i = 0, n = m.ring.length / 2; i < n; i++) {
    const j = (i + 1) % n;
    perimeter += Math.hypot(m.ring[2 * j] - m.ring[2 * i], m.ring[2 * j + 1] - m.ring[2 * i + 1]);
  }
  const diamondH = 2 * storey, diamondW = (2 * ((perimeter * H) / m.diagrid_panels)) / diamondH;
  g.add(mesh(walls(m.ring, y0 - 0.5, y0 + H, diamondW, { offset, rowM: storey / 2 }), facadeGlass("diagrid", { diamondM: diamondH, color: 0x56737f })));
  const [tx, tz] = offset(y0 + H);
  g.add(mesh(flat(m.ring, [], y0 + H, tx, tz), stone(0x8d9398, { roughness: 0.7 })));

  // The splash: a curved steel sheet from the facade at 45 % of the height, falling
  // steeply and then flattening into a canopy 10 m above the ground, 95 m out.
  const [dx, dz] = along(m.splash_toward_deg), [px, pz] = [-dz, dx];
  const [cx, cz] = ringCentre(m.ring);
  let reach = 0;
  for (let i = 0; i < m.ring.length; i += 2) reach = Math.max(reach, (m.ring[i] - cx) * dx + (m.ring[i + 1] - cz) * dz);
  const yS = 0.45 * H, [ox, oz] = offset(y0 + yS), start = reach + 1 + ox * dx + oz * dz;
  const N = 28, M = 6, pos = [], idx = [];
  for (let i = 0; i <= N; i++) {
    const t = i / N, dist = start + (95 - start) * t, y = y0 + 10 + (yS - 10) * (1 - t) ** 2, w = 22 - 8 * t;
    const sideX = ox - (ox * dx + oz * dz) * dx, sideZ = oz - (ox * dx + oz * dz) * dz; // the lean across the splash
    for (let j = 0; j <= M; j++) {
      const s = j / M - 0.5, sag = 1.6 * (1 - 4 * s * s);
      pos.push(cx + sideX * (1 - t) + dx * dist + px * s * w, y + sag, cz + sideZ * (1 - t) + dz * dist + pz * s * w);
      if (i < N && j < M) {
        const a = i * (M + 1) + j;
        idx.push(a, a + M + 1, a + 1, a + 1, a + M + 1, a + M + 2);
      }
    }
  }
  const splash = new THREE.BufferGeometry();
  splash.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  splash.setIndex(idx);
  splash.computeVertexNormals();
  g.add(mesh(splash, steel(THREE.DoubleSide)));
  return g;
}

// --- Louvre Abu Dhabi -----------------------------------------------------------------------
// A shallow dome 180 m across on four piers 110 m apart; its rim (a 5 m steel frame) at
// OSM's dome part's min_height and its top at min_height + roof:height. Two perforated
// shells (stainless steel outside, aluminium inside) stand for the eight layers of stars,
// so sunlight falls through in spots ("rain of light", also in the shadow pass). Under it
// a museum city of white buildings on the land of the OSM footprint (layout, sizes and
// heights project choices; the count from louvreabudhabi.ae).

const STAR = `
float lmStar(vec2 p, float cell, float rot) {
  float c = cos(rot), s = sin(rot);
  p = mat2(c, -s, s, c) * p;
  vec2 q = (fract(p / cell) - 0.5) * cell;
  float sq1 = max(abs(q.x), abs(q.y));
  float sq2 = max(abs(q.x + q.y), abs(q.x - q.y)) * 0.70710678;
  return min(sq1, sq2) - 0.3 * cell; // < 0: inside an eight-pointed star (an opening)
}`;

// A perforated shell: star openings where they are a few pixels or more across (and in
// the shadow map); beyond that the pattern stays as shading (the frame around each star
// darker), averaging out with distance.
function perforated(color, cell, rot) {
  const c = cell.toFixed(2), holes = `if (lmStar(vLmWorld.xz, ${c}, ${rot.toFixed(3)}) < 0.0 && fwidth(vLmWorld.x) < ${(cell * 0.12).toFixed(3)}) discard;`;
  const shade = `{
  float st = lmStar(vLmWorld.xz, ${c}, ${rot.toFixed(3)}), pw = fwidth(vLmWorld.x);
  float near = smoothstep(-0.2 * ${c}, 0.15 * ${c}, st) * 0.4 + 0.6;
  diffuseColor.rgb *= mix(near, 0.78, smoothstep(${c} * 0.05, ${c} * 0.3, pw));
}`;
  const mat = patched({ color, roughness: 0.42, metalness: 1, envMapIntensity: 0.9, side: THREE.DoubleSide }, { key: `star-${cell}`, head: STAR, color: `${holes}\n${shade}` });
  const depth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking, side: THREE.DoubleSide });
  depth.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vLmWorld;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvLmWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\nvarying vec3 vLmWorld;\n${STAR}`)
      .replace("void main() {", `void main() {\n${holes}`);
  };
  depth.customProgramCacheKey = () => `star-depth-${cell}`;
  return [mat, depth];
}

function hash2(i, j) {
  const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453;
  return s - Math.floor(s);
}

function flatDome(m) {
  const g = new THREE.Group(), y0 = m.ground_m;
  const part = (m.domes ?? []).reduce((a, d) => (!a || d.d > a.d ? d : a), null);
  const [cx, cz] = part ? [part.x, part.z] : m.centre;
  const r = m.dome_diameter_m / 2;
  const rimLow = y0 + (part?.min ?? 10), rimTop = rimLow + m.frame_m;
  const rise = part?.roof_h ? Math.max(4, part.roof_h - m.frame_m) : 0.12 * r;
  const R = (r * r + rise * rise) / (2 * rise), theta = Math.asin(r / R);
  const cap = (lift) => new THREE.SphereGeometry(R, 96, 10, 0, Math.PI * 2, 0, theta).translate(cx, rimTop + lift + rise - R, cz);
  const [outerMat, outerDepth] = perforated(0xaeb3b8, 4.6, 0.0);
  const [innerMat, innerDepth] = perforated(0xc9ccd0, 3.3, 0.39);
  const outer = mesh(cap(0), outerMat), inner = mesh(cap(-2.5), innerMat);
  outer.customDepthMaterial = outerDepth;
  inner.customDepthMaterial = innerDepth;
  const rimMat = steel(THREE.DoubleSide);
  rimMat.color.setHex(0x80868c);
  const rim = mesh(new THREE.CylinderGeometry(r, r, m.frame_m, 96, 1, true).translate(cx, (rimLow + rimTop) / 2, cz), rimMat);
  g.add(outer, inner, rim);

  // Piers and the museum city.
  const [ux, uz] = along(m.axis_deg), [vx, vz] = [-uz, ux], h = m.pier_spacing_m / 2;
  const white = [], roofs = [];
  for (const [a, b] of [[-h, -h], [-h, h], [h, -h], [h, h]]) {
    white.push(walls(ngon(cx + ux * a + vx * b, cz + uz * a + vz * b, 4, 4, Math.atan2(uz, ux) + Math.PI / 4), y0 - 0.5, rimLow, 0));
  }
  const cand = [], step = 24;
  for (let i = -5; i <= 5; i++) {
    for (let j = -5; j <= 5; j++) {
      const a = i * step + (hash2(i, j) - 0.5) * 8, b = j * step + (hash2(j, i) - 0.5) * 8;
      const x = cx + ux * a + vx * b, z = cz + uz * a + vz * b;
      if (Math.hypot(a, b) > r * 0.92 || !inside(x, z, m.ring)) continue;
      cand.push({ x, z, key: hash2(i + 31, j + 7), w: 10 + 10 * hash2(i + 3, j), d: 10 + 10 * hash2(i, j + 5), hgt: 6 + 10 * hash2(i + 9, j + 2) });
    }
  }
  cand.sort((p, q) => p.key - q.key);
  for (const c of cand.slice(0, m.museum_buildings)) {
    const ring = [];
    for (const [sa, sb] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) ring.push(c.x + ux * sa * c.w * 0.5 + vx * sb * c.d * 0.5, c.z + uz * sa * c.w * 0.5 + vz * sb * c.d * 0.5);
    white.push(walls(ring, y0 - 0.5, y0 + c.hgt, 0));
    roofs.push(flat(ring, [], y0 + c.hgt));
  }
  g.add(mesh(merge(white), facadeStone(0xf1eee8, { roughness: 0.8, jointM: 2.4 })), mesh(merge(roofs), stone(0xeeebe5, { roughness: 0.85 })));
  return g;
}

// --- Palaces (Emirates Palace, Qasr Al Watan) ---------------------------------------------------
// The OSM footprint (courtyards from its inner rings) at the region file's roof height or
// OSM's height for it, walls with storeys of arched windows (4.2 m storeys, 4.5 m bays:
// project choices) and a parapet; OSM's taller building parts; OSM's domes (raised domes
// on drums with a spire finial), the largest at the published size (at the footprint's
// centre when OSM maps no domes); the pools. Emirates Palace's main dome is gold and
// silver glass mosaic; domes OSM tags golden are gilded.

function mosaic(cx, cy, cz) {
  return patched(
    { color: 0xe3bf62, roughness: 0.24, metalness: 1, envMapIntensity: 1.1 },
    {
      key: "mosaic",
      uniforms: { uC: { value: new THREE.Vector3(cx, cy, cz) } },
      head: "uniform vec3 uC;",
      color: `{
  vec3 d = vLmWorld - uC;
  vec2 p = vec2(atan(d.z, d.x) * 16.0 / 3.14159265, d.y / 2.4);
  float lat = abs(fract(p.x + p.y + 0.5) - 0.5) + abs(fract(p.x - p.y + 0.5) - 0.5);
  float det = 1.0 - smoothstep(0.15, 0.45, fwidth(p.x));
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.8, 0.82, 0.85), step(lat, 0.32) * det * 0.9);
}`,
    },
  );
}

function palace(m) {
  const g = new THREE.Group(), y0 = m.ground_m;
  const colour = m.colour === "white" ? 0xece8df : 0xd5b48a;
  const fp = m.footprints?.[0] ?? { ring: m.ring, inner: m.inner, height_m: null };
  const roofH = m.roof_m ?? fp.height_m ?? 20, roof = y0 + roofH;
  const wallGeos = [walls(fp.ring, y0 - 0.5, roof, 4.5), walls(fp.ring, roof, roof + PARAPET_M, 0, { out: false })];
  for (const r of fp.inner ?? []) wallGeos.push(walls(r, y0 - 0.5, roof, 4.5, { out: false }), walls(r, roof, roof + PARAPET_M, 0));
  const plain = [flat(fp.ring, fp.inner, roof)];
  for (const p of m.building_parts ?? []) {
    const top = p.top ?? (p.levels ? p.levels * 4.2 : null);
    if (!top || top < roofH + 2) continue;
    wallGeos.push(walls(p.ring, y0 + (p.min ?? 0) - 0.5, y0 + top, 4.5));
    plain.push(flat(p.ring, [], y0 + top));
  }
  const domes = (m.domes ?? []).map((d) => ({ ...d }));
  let main = domes.reduce((a, d) => (!a || d.d > a.d ? d : a), null);
  if (!main && m.dome_diameter_m) domes.push((main = { x: m.centre[0], z: m.centre[1] }));
  if (main) Object.assign(main, { d: m.dome_diameter_m, top: m.dome_top_m, min: null, main: true });
  const gilded = [], finials = [], domeGeos = [];
  for (const d of domes) {
    const r = d.d / 2, base = y0 + (d.min ?? roofH);
    const top = y0 + (d.top ?? roofH + 1.7 * r), f = Math.max(1, 0.22 * r), tip = top - f;
    const spring = Math.max(base, tip - 0.95 * r);
    if (spring - base > 0.3) {
      const n = Math.max(8, Math.round((2 * Math.PI * r) / 3));
      wallGeos.push(walls(ngon(d.x, d.z, r, n), base - 0.2, spring, (2 * Math.PI * r) / n, { centred: true }));
      plain.push(at(new THREE.CylinderGeometry(r * 1.07, r * 1.03, Math.max(0.5, 0.08 * r), 32), d.x, spring, d.z, 1));
    }
    const geo = at(new THREE.LatheGeometry(CLASSIC, 32), d.x, spring, d.z, r, tip - spring, r);
    if (d.main && m.mosaic) g.add(mesh(geo, mosaic(d.x, spring, d.z)));
    else if (/gold/i.test(d.colour ?? "")) gilded.push(geo);
    else domeGeos.push(geo);
    for (const p of spireParts()) finials.push(at(p, d.x, tip - 0.05 * f, d.z, f));
  }
  g.add(mesh(merge(wallGeos), facadeStone(colour, { pattern: "storeys", glazed: true, storeyM: 4.2, jointM: 1.5 })));
  g.add(mesh(merge(plain), stone(colour, { roughness: 0.6 })));
  if (domeGeos.length) g.add(mesh(merge(domeGeos), stone(colour, { roughness: 0.5 })));
  if (gilded.length) g.add(mesh(merge(gilded), gold()));
  if (finials.length) g.add(mesh(merge(finials), gold()));
  const water = pools(m);
  if (water) g.add(water);
  return g;
}

// --- Glass towers (Etihad Towers) -----------------------------------------------------------
// Each OSM footprint at OSM's height for it, in mirror-like glass with mullions and
// spandrel bands (3.9 m storeys, 1.6 m mullions: project choices).

function glassTowers(m) {
  const g = new THREE.Group(), y0 = m.ground_m;
  const geos = [], roofs = [];
  for (const fp of m.footprints ?? []) {
    const h = fp.height_m ?? 150;
    geos.push(walls(fp.ring, y0 - 0.5, y0 + h, 1.6));
    roofs.push(flat(fp.ring, [], y0 + h));
  }
  g.add(mesh(merge(geos), facadeGlass("curtain", { storeyM: 3.9, color: 0x4f6f7d })), mesh(merge(roofs), stone(0x7d848a, { roughness: 0.7 })));
  return g;
}

const BUILDERS = { grand_mosque: grandMosque, leaning_tower: leaningTower, flat_dome: flatDome, palace, glass_towers: glassTowers };

// --- Landmarks ---------------------------------------------------------------------------------

export class Landmarks {
  constructor(scene) {
    this.group = new THREE.Group();
    scene.add(this.group);
    this.sites = []; // per landmark: its centre {x, y, z} and half size R
  }

  build(list) {
    this.clear();
    for (const m of list ?? []) {
      const make = BUILDERS[m.kind];
      if (!make) continue;
      const g = make(m);
      this.group.add(g);
      const box = new THREE.Box3().setFromObject(g), c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
      const R = Math.max(Math.max(s.x, s.z) / 2, 0.9 * s.y) + 25;
      this.sites.push({ x: c.x, y: m.ground_m + s.y / 3, z: c.z, R });
    }
  }

  clear() {
    this.group.traverse((o) => {
      o.geometry?.dispose();
      o.material?.dispose();
      o.customDepthMaterial?.dispose();
    });
    this.group.clear();
    this.sites = [];
  }

  // An environment map of the current sky (and a sand-coloured ground below the horizon)
  // for the stone, metal, glass and water; again after the time of day or the renderer
  // changes.
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
    this.group.traverse((o) => {
      if (!(o.material instanceof THREE.MeshStandardMaterial)) return;
      o.material.envMap = target.texture;
      o.material.needsUpdate = true;
    });
  }
}
