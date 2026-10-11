// The road network as drawn roads with markings (prototype, 2026-10-11; `?roadproto` in the
// URL): a region's proto_roads.json (flightsim/world/scenery_roads.py: OSM roads in a box,
// moved onto the imagery's roads, lanes and one-way, where markings stop for junctions,
// marked crossings and stop lines) draped on the ground as the terrain draws it near the
// aircraft. One asphalt for every road, ramp and bridge deck (bridges.js uses roadMaterial):
// ASPHALT, varied by noise fixed to the ground (overlapping roads at junctions merge),
// darker tyre tracks; markings drawn per pixel from the road's own coordinates (metres
// along the network, metres across). Markings and tracks fade out where roads overlap at
// junctions (aMark, scenery_roads.py). Visual only.
//
// Markings (project choices; UAE practice from memory, to be checked against the UAE road
// marking manual): lane lines white, dashed 3 m in 9 m; edge lines solid, on one-way
// carriageways yellow on the left (median) side and white on the right; two-way roads a
// dashed white centre line; zebra crossings 0.5 m bars; stop lines 0.4 m.

import * as THREE from "three";
import { drawnGround } from "./featureGeometry.js";
import { sceneryUrl, world } from "./world.js";

export const ROAD_PROTO = typeof location !== "undefined" && new URLSearchParams(location.search).has("roadproto");
export const LANE_M = 3.65; // as scenery_roads.py
const LIFT_M = 0.06;

const MARKINGS_GLSL = `
const vec3 ASPHALT = vec3(0.099, 0.10, 0.102); // linear, about sRGB (88, 89, 89): worn asphalt (project choice)
float roadHash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float roadNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(roadHash(i), roadHash(i + vec2(1.0, 0.0)), u.x), mix(roadHash(i + vec2(0.0, 1.0)), roadHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
// Coverage of a line across the road at t = c, w wide (antialiased).
float roadLine(float t, float c, float w, float aa) { return 1.0 - smoothstep(w * 0.5 - aa, w * 0.5 + aa, abs(t - c)); }
float roadDash(float s, float aa) { float f = fract(s / 9.0) * 9.0; return smoothstep(0.0, aa, f) * (1.0 - smoothstep(3.0 - aa, 3.0, f)); }`;

// The roads' (and decks') material: vertex colour the asphalt; attributes aRoad (metres
// along, metres across (+ right of travel), lanes, one-way) and aMark (marking weight).
export function roadMaterial() {
  const m = new THREE.MeshLambertMaterial({ polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 });
  m.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec4 aRoad;\nattribute float aMark;\nvarying vec4 vRoad;\nvarying float vMarkW;\nvarying vec2 vRoadXZ;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvRoad = aRoad;\nvMarkW = aMark;\nvRoadXZ = (modelMatrix * vec4(position, 1.0)).xz;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\nvarying vec4 vRoad;\nvarying float vMarkW;\nvarying vec2 vRoadXZ;\n${MARKINGS_GLSL}`)
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
{
  float s = vRoad.x, t = vRoad.y, lanes = vRoad.z, oneway = vRoad.w;
  float aa = fwidth(t) * 0.75 + 0.01, aas = fwidth(s) * 0.75 + 0.01;
  // Asphalt: patches and grain; darker where the tyres run (two tracks a lane).
  float n = roadNoise(vRoadXZ * 0.08) * 0.6 + roadNoise(vRoadXZ * 0.9) * 0.4;
  float L = lanes * ${LANE_M.toFixed(2)};
  float lane = fract((t + L * 0.5) / ${LANE_M.toFixed(2)}) * ${LANE_M.toFixed(2)} - ${(LANE_M / 2).toFixed(3)};
  float tracks = (roadLine(lane, -0.85, 0.7, 0.3) + roadLine(lane, 0.85, 0.7, 0.3)) * step(abs(t), L * 0.5);
  diffuseColor.rgb = ASPHALT * (0.9 + 0.2 * n) * (1.0 - 0.07 * tracks * vMarkW);
  // Markings.
  float white = roadLine(t, L * 0.5, 0.2, aa); // right edge
  float yellow = 0.0;
  if (oneway > 0.5) {
    yellow = roadLine(t, -L * 0.5, 0.2, aa);
    for (int k = 1; k < 8; k++) {
      if (float(k) >= lanes) break;
      white += roadLine(t, -L * 0.5 + float(k) * ${LANE_M.toFixed(2)}, 0.15, aa) * roadDash(s, aas);
    }
  } else {
    white += roadLine(t, -L * 0.5, 0.2, aa) + roadLine(t, 0.0, 0.15, aa) * roadDash(s, aas);
    for (int k = 1; k < 4; k++) {
      if (float(k) * 2.0 >= lanes) break;
      float o = float(k) * ${LANE_M.toFixed(2)};
      white += (roadLine(t, -o, 0.15, aa) + roadLine(t, o, 0.15, aa)) * roadDash(s, aas);
    }
  }
  float wear = 0.75 + 0.25 * roadNoise(vRoadXZ * 1.7);
  white = clamp(white, 0.0, 1.0) * vMarkW * wear;
  yellow = clamp(yellow, 0.0, 1.0) * vMarkW * wear;
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.62, 0.62, 0.6), white);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.62, 0.42, 0.06), yellow);
}`,
      );
  };
  m.customProgramCacheKey = () => "road-markings";
  return m;
}

// Zebra bars and stop lines: aRoad = (metres along the crossing's depth, metres across the
// road, kind (0 zebra, 1 stop), 0).
function marksMaterial() {
  const m = new THREE.MeshLambertMaterial({ color: 0x9e9e9a, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8 });
  m.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec4 aRoad;\nvarying vec4 vRoad;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvRoad = aRoad;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec4 vRoad;")
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
{
  float aa = fwidth(vRoad.y) * 0.75 + 0.01;
  float bars;
  if (vRoad.z < 0.5) bars = smoothstep(0.25 - aa, 0.25 + aa, abs(fract(vRoad.y / 1.0) - 0.5)); // zebra: 0.5 m bars, 0.5 m gaps
  else if (vRoad.z < 1.5) bars = 1.0; // stop line
  else if (vRoad.z < 2.5) bars = smoothstep(0.25 - aa, 0.25 + aa, abs(fract(vRoad.y / 1.2) - 0.5)); // give way: 0.6 m dashes
  else { // gore: diagonal stripes 0.6 m wide every 3 m (vRoad.xy: metres along and across)
    float aad = fwidth(vRoad.x + vRoad.y) * 0.5 + 0.02;
    float d = fract((vRoad.x + vRoad.y) * 0.7071 / 3.0) * 3.0;
    bars = smoothstep(0.0, aad, d) * (1.0 - smoothstep(0.6 - aad, 0.6, d));
  }
  diffuseColor.a *= bars;
}`,
      );
  };
  m.customProgramCacheKey = () => "road-marks";
  return m;
}

// A road ribbon: points [[x, z, mark], ...] (centreline, densified), its width; heights from
// `height(x, z)`. Returns arrays for a BufferGeometry.
export function ribbon(points, widthM, lanes, oneway, s0, height, into) {
  const hw = widthM / 2, n = points.length;
  let s = s0;
  for (let i = 0; i < n; i++) {
    const [x, z, mark] = points[i];
    if (i) s += Math.hypot(x - points[i - 1][0], z - points[i - 1][1]);
    const a = points[Math.max(0, i - 1)], b = points[Math.min(n - 1, i + 1)];
    const dx = b[0] - a[0], dz = b[1] - a[1], l = Math.hypot(dx, dz) || 1;
    const rx = -dz / l, rz = dx / l; // right of travel (x east, z south)
    for (const side of [-1, 1]) {
      const px = x + rx * hw * side, pz = z + rz * hw * side;
      into.pos.push(px, height(px, pz) + LIFT_M, pz);
      into.nrm.push(0, 1, 0);
      into.road.push(s, hw * side, lanes, oneway ? 1 : 0);
      into.mark.push(mark);
    }
    if (i) {
      const v = into.pos.length / 3 - 4;
      into.idx.push(v, v + 1, v + 2, v + 1, v + 3, v + 2); // facing up
    }
  }
}

function geometry(a) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(a.pos, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(a.nrm, 3));
  if (a.col?.length) g.setAttribute("color", new THREE.Float32BufferAttribute(a.col, 3));
  g.setAttribute("aRoad", new THREE.Float32BufferAttribute(a.road, 4));
  if (a.mark) g.setAttribute("aMark", new THREE.Float32BufferAttribute(a.mark, 1));
  g.setIndex(a.idx);
  g.computeBoundingSphere();
  return g;
}


// One 4 km tile's roads (rd_<ix>_<iz>.json), draped on the ground as the terrain draws
// that tile (`segments`): its ribbons, kerb fillets and gores (asphalt), its markings.
function tileGroup(data, segments, materials) {
  const ground = drawnGround(world.tiles, segments);
  const height = (x, z) => ground.height(x, z);
  const stride = segments >= 96 ? 1 : segments >= 48 ? 2 : 6; // fewer points on coarser ground
  const a = { pos: [], nrm: [], road: [], mark: [], idx: [] };
  for (const r of data.roads) {
    const pts = stride === 1 ? r.pts : r.pts.filter((_, i) => i % stride === 0 || i === r.pts.length - 1);
    if (pts.length >= 2) ribbon(pts, r.width, r.lanes, r.oneway, r.s0 ?? 0, height, a);
  }
  const polygon = (t, into, road) => {
    const base = into.pos.length / 3;
    for (let i = 0; i < t.v.length; i += 2) {
      const x = t.v[i], z = t.v[i + 1];
      into.pos.push(x, height(x, z) + LIFT_M, z);
      into.nrm.push(0, 1, 0);
      into.road.push(...road(x, z));
      into.mark?.push(0);
    }
    for (let i = 0; i < t.t.length; i += 3) {
      const [p, q, r] = [t.t[i], t.t[i + 1], t.t[i + 2]];
      const ux = t.v[2 * q] - t.v[2 * p], uz = t.v[2 * q + 1] - t.v[2 * p + 1], vx = t.v[2 * r] - t.v[2 * p], vz = t.v[2 * r + 1] - t.v[2 * p + 1];
      if (uz * vx - ux * vz > 0) into.idx.push(base + p, base + q, base + r); // facing up
      else into.idx.push(base + p, base + r, base + q);
    }
  };
  // Kerb fillets and gores (scenery_roads.py junction_shapes): plain asphalt, no markings.
  for (const t of [...data.fills, ...data.gores]) polygon(t, a, () => [0, 0, 0, 0]);
  const group = new THREE.Group();
  if (a.idx.length) {
    const roads = new THREE.Mesh(geometry(a), materials.road);
    roads.receiveShadow = true;
    group.add(roads);
  }
  if (segments < 48) return group; // far away: no zebras, stop lines or hatching
  const m = { pos: [], nrm: [], road: [], idx: [] };
  for (const k of data.marks) {
    const depth = k.kind === "stop" ? 0.4 : k.kind === "giveway" ? 0.3 : 4.0, hw = k.width / 2 - 0.5;
    const rx = -k.uz, rz = k.ux; // across the road
    const a0 = Math.max(k.a0 ?? -hw, -hw), a1 = Math.min(k.a1 ?? hw, hw);
    const corners = [[-depth / 2, a0], [depth / 2, a0], [-depth / 2, a1], [depth / 2, a1]];
    const v = m.pos.length / 3;
    for (const [along, across] of corners) {
      const x = k.x + k.ux * along + rx * across, z = k.z + k.uz * along + rz * across;
      m.pos.push(x, height(x, z) + LIFT_M, z);
      m.nrm.push(0, 1, 0);
      m.road.push(along, across + hw, { zebra: 0, stop: 1, giveway: 2 }[k.kind] ?? 0, 0);
    }
    m.idx.push(v, v + 1, v + 2, v + 1, v + 3, v + 2);
  }
  for (const t of data.gores) {
    const [ux, uz] = t.u;
    polygon(t, m, (x, z) => [x * ux + z * uz, -x * uz + z * ux, 3, 0]);
  }
  for (let i = 0; i < m.pos.length; i += 3) m.pos[i + 1] += 0.02;
  if (m.idx.length) group.add(new THREE.Mesh(geometry(m), materials.marks));
  return group;
}

export const ROAD_RING = 2; // roads on the terrain tiles this many tiles out (about 10 km)

export class RegionRoads {
  constructor(scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    scene.add(this.group);
    this.materials = { road: roadMaterial(), marks: marksMaterial() };
    this.data = null; // roads.json
    this.scenery = null;
    this.files = new Map(); // "ix,iz" -> tile data, "loading" or null (none)
    this.built = new Map(); // "ix,iz" -> {group, segments}
  }

  // Lanes and one-way of a bridge's OSM ways (bridges.js), or null.
  bridgeLanes(ids) {
    for (const id of ids ?? []) {
      const v = this.data?.bridge_lanes?.[String(id)];
      if (v) return { lanes: v[0], oneway: v[1], dx: v[2] ?? 0, dz: v[3] ?? 0 };
    }
    return null;
  }

  // The network's arc length at a bridge's end (x, z), or null: deck dashes continue the road's.
  sAt(x, z) {
    let best = null;
    for (const [bx, bz, s] of this.data?.bridge_s ?? []) {
      const d = Math.hypot(bx - x, bz - z);
      if (d < 30 && (!best || d < best.d)) best = { d, s };
    }
    return best?.s ?? null;
  }

  // A bridge point [x, z, ...] moved by the network's shift field (scenery_roads.py; held
  // at its edge beyond the 1 m imagery, as the roads).
  shift(q) {
    const f = this.data?.field;
    if (!f) return q;
    const { x0, z0, cell, rows, cols, d } = f;
    const u = Math.min(Math.max((q[0] - x0) / cell, 0), cols - 1.001), v = Math.min(Math.max((q[1] - z0) / cell, 0), rows - 1.001);
    const i = Math.floor(u), j = Math.floor(v), fx = u - i, fz = v - j;
    const at = (r, c, k) => d[(r * cols + c) * 2 + k];
    const lerp = (k) => (at(j, i, k) * (1 - fx) + at(j, i + 1, k) * fx) * (1 - fz) + (at(j + 1, i, k) * (1 - fx) + at(j + 1, i + 1, k) * fx) * fz;
    return [q[0] + lerp(0), q[1] + lerp(1), ...q.slice(2)];
  }

  get active() {
    return Boolean(this.data);
  }

  async load(scenery) {
    this.clear();
    this.data = null;
    this.files.clear();
    this.scenery = scenery;
    if (!ROAD_PROTO || !scenery) return;
    try {
      const r = await fetch(sceneryUrl(scenery, "roads.json"));
      this.data = r.ok ? await r.json() : null;
    } catch {
      this.data = null;
    }
    this.have = new Set((this.data?.tiles ?? []).map(([ix, iz]) => `${ix},${iz}`));
  }

  _fetch(key) {
    this.files.set(key, "loading");
    const [ix, iz] = key.split(",");
    fetch(sceneryUrl(this.scenery, `tiles/rd_${ix}_${iz}.json`))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => this.files.set(key, d))
      .catch(() => this.files.set(key, null));
  }

  // Each frame: road tiles on the terrain tiles drawn within ROAD_RING of the camera's
  // tile, at their detail; at most one built a frame.
  update(terrain) {
    if (!this.data || !terrain?.centre) return;
    const [cx, cz] = terrain.centre;
    const want = new Map();
    for (const [key, t] of terrain.tiles) {
      if (!t.mesh || !this.have.has(key)) continue;
      const [ix, iz] = key.split(",").map(Number);
      if (Math.max(Math.abs(ix - cx), Math.abs(iz - cz)) <= ROAD_RING) want.set(key, t.segments);
    }
    for (const [key, b] of this.built) {
      if (want.get(key) !== b.segments) {
        this.group.remove(b.group);
        b.group.traverse((o) => o.geometry?.dispose());
        this.built.delete(key);
      }
    }
    let budget = 1;
    for (const [key, segments] of want) {
      if (this.built.has(key)) continue;
      const f = this.files.get(key);
      if (f === undefined) this._fetch(key);
      if (!f || f === "loading" || budget <= 0) continue;
      const [ix, iz] = key.split(",").map(Number);
      if (!world.tileReady(ix, iz)) continue;
      const group = tileGroup(f, segments, this.materials);
      this.group.add(group);
      this.built.set(key, { group, segments });
      budget--;
    }
  }

  clear() {
    this.group.traverse((o) => o.geometry?.dispose());
    this.group.clear();
    this.built.clear();
  }
}
