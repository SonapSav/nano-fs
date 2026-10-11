// A tile of the drawn road network (tiles/rd_<ix>_<iz>.json, flightsim/world/scenery_roads.py)
// as arrays, draped on the ground as the terrain draws that tile (`segments`): built in the
// tile worker (terrainWorker.js), no three.js here. "roadnet": ribbons, kerb fillets and
// gores (asphalt; attributes aRoad = metres along, metres across, lanes, one-way and aMark =
// marking weight, as roadNetwork.js roadMaterial); "roadmarks": zebras, stop and give-way
// lines, gore hatching (aRoad = along, across, kind). Visual only.

import { drawnGround } from "./featureGeometry.js";

const LIFT_M = 0.06;

class Arrays {
  constructor() {
    this.pos = [];
    this.road = [];
    this.mark = [];
    this.idx = [];
  }
  out() {
    if (!this.idx.length) return null;
    const n = this.pos.length / 3;
    return {
      position: new Float32Array(this.pos), normal: new Float32Array(n * 3).map((_, i) => (i % 3 === 1 ? 1 : 0)),
      aRoad: new Float32Array(this.road), aMark: new Float32Array(this.mark), index: new Uint32Array(this.idx),
    };  // fmt: skip
  }
}

// A road ribbon: points [[x, z, mark], ...] (centreline), its width; `s0` the network's arc
// length at its start (dashes run on).
function ribbon(points, widthM, lanes, oneway, s0, height, into) {
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
      into.road.push(s, hw * side, lanes, oneway ? 1 : 0);
      into.mark.push(mark);
    }
    if (i) {
      const v = into.pos.length / 3 - 4;
      into.idx.push(v, v + 1, v + 2, v + 1, v + 3, v + 2); // facing up
    }
  }
}

// A triangulated polygon ({v: [x, z, ...], t: [i, ...]}), each triangle facing up.
function polygon(t, into, road, height, lift = 0) {
  const base = into.pos.length / 3;
  for (let i = 0; i < t.v.length; i += 2) {
    const x = t.v[i], z = t.v[i + 1];
    into.pos.push(x, height(x, z) + LIFT_M + lift, z);
    into.road.push(...road(x, z));
    into.mark.push(0);
  }
  for (let i = 0; i < t.t.length; i += 3) {
    const [p, q, r] = [t.t[i], t.t[i + 1], t.t[i + 2]];
    const ux = t.v[2 * q] - t.v[2 * p], uz = t.v[2 * q + 1] - t.v[2 * p + 1], vx = t.v[2 * r] - t.v[2 * p], vz = t.v[2 * r + 1] - t.v[2 * p + 1];
    if (uz * vx - ux * vz > 0) into.idx.push(base + p, base + q, base + r);
    else into.idx.push(base + p, base + r, base + q);
  }
}

// Fewer points (Douglas-Peucker): within TOL_M of the centreline, the marking weight
// counted as MARK_M metres (its transitions kept).
const TOL_M = 0.3, MARK_M = 20;
function simplify(pts) {
  if (pts.length <= 2) return pts;
  const keep = new Uint8Array(pts.length);
  keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const [i, j] = stack.pop();
    const [ax, az, am] = pts[i], [bx, bz, bm] = pts[j];
    const dx = bx - ax, dz = bz - az, l2 = dx * dx + dz * dz || 1;
    let worst = -1, at = -1;
    for (let k = i + 1; k < j; k++) {
      const [x, z, m] = pts[k];
      const t = Math.min(1, Math.max(0, ((x - ax) * dx + (z - az) * dz) / l2));
      const e = Math.max(Math.hypot(x - ax - dx * t, z - az - dz * t), Math.abs(m - (am + (bm - am) * t)) * MARK_M);
      if (e > worst) [worst, at] = [e, k];
    }
    if (worst > TOL_M) {
      keep[at] = 1;
      stack.push([i, at], [at, j]);
    }
  }
  return pts.filter((_, k) => keep[k]);
}

// The simplified centreline with a point wherever it crosses the drawn ground's edges (so
// the ribbon follows the ground's facets).
function draped(pts, ground) {
  const out = [pts[0]];
  for (let k = 1; k < pts.length; k++) {
    const [ax, az, am] = pts[k - 1], [bx, bz, bm] = pts[k];
    const L = Math.hypot(bx - ax, bz - az);
    let last = 0;
    for (const t of ground.cuts(ax, az, bx, bz)) {
      if ((t - last) * L < 0.5 || (1 - t) * L < 0.5) continue; // (not on a point or another cut: no slivers)
      out.push([ax + (bx - ax) * t, az + (bz - az) * t, am + (bm - am) * t]);
      last = t;
    }
    out.push(pts[k]);
  }
  return out;
}

export function roadTileArrays(data, tiles, segments) {
  const ground = drawnGround(tiles, segments);
  const height = (x, z) => ground.height(x, z);
  const a = new Arrays();
  for (const r of data.roads) {
    const pts = draped(simplify(r.pts), ground);
    if (pts.length >= 2) ribbon(pts, r.width, r.lanes, r.oneway, r.s0 ?? 0, height, a);
  }
  // Kerb fillets and gores (scenery_roads.py junction_shapes): plain asphalt, no markings.
  for (const t of [...(data.fills ?? []), ...(data.gores ?? [])]) polygon(t, a, () => [0, 0, 0, 0], height);
  const out = { roadnet: a.out(), roadmarks: null };
  if (segments < 48) return out; // far away: no zebras, stop lines or hatching
  const m = new Arrays();
  const KIND = { zebra: 0, stop: 1, giveway: 2 };
  for (const k of data.marks ?? []) {
    const depth = k.kind === "stop" ? 0.4 : k.kind === "giveway" ? 0.3 : 4.0, hw = k.width / 2 - 0.5;
    const rx = -k.uz, rz = k.ux; // across the road
    const a0 = Math.max(k.a0 ?? -hw, -hw), a1 = Math.min(k.a1 ?? hw, hw);
    const v = m.pos.length / 3;
    for (const [along, across] of [[-depth / 2, a0], [depth / 2, a0], [-depth / 2, a1], [depth / 2, a1]]) {
      const x = k.x + k.ux * along + rx * across, z = k.z + k.uz * along + rz * across;
      m.pos.push(x, height(x, z) + LIFT_M + 0.02, z);
      m.road.push(along, across + hw, KIND[k.kind] ?? 0, 0);
      m.mark.push(0);
    }
    m.idx.push(v, v + 1, v + 2, v + 1, v + 3, v + 2);
  }
  for (const t of data.gores ?? []) {
    const [ux, uz] = t.u;
    polygon(t, m, (x, z) => [x * ux + z * uz, -x * uz + z * ux, 3, 0], height, 0.02);
  }
  out.roadmarks = m.out();
  return out;
}
