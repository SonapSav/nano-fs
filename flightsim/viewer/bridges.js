// Bridges of a real-world region (its bridges.json: every OSM road and railway bridge as
// a deck profile, flightsim/world/scenery_bridges.py): the deck (road on top, girder
// fascia, parapets, underside) and piers where it stands high enough above the ground or
// the water, and the landmark bridges' structures: Sheikh Zayed Bridge's wave of arches,
// Al Maqta's bowstring arches, Sheikh Khalifa Bridge's haunched box girder on V-piers.
// Their dimensions come from the region file (sources in docs/REFERENCES.md); sizes the
// sources do not give are project choices, marked here. Grouped by 4 km tile, drawn
// within VIEW_M of the camera. World frame: x east, y up, z south. Visual only.

import * as THREE from "three";
import { steel, stone } from "./landmarkKit.js";

const TILE_M = 4000;
const VIEW_M = 16000;
const PARAPET_M = 1.0, PARAPET_W = 0.4; // (project choices, as the piers below)
const PIER_MIN_M = 2.5; // no pier where the deck's underside is lower than this above the ground
const PIER_SPACING_M = { land: 35, water: 45 };
const SEA_FLOOR_M = -4; // piers in water go this far down (out of sight)

// --- Geometry collection -------------------------------------------------------------------

class Collector {
  constructor() {
    this.pos = [];
    this.nrm = [];
  }
  // A triangle, its face normal turned toward `hint` (when given).
  tri(a, b, c, hint) {
    const n = new THREE.Vector3().subVectors(b, a).cross(new THREE.Vector3().subVectors(c, a));
    if (n.lengthSq() < 1e-12) return;
    n.normalize();
    if (hint && n.dot(hint) < 0) {
      [b, c] = [c, b];
      n.negate();
    }
    for (const p of [a, b, c]) {
      this.pos.push(p.x, p.y, p.z);
      this.nrm.push(n.x, n.y, n.z);
    }
  }
  quad(a, b, c, d, hint) {
    this.tri(a, b, c, hint);
    this.tri(a, c, d, hint);
  }
  geometry() {
    if (!this.pos.length) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(this.nrm, 3));
    g.computeBoundingSphere();
    return g;
  }
}

const V = (x, y, z) => new THREE.Vector3(x, y, z);

// A box between points a and b (its long axis), `across` (unit, horizontal) the direction
// of its width, `w` wide and `t` thick.
function strut(col, a, b, across, w, t) {
  const axis = new THREE.Vector3().subVectors(b, a).normalize();
  const u = across.clone().sub(axis.clone().multiplyScalar(across.dot(axis))).normalize().multiplyScalar(w / 2);
  const v = new THREE.Vector3().crossVectors(axis, u).normalize().multiplyScalar(t / 2);
  const c = (p, su, sv) => p.clone().addScaledVector(u, su).addScaledVector(v, sv);
  const corners = [[1, 1], [-1, 1], [-1, -1], [1, -1]];
  for (let k = 0; k < 4; k++) {
    const [s0, t0] = corners[k], [s1, t1] = corners[(k + 1) % 4];
    const hint = u.clone().multiplyScalar(s0 + s1).add(v.clone().multiplyScalar(t0 + t1));
    col.quad(c(a, s0, t0), c(a, s1, t1), c(b, s1, t1), c(b, s0, t0), hint);
  }
}

// A tube along points (a CatmullRom curve), radius r.
function tube(points, r, segments = 64) {
  return new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points), segments, r, 10, false);
}

// --- A bridge's centreline ----------------------------------------------------------------

// Samples with arc length, horizontal unit direction and side (left) normal, and the
// mitre scale for the deck's edges.
function centreline(b) {
  const p = b.pts.map(([x, z, y, g, wet]) => ({ x, z, y, g, wet: Boolean(wet) }));
  let s = 0;
  p.forEach((q, i) => {
    if (i) s += Math.hypot(q.x - p[i - 1].x, q.z - p[i - 1].z);
    q.s = s;
  });
  const dir = (i) => {
    const a = p[Math.max(0, i - 1)], c = p[Math.min(p.length - 1, i + 1)];
    const dx = c.x - a.x, dz = c.z - a.z, l = Math.hypot(dx, dz) || 1;
    return [dx / l, dz / l];
  };
  p.forEach((q, i) => {
    [q.ux, q.uz] = dir(i);
    q.nx = q.uz; // left of the direction of travel (x east, z south)
    q.nz = -q.ux;
  });
  return p;
}

// The point at arc length s (interpolated), with the same fields.
function at(p, s) {
  if (s <= 0) return p[0];
  if (s >= p[p.length - 1].s) return p[p.length - 1];
  let i = 1;
  while (p[i].s < s) i++;
  const a = p[i - 1], c = p[i], k = (s - a.s) / (c.s - a.s || 1);
  const r = {};
  for (const key of ["x", "z", "y", "g", "ux", "uz", "nx", "nz", "s"]) r[key] = a[key] + (c[key] - a[key]) * k;
  r.wet = k < 0.5 ? a.wet : c.wet;
  return r;
}

// The longest run of water under the bridge: {s0, s1} or null.
function waterRun(p) {
  let best = null, start = null;
  p.forEach((q, i) => {
    if (q.wet && start === null) start = q.s;
    if ((!q.wet || i === p.length - 1) && start !== null) {
      const end = q.wet ? q.s : p[i - 1].s;
      if (!best || end - start > best.s1 - best.s0) best = { s0: start, s1: end };
      start = null;
    }
  });
  return best;
}

// --- Deck and piers -----------------------------------------------------------------------

function deck(b, p, cols, depthAt, pierAt) {
  const hw = b.width_m / 2;
  const sec = p.map((q) => {
    const d = depthAt(q.s), y = q.y;
    const L = (o, h) => V(q.x + q.nx * o, h, q.z + q.nz * o);
    return {
      Lt: L(hw, y), Rt: L(-hw, y), // road surface edges
      Lp: L(hw, y + PARAPET_M), Rp: L(-hw, y + PARAPET_M), // parapet tops (outer)
      Lpi: L(hw - PARAPET_W, y + PARAPET_M), Rpi: L(-hw + PARAPET_W, y + PARAPET_M),
      Li: L(hw - PARAPET_W, y), Ri: L(-hw + PARAPET_W, y),
      Lb: L(hw, y - d), Rb: L(-hw, y - d), // underside edges
      n: V(q.nx, 0, q.nz),
    };
  });
  const up = V(0, 1, 0), down = V(0, -1, 0);
  for (let i = 0; i + 1 < sec.length; i++) {
    const a = sec[i], c = sec[i + 1], n = a.n, m = n.clone().negate();
    cols.road.quad(a.Li, a.Ri, c.Ri, c.Li, up);
    cols.concrete.quad(a.Lb, a.Lp, c.Lp, c.Lb, n); // left fascia and parapet's outer face
    cols.concrete.quad(a.Rb, a.Rp, c.Rp, c.Rb, m);
    cols.concrete.quad(a.Li, a.Lpi, c.Lpi, c.Li, m); // parapets' inner faces
    cols.concrete.quad(a.Ri, a.Rpi, c.Rpi, c.Ri, n);
    cols.concrete.quad(a.Lp, a.Lpi, c.Lpi, c.Lp, up); // parapet caps
    cols.concrete.quad(a.Rp, a.Rpi, c.Rpi, c.Rp, up);
    cols.concrete.quad(a.Lb, a.Rb, c.Rb, c.Lb, down);
  }
  // Piers: at `pierAt` positions (arc lengths), where the underside is high enough.
  for (const s of pierAt) {
    const q = at(p, s), bottom = q.y - depthAt(s), foot = q.wet ? SEA_FLOOR_M : q.g - 0.5;
    if (bottom - Math.max(q.g, 0) < PIER_MIN_M) continue;
    const c = V(q.x, 0, q.z), w = Math.max(3, 0.55 * b.width_m);
    strut(cols.concrete, V(c.x, foot, c.z), V(c.x, bottom + 0.2, c.z), V(q.nx, 0, q.nz), w, 2.2);
  }
}

function spacedPiers(p, spacing) {
  const L = p[p.length - 1].s, out = [];
  for (let s = spacing; s < L - 8; s += spacing) out.push(s);
  return out;
}

// --- Landmark structures ------------------------------------------------------------------

// Sheikh Zayed Bridge: a wave of arches over the median between the carriageways, the
// principal arch (`main_arch_m` long, `arch_top_m` above the water) over the channel and
// a lower arch either side; each arch two steel strands that cross at its crown (the
// "S" seen from above), hangers down to the decks every 15 m. Side arches, strand sizes
// and hanger spacing: project choices.
function waveArch(decks, cols) {
  const ref = decks.reduce((a, d) => (d.p[d.p.length - 1].s > a.p[a.p.length - 1].s ? d : a));
  const others = decks.filter((d) => d !== ref).flatMap((d) => d.p);
  const median = (q) => {
    if (!others.length) return { x: q.x, z: q.z, half: 0 };
    let best = null, bd = Infinity;
    for (const o of others) {
      const d = Math.hypot(o.x - q.x, o.z - q.z);
      if (d < bd) [best, bd] = [o, d];
    }
    return { x: (q.x + best.x) / 2, z: (q.z + best.z) / 2, half: bd / 2 };
  };
  const st = ref.b.structure, run = waterRun(ref.p) ?? { s0: 0, s1: ref.p[ref.p.length - 1].s };
  const sc = (run.s0 + run.s1) / 2, A = st.main_arch_m, S = st.side_arch_m;
  const arches = [[sc - A / 2, sc + A / 2, st.arch_top_m, 1.6], [sc - A / 2 - S, sc - A / 2, st.side_arch_top_m, 1.1], [sc + A / 2, sc + A / 2 + S, st.side_arch_top_m, 1.1]];
  const geos = [];
  for (const [s0, s1, top, r] of arches) {
    for (const sign of [1, -1]) {
      const pts = [];
      for (let k = 0; k <= 40; k++) {
        const t = k / 40, q = at(ref.p, s0 + (s1 - s0) * t), m = median(q);
        const spring = Math.max(q.g, 0) + 2;
        const off = sign * m.half * 0.85 * Math.cos(Math.PI * t), y = spring + (top - spring) * Math.sin(Math.PI * t) ** 0.85;
        pts.push(V(m.x + q.nx * off, y, m.z + q.nz * off));
      }
      geos.push(tube(pts, r, 80));
      // Hangers to the deck on this strand's side.
      for (let s = s0 + 15; s < s1 - 10; s += 15) {
        const t = (s - s0) / (s1 - s0), q = at(ref.p, s), m = median(q), k = Math.round(t * 40), a = pts[k];
        if (a.y < q.y + 4) continue;
        const side = sign * Math.cos(Math.PI * t) >= 0 ? 1 : -1; // the carriageway under the strand
        const d = V(m.x + q.nx * side * (m.half - ref.b.width_m / 2 + 0.6), q.y + PARAPET_M, m.z + q.nz * side * (m.half - ref.b.width_m / 2 + 0.6));
        strut(cols.steel, a, d, V(q.ux, 0, q.uz), 0.35, 0.35);
      }
    }
  }
  return geos;
}

// Al Maqta Bridge: a steel tied (bowstring) arch over each carriageway, spanning the
// channel (its longest run of water, 50-140 m), rising `arch_rise_ratio` of its span
// above the deck; hangers every 6 m and bracing across the crown. Project choices but
// the type (sources: a steel tied arch per direction).
function tiedArch(d, cols) {
  const st = d.b.structure, run = waterRun(d.p);
  if (!run) return [];
  const mid = (run.s0 + run.s1) / 2, span = Math.min(140, Math.max(50, run.s1 - run.s0)), s0 = mid - span / 2;
  const rise = st.arch_rise_ratio * span, off = d.b.width_m / 2 + 0.4;
  const rib = (side) => {
    const pts = [];
    for (let k = 0; k <= 30; k++) {
      const q = at(d.p, s0 + (span * k) / 30);
      pts.push(V(q.x + q.nx * side * off, q.y + PARAPET_M * 0.5 + rise * Math.sin((Math.PI * k) / 30), q.z + q.nz * side * off));
    }
    return pts;
  };
  const L = rib(1), R = rib(-1), geos = [tube(L, 0.7, 48), tube(R, 0.7, 48)];
  for (let s = 6; s < span - 3; s += 6) {
    const k = (s / span) * 30, i = Math.round(k), q = at(d.p, s0 + s);
    for (const [pts, side] of [[L, 1], [R, -1]]) strut(cols.paint, pts[i], V(q.x + q.nx * side * off, q.y, q.z + q.nz * side * off), V(q.ux, 0, q.uz), 0.18, 0.18);
    if (Math.abs(s - span / 2) < span * 0.28 && Math.round(s / 6) % 2 === 0) strut(cols.paint, L[i], R[i], V(q.ux, 0, q.uz), 0.3, 0.3);
  }
  return geos;
}

// Sheikh Khalifa Bridge: the main unit's spans (`main_spans_m`) placed with the longest
// (200 m) at the deck's highest point; the girder deepens to `girder_max_m` over the two
// piers flanking it (haunches over 30 % of the span; their shape a project choice), which
// are V-piers: two legs splayed `v_pier_deg` from the vertical along the bridge, three
// across the deck (sources: two sets of triple piers inclined 27.45 deg).
function boxGirder(d) {
  const st = d.b.structure, p = d.p;
  const peak = p.reduce((a, q) => (q.y > a.y ? q : a)).s;
  const spans = st.main_spans_m, k = spans.indexOf(Math.max(...spans));
  let s = peak - spans.slice(0, k).reduce((a, v) => a + v, 0) - spans[k] / 2;
  const piers = [s];
  for (const v of spans) piers.push((s += v));
  const main = [piers[k], piers[k + 1]], reach = 0.3 * spans[k];
  const depthAt = (x) => {
    const near = Math.min(...main.map((m) => Math.abs(x - m)));
    return d.b.depth_m + (st.girder_max_m - d.b.depth_m) * Math.max(0, 1 - near / reach) ** 2;
  };
  const L = p[p.length - 1].s;
  const others = [...piers.filter((x) => !main.includes(x) && x > 8 && x < L - 8), ...spacedPiers(p, 55).filter((x) => x < piers[0] - 30 || x > piers[piers.length - 1] + 30)];
  const vPiers = (cols) => {
    const tan = Math.tan((st.v_pier_deg * Math.PI) / 180);
    for (const m of main) {
      const q = at(p, m), foot = q.wet ? SEA_FLOOR_M : q.g, top = q.y - depthAt(m);
      const h = top - Math.max(foot, 0);
      for (const across of [-1, 0, 1]) {
        const o = across * d.b.width_m * 0.3;
        const base = V(q.x + q.nx * o, foot, q.z + q.nz * o);
        for (const dir of [1, -1]) {
          const tq = at(p, m + dir * h * tan);
          strut(cols.concrete, base, V(tq.x + tq.nx * o, tq.y - depthAt(tq.s) + 0.3, tq.z + tq.nz * o), V(q.nx, 0, q.nz), 2.4, 2.0);
        }
      }
    }
  };
  return { depthAt, piers: others, vPiers };
}

// --- Bridges ------------------------------------------------------------------------------

export class Bridges {
  constructor(scene) {
    this.group = new THREE.Group();
    scene.add(this.group);
    this.materials = {
      road: new THREE.MeshStandardMaterial({ color: 0x4b4d50, roughness: 0.92, metalness: 0 }),
      concrete: stone(0xc7c2b6, { roughness: 0.75, key: "bridge-concrete" }),
      steel: steel(),
      paint: new THREE.MeshStandardMaterial({ color: 0x7d888c, roughness: 0.5, metalness: 0.6, envMapIntensity: 0.8 }),
    };
    this.materials.steel.color.setHex(0xe9e8e3); // Sheikh Zayed Bridge's white arches
  }

  build(list) {
    this.clear();
    const tiles = new Map(), landmarks = new Map();
    for (const b of list ?? []) {
      if (b.pts.length < 2) continue;
      const p = centreline(b), mid = p[Math.floor(p.length / 2)];
      const key = `${Math.floor(mid.x / TILE_M)},${Math.floor(mid.z / TILE_M)}`;
      if (!tiles.has(key)) tiles.set(key, { road: new Collector(), concrete: new Collector(), steel: new Collector(), paint: new Collector(), tubes: [], x: (Math.floor(mid.x / TILE_M) + 0.5) * TILE_M, z: (Math.floor(mid.z / TILE_M) + 0.5) * TILE_M });
      const cols = tiles.get(key), d = { b, p };
      const kind = b.structure?.kind;
      b.depth_m ??= 1.8; // builds before 2026-10-10 did not record it (scenery_bridges.py DECK_DEPTH_M)
      let depthAt = () => b.depth_m, piers = null;
      if (kind === "box_girder") {
        const g = boxGirder(d);
        depthAt = g.depthAt;
        piers = g.piers;
        g.vPiers(cols);
      }
      if (kind === "tied_arch") cols.tubes.push(...tiedArch(d, cols).map((g) => [g, "paint"]));
      if (kind === "wave_arch") {
        if (!landmarks.has(b.landmark)) landmarks.set(b.landmark, { cols, decks: [] });
        landmarks.get(b.landmark).decks.push(d);
      }
      piers ??= spacedPiers(p, p.some((q) => q.wet) ? PIER_SPACING_M.water : PIER_SPACING_M.land);
      deck(b, p, cols, depthAt, piers);
    }
    for (const { cols, decks } of landmarks.values()) cols.tubes.push(...waveArch(decks, cols).map((g) => [g, "steel"]));
    this.tiles = [];
    for (const t of tiles.values()) {
      const group = new THREE.Group();
      for (const k of ["road", "concrete", "steel", "paint"]) {
        const g = t[k].geometry();
        if (g) group.add(this.mesh(g, this.materials[k]));
      }
      const byMat = {};
      for (const [g, k] of t.tubes) (byMat[k] ??= []).push(g);
      for (const [k, gs] of Object.entries(byMat)) for (const g of gs) group.add(this.mesh(g, this.materials[k]));
      this.group.add(group);
      this.tiles.push({ group, x: t.x, z: t.z });
    }
  }

  mesh(g, material) {
    const m = new THREE.Mesh(g, material);
    m.castShadow = true;
    return m;
  }

  // Only the tiles within VIEW_M of the camera.
  update(cameraPos) {
    for (const t of this.tiles ?? []) t.group.visible = Math.hypot(t.x - cameraPos.x, t.z - cameraPos.z) < VIEW_M;
  }

  // The landmarks' environment map (Landmarks.setEnvironment), or null.
  setEnvironment(texture) {
    for (const m of Object.values(this.materials)) {
      m.envMap = texture ?? null;
      m.needsUpdate = true;
    }
  }

  clear() {
    this.group.traverse((o) => o.geometry?.dispose());
    this.group.clear();
    this.tiles = [];
  }
}
