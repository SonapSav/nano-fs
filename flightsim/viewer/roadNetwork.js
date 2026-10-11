// The road network as drawn roads with markings (2026-10-11; `?oldroads` in the URL for
// OSM's plain ribbons): its materials and the bridges' share of roads.json. The road tiles
// (tiles/rd_*.json, flightsim/world/scenery_roads.py: OSM's roads moved onto the imagery's,
// lanes and one-way, junction shapes, crossings) are built in the tile worker
// (roadGeometry.js) and drawn with their terrain tiles (terrain.js). One asphalt for every road, ramp and bridge deck (bridges.js uses roadMaterial):
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
import { sceneryUrl } from "./world.js";

// On unless ?oldroads in the URL (OSM's ribbons as before, 2026-10-11).
export const ROADS = !(typeof location !== "undefined" && new URLSearchParams(location.search).has("oldroads"));
export const ROAD_PROTO = ROADS; // (older name)
export const LANE_M = 3.65; // as scenery_roads.py

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
// The share of a pixel f metres across (centred on t) covered by a line w wide at c: the
// exact box filter, so a line well under a pixel shows faint (w / f) as in the imagery.
float roadBox(float t, float c, float w, float f) { return clamp((min(t + f * 0.5, c + w * 0.5) - max(t - f * 0.5, c - w * 0.5)) / f, 0.0, 1.0); }
// The same for the dashes along (3 m in 9 m): a third far away.
float roadDashLen(float x) { return floor(x / 9.0) * 3.0 + min(fract(x / 9.0) * 9.0, 3.0); }
float roadDash(float s, float f) { return clamp((roadDashLen(s + f * 0.5) - roadDashLen(s - f * 0.5)) / f, 0.0, 1.0); }`;

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
  float f = fwidth(t) + 0.01, fs = fwidth(s) + 0.01; // the pixel's metres across and along
  // Asphalt: patches and grain; darker where the tyres run (two tracks a lane).
  float n = roadNoise(vRoadXZ * 0.08) * 0.6 + roadNoise(vRoadXZ * 0.9) * 0.4;
  float L = lanes * ${LANE_M.toFixed(2)};
  float lane = fract((t + L * 0.5) / ${LANE_M.toFixed(2)}) * ${LANE_M.toFixed(2)} - ${(LANE_M / 2).toFixed(3)};
  float tracks = (roadLine(lane, -0.85, 0.7, 0.3) + roadLine(lane, 0.85, 0.7, 0.3)) * step(abs(t), L * 0.5);
  diffuseColor.rgb = ASPHALT * (0.9 + 0.2 * n) * (1.0 - 0.07 * tracks * vMarkW);
  // Markings, averaged over the pixel (faint where a line is under one), skipped where a
  // pixel is 2-3 m across (a line under 8 % of it).
  float far = 1.0 - smoothstep(2.0, 3.0, f);
  float white = 0.0, yellow = 0.0;
  if (far > 0.0) {
    float LN = ${LANE_M.toFixed(2)};
    white = roadBox(t, L * 0.5, 0.2, f); // right edge
    if (oneway > 0.5) {
      yellow = roadBox(t, -L * 0.5, 0.2, f);
      // The nearest lane line between lanes (k = 1 .. lanes - 1 from the left edge).
      float k = clamp(floor((t + L * 0.5) / LN + 0.5), 1.0, max(lanes - 1.0, 1.0));
      white += roadBox(t, -L * 0.5 + k * LN, 0.15, f) * roadDash(s, fs) * step(1.5, lanes);
    } else {
      white += roadBox(t, -L * 0.5, 0.2, f) + roadBox(t, 0.0, 0.15, f) * roadDash(s, fs);
      // The nearest lane line within each half (k = 1 .. lanes / 2 - 1 from the centre).
      float halves = floor(lanes * 0.5);
      float k = clamp(floor(abs(t) / LN + 0.5), 1.0, max(halves - 1.0, 1.0));
      white += roadBox(abs(t), k * LN, 0.15, f) * roadDash(s, fs) * step(1.5, halves);
    }
    float wear = 0.75 + 0.25 * roadNoise(vRoadXZ * 1.7);
    white = clamp(white, 0.0, 1.0) * vMarkW * wear * far;
    yellow = clamp(yellow, 0.0, 1.0) * vMarkW * wear * far;
  }
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
export function marksMaterial() {
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

export class RegionRoads {
  constructor() {
    this.data = null; // roads.json: the shift field, bridges' lanes and arc lengths
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

  async load(scenery) {
    this.data = null;
    if (!ROADS || !scenery) return;
    try {
      const r = await fetch(sceneryUrl(scenery, "roads.json"));
      this.data = r.ok ? await r.json() : null;
    } catch {
      this.data = null;
    }
  }
}
