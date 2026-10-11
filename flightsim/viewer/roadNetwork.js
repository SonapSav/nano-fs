// The road network as drawn roads with markings (prototype, 2026-10-11; `?roadproto` in the
// URL): a region's proto_roads.json (flightsim/world/scenery_roads.py: OSM roads in a box,
// moved onto the imagery's roads, lanes and one-way, where markings stop for junctions,
// marked crossings and stop lines) draped on the ground as the terrain draws it near the
// aircraft. One asphalt for these roads and the bridge decks (bridges.js uses roadMaterial):
// the tile's measured road colour, varied by noise, darker tyre tracks; markings drawn per
// pixel from the road's own coordinates (metres along, metres across). Visual only.
//
// Markings (project choices; UAE practice from memory, to be checked against the UAE road
// marking manual): lane lines white, dashed 3 m in 9 m; edge lines solid, on one-way
// carriageways yellow on the left (median) side and white on the right; two-way roads a
// dashed white centre line; zebra crossings 0.5 m bars; stop lines 0.4 m.

import * as THREE from "three";
import { drawnGround, imageryColour } from "./featureGeometry.js";
import { sceneryUrl, world } from "./world.js";

export const ROAD_PROTO = typeof location !== "undefined" && new URLSearchParams(location.search).has("roadproto");
export const LANE_M = 3.65; // as scenery_roads.py
const NEAR_SEGMENTS = 96; // the terrain's near tiles (terrain.js QUALITY.high)
const LIFT_M = 0.06;

const MARKINGS_GLSL = `
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
  const m = new THREE.MeshLambertMaterial({ vertexColors: true, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 });
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
  // Neutral grey from the measured colour (shade and cars in the imagery make it dark and
  // tinted), no darker than worn asphalt (project choice: linear 0.10, about sRGB 89).
  float lum = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));
  diffuseColor.rgb = vec3(max(lum, 0.10)) * vec3(0.99, 1.0, 1.02);
  diffuseColor.rgb *= (0.9 + 0.2 * n) * (1.0 - 0.07 * tracks);
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
  float bars = vRoad.z > 0.5 ? 1.0 : smoothstep(0.25 - aa, 0.25 + aa, abs(fract(vRoad.y / 1.0) - 0.5)); // 0.5 m bars, 0.5 m gaps
  diffuseColor.a *= bars;
}`,
      );
  };
  m.customProgramCacheKey = () => "road-marks";
  return m;
}

// A road ribbon: points [[x, z, mark], ...] (centreline, densified), its width; heights from
// `height(x, z)`. Returns arrays for a BufferGeometry.
export function ribbon(points, widthM, lanes, oneway, colour, height, into) {
  const hw = widthM / 2, n = points.length;
  let s = 0;
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
      into.col.push(...colour);
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
  if (a.col) g.setAttribute("color", new THREE.Float32BufferAttribute(a.col, 3));
  g.setAttribute("aRoad", new THREE.Float32BufferAttribute(a.road, 4));
  if (a.mark) g.setAttribute("aMark", new THREE.Float32BufferAttribute(a.mark, 1));
  g.setIndex(a.idx);
  g.computeBoundingSphere();
  return g;
}

const ASPHALT = [0.042, 0.045, 0.048];

export class RegionRoads {
  constructor(scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    scene.add(this.group);
    this.material = roadMaterial();
    this.marksMaterial = marksMaterial();
    this.data = null;
    this.built = false;
  }

  // Lanes and one-way of a bridge's OSM ways (bridges.js), or null.
  bridgeLanes(ids) {
    for (const id of ids ?? []) {
      const v = this.data?.bridge_lanes?.[String(id)];
      if (v) return { lanes: v[0], oneway: v[1], dx: v[2] ?? 0, dz: v[3] ?? 0 };
    }
    return null;
  }

  // A bridge point [x, z, ...] moved by the network's shift field (scenery_roads.py), or as is.
  shift(q) {
    const f = this.data?.field;
    if (!f) return q;
    const { x0, z0, cell, rows, cols, d } = f;
    const u = Math.min(Math.max((q[0] - x0) / cell, 0), cols - 1.001), v = Math.min(Math.max((q[1] - z0) / cell, 0), rows - 1.001);
    const i = Math.floor(u), j = Math.floor(v), fx = u - i, fz = v - j;
    const at = (r, c, k) => d[(r * cols + c) * 2 + k];
    const lerp = (k) => (at(j, i, k) * (1 - fx) + at(j, i + 1, k) * fx) * (1 - fz) + (at(j + 1, i, k) * (1 - fx) + at(j + 1, i + 1, k) * fx) * fz;
    const [x0b, z0b, x1b, z1b] = this.data.box;
    if (q[0] < x0b - 300 || q[0] > x1b + 300 || q[1] < z0b - 300 || q[1] > z1b + 300) return q;
    return [q[0] + lerp(0), q[1] + lerp(1), ...q.slice(2)];
  }

  async load(scenery) {
    this.clear();
    if (!ROAD_PROTO || !scenery) return;
    try {
      const r = await fetch(sceneryUrl(scenery, "proto_roads.json"));
      this.data = r.ok ? await r.json() : null;
    } catch {
      this.data = null;
    }
  }

  // Builds once the height tiles under the box are here.
  update() {
    if (!this.data || this.built) return;
    const [x0, z0, x1, z1] = this.data.box;
    for (let tz = Math.floor(z0 / 4000); tz <= Math.floor(z1 / 4000); tz++) for (let tx = Math.floor(x0 / 4000); tx <= Math.floor(x1 / 4000); tx++) if (!world.tileReady(tx, tz)) return;
    const ground = drawnGround(world.tiles, NEAR_SEGMENTS);
    const height = (x, z) => ground.height(x, z);
    const a = { pos: [], nrm: [], col: [], road: [], mark: [], idx: [] };
    for (const r of this.data.roads) {
      const colour = r.colour !== null && r.colour !== undefined ? imageryColour(r.colour) : ASPHALT;
      ribbon(r.pts, r.width, r.lanes, r.oneway, colour, height, a);
    }
    const roads = new THREE.Mesh(geometry(a), this.material);
    roads.receiveShadow = true;
    this.group.add(roads);
    const m = { pos: [], nrm: [], road: [], idx: [] };
    for (const k of this.data.marks) {
      const depth = k.kind === "stop" ? 0.4 : 4.0, hw = k.width / 2 - 0.5;
      const rx = -k.uz, rz = k.ux; // across the road
      const corners = [[-depth / 2, -hw], [depth / 2, -hw], [-depth / 2, hw], [depth / 2, hw]];
      const v = m.pos.length / 3;
      for (const [along, across] of corners) {
        const x = k.x + k.ux * along + rx * across, z = k.z + k.uz * along + rz * across;
        m.pos.push(x, height(x, z) + LIFT_M + 0.02, z);
        m.nrm.push(0, 1, 0);
        m.road.push(along, across + hw, k.kind === "stop" ? 1 : 0, 0);
      }
      m.idx.push(v, v + 1, v + 2, v + 1, v + 3, v + 2);
    }
    if (m.idx.length) this.group.add(new THREE.Mesh(geometry(m), this.marksMaterial));
    this.built = true;
  }

  clear() {
    this.group.traverse((o) => o.geometry?.dispose());
    this.group.clear();
    this.built = false;
  }
}
