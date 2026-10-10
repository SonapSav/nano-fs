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

function deck(b, p, cols, depthAt, pierAt, fascia = cols.concrete) {
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
    fascia.quad(a.Lb, a.Lp, c.Lp, c.Lb, n); // left fascia and parapet's outer face
    fascia.quad(a.Rb, a.Rp, c.Rp, c.Rb, m);
    fascia.quad(a.Li, a.Lpi, c.Lpi, c.Li, m); // parapets' inner faces
    fascia.quad(a.Ri, a.Rpi, c.Rpi, c.Ri, n);
    fascia.quad(a.Lp, a.Lpi, c.Lpi, c.Lp, up); // parapet caps
    fascia.quad(a.Rp, a.Rpi, c.Rpi, c.Rp, up);
    fascia.quad(a.Lb, a.Rb, c.Rb, c.Lb, down);
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

// Sheikh Zayed Bridge (Zaha Hadid; sources in docs/REFERENCES.md): a white spine in the
// void between the twin decks that rises into steel arches above them and plunges below
// them to mass concrete piers at the water: in profile one continuous wave. Each arch is
// a pair of flat box ribs (`rib_width_m` wide, `rib_depth_m` deep from spring to crown:
// 5-8 m published) that part over the span (`ribs_apart_m`, the lens seen from above);
// below the decks they are one wider concrete spine. The arches (`arches`: centre on the
// map, span, top above the water, measured on the 1 m imagery) sit in the void, the last
// swinging out past the southern deck (`side`: 0 the void's middle, 1 outside that deck),
// as the spine "splits and splays ... to the outside of the roadways at the other end".
// Hangers from the ribs to the nearest deck edge, cross beams under the decks in the void,
// white curved lamp posts along both outer edges. Rises of the side arches, hanger and
// beam spacing, lamp posts: project choices. Returns the stretch the spine carries (no
// ordinary piers there).
function waveArch(decks, white) {
  const ref = decks.reduce((a, d) => (d.p[d.p.length - 1].s > a.p[a.p.length - 1].s ? d : a));
  const st = ref.b.structure, W = ref.b.width_m;
  const others = decks.filter((d) => d !== ref).map((d) => d.p);
  // The nearest point on a centreline (interpolated between its samples): {x, z, s, d}.
  const nearest = (p, x, z) => {
    let best = { d: Infinity };
    for (let i = 0; i + 1 < p.length; i++) {
      const a = p[i], b = p[i + 1], dx = b.x - a.x, dz = b.z - a.z, l2 = dx * dx + dz * dz || 1;
      const k = Math.min(1, Math.max(0, ((x - a.x) * dx + (z - a.z) * dz) / l2));
      const px = a.x + dx * k, pz = a.z + dz * k, d = Math.hypot(px - x, pz - z);
      if (d < best.d) best = { x: px, z: pz, s: a.s + (b.s - a.s) * k, d };
    }
    return best;
  };
  const project = (x, z) => nearest(ref.p, x, z).s;
  const otherPts = others.flat();
  const southRef = otherPts.length && ref.p.reduce((t, q) => t + q.z, 0) / ref.p.length > otherPts.reduce((t, q) => t + q.z, 0) / otherPts.length;
  // The void's middle at arc length s, the lateral unit vector toward the southern deck and
  // half the distance between the deck centrelines.
  const frame = (s) => {
    const q = at(ref.p, s);
    if (!others.length) return { q, x: q.x, z: q.z, lx: q.nx, lz: q.nz, half: 0 };
    const best = others.map((p) => nearest(p, q.x, q.z)).reduce((a, b) => (b.d < a.d ? b : a)), bd = best.d;
    const sg = southRef ? -1 : 1; // from the ref deck toward the other, flipped if ref is the southern one
    return { q, x: (q.x + best.x) / 2, z: (q.z + best.z) / 2, lx: (sg * (best.x - q.x)) / bd, lz: (sg * (best.z - q.z)) / bd, half: bd / 2 };
  };
  const arches = st.arches.map((a) => {
    const c = project(a.centre_xz[0], a.centre_xz[1]);
    return { ...a, s0: c - a.span_m / 2, s1: c + a.span_m / 2, westFirst: at(ref.p, c + 1).x > at(ref.p, c - 1).x };
  }).sort((x, y) => x.s0 - y.s0);
  const [rw, d0, d1] = [st.rib_width_m, st.rib_depth_m[0], st.rib_depth_m[1]];
  const low = st.pier_low_m, lead = 60, tail = 40;
  const S0 = arches[0].s0 - lead, S1 = arches[arches.length - 1].s1 + tail;
  // Profile: {y (centre of the spine), sep (rib centres apart), f (side), above (an arch)}.
  const profile = (s) => {
    const { q } = frame(s), yd = q.y, ground = q.wet ? low : Math.max(q.g, low);
    for (const [i, a] of arches.entries()) {
      if (s >= a.s0 && s <= a.s1) {
        // Asymmetric (the sources): the crown `crown_at` of the span from the arch's west
        // end; nearly straight legs to a rounded top, as in photographs (1 - |x|^1.6).
        const t = (s - a.s0) / (a.s1 - a.s0), tw = a.westFirst ? t : 1 - t, c = a.crown_at ?? 0.5;
        const h = 1 - Math.abs(tw < c ? 1 - tw / c : (tw - c) / (1 - c)) ** 1.6;
        return { y: yd + (a.top_m - d1 / 2 - yd) * h, sep: a.ribs_apart_m * Math.sqrt(h), f: a.side, above: true, t, depth: d0 + (d1 - d0) * h, i };
      }
      const b = arches[i + 1];
      if (b && s > a.s1 && s < b.s0) {
        // Below the decks: down to a mass pier at the water, the spine deepening toward it
        // (the "dune" piers of photographs; how much: project choice).
        const u = (s - a.s1) / (b.s0 - a.s1), dip = Math.sin(Math.PI * u), depth = d0 * (1 + 0.8 * dip);
        return { y: yd - (yd - low - depth / 2) * dip, sep: 0, f: a.side + (b.side - a.side) * u, above: false, depth };
      }
    }
    const first = s < arches[0].s0, a = first ? arches[0] : arches[arches.length - 1];
    const u = first ? (s - S0) / lead : (S1 - s) / tail; // 0 at the spine's end, 1 at the arch
    return { y: ground - d0 / 2 + (yd - ground + d0 / 2) * Math.sin((Math.PI / 2) * u), sep: 0, f: a.side, above: false, depth: d0 };
  };
  const lateral = (fr, f) => f * (fr.half + W / 2 + rw); // offset of the spine from the void's middle
  // Sweep a box section along stations (centre, lateral unit, width, depth), each
  // station's section square to the smoothed tangent there, so segments join cleanly.
  const sweep = (secs) => {
    const rings = secs.map((S, k) => {
      const a = secs[Math.max(0, k - 1)].c, b = secs[Math.min(secs.length - 1, k + 1)].c;
      const T = new THREE.Vector3().subVectors(b, a).normalize();
      const L = V(S.lx, 0, S.lz), N = new THREE.Vector3().crossVectors(L, T).normalize();
      if (N.y < 0) N.negate();
      return [[1, 1], [-1, 1], [-1, -1], [1, -1]].map(([u, v]) => S.c.clone().addScaledVector(L, (u * S.w) / 2).addScaledVector(N, (v * S.d) / 2));
    });
    for (let k = 0; k + 1 < rings.length; k++) {
      const [ca, cb] = [rings[k], rings[k + 1]];
      for (let e = 0; e < 4; e++) {
        const e1 = (e + 1) % 4, mid = ca[e].clone().add(ca[e1]).multiplyScalar(0.5).sub(secs[k].c);
        white.quad(ca[e], ca[e1], cb[e1], cb[e], mid);
      }
    }
  };
  const STEP = 2;
  const spine = [], ribs = [[], []];
  for (let s = S0; s <= S1 + 1e-6; s += STEP) {
    const fr = frame(s), pr = profile(s), o = lateral(fr, pr.f);
    const c = (off) => V(fr.x + fr.lx * off, pr.y, fr.z + fr.lz * off);
    if (pr.above) {
      for (const [k, sg] of [[0, -1], [1, 1]]) ribs[k].push({ c: c(o + (sg * pr.sep) / 2), lx: fr.lx, lz: fr.lz, w: rw, d: pr.depth, s, i: pr.i });
      if (spine.length) {
        spine.push({ c: c(o), lx: fr.lx, lz: fr.lz, w: rw * 1.5, d: pr.depth });
        sweep(spine);
        spine.length = 0;
      }
    } else {
      spine.push({ c: c(o), lx: fr.lx, lz: fr.lz, w: rw * 1.5, d: pr.depth });
      for (const r of ribs) if (r.length) {
        sweep(r);
        r.length = 0;
      }
    }
  }
  if (spine.length) sweep(spine);
  for (const r of ribs) if (r.length) sweep(r);
  // Hangers and cross beams, every 12 m inside each arch.
  for (const a of arches) {
    for (let s = a.s0 + 10; s < a.s1 - 8; s += 12) {
      const fr = frame(s), pr = profile(s), o = lateral(fr, pr.f);
      const deckY = fr.q.y - ref.b.depth_m;
      const edges = [-fr.half - W / 2, -fr.half + W / 2, fr.half - W / 2, fr.half + W / 2];
      for (const sg of [-1, 1]) {
        const off = o + (sg * pr.sep) / 2, top = pr.y - pr.depth / 2;
        if (top < fr.q.y + 5) continue;
        const e = edges.reduce((b, x) => (Math.abs(x - off) < Math.abs(b - off) ? x : b));
        strut(white, V(fr.x + fr.lx * off, top, fr.z + fr.lz * off), V(fr.x + fr.lx * e, fr.q.y + PARAPET_M, fr.z + fr.lz * e), V(fr.q.ux, 0, fr.q.uz), 0.3, 0.3);
      }
      if (a.side <= 0.5 && fr.half > W / 2) {
        const e0 = -fr.half + W / 2, e1 = fr.half - W / 2;
        strut(white, V(fr.x + fr.lx * e0, deckY + 0.8, fr.z + fr.lz * e0), V(fr.x + fr.lx * e1, deckY + 0.8, fr.z + fr.lz * e1), V(fr.q.ux, 0, fr.q.uz), 1.6, 1.6);
      }
    }
  }
  // Lamp posts: white, `lamp_height_m`, curving in over the road, along both outer edges.
  for (const d of decks) {
    const L = d.p[d.p.length - 1].s, hw = d.b.width_m / 2;
    for (let s = st.lamp_spacing_m / 2; s < L; s += st.lamp_spacing_m) {
      const q = at(d.p, s), fr = frame(project(q.x, q.z));
      const out = (q.nx * (q.x - fr.x) + q.nz * (q.z - fr.z)) >= 0 ? 1 : -1; // the deck's outer side
      const P = (o, h) => V(q.x + q.nx * out * o, q.y + h, q.z + q.nz * out * o);
      const H = st.lamp_height_m, along = V(q.ux, 0, q.uz);
      const pts = [P(hw - 0.4, PARAPET_M), P(hw - 0.4, H - 1.4), P(hw - 0.9, H - 0.2), P(hw - 2.0, H), P(hw - 2.8, H - 0.4)];
      for (let k = 0; k + 1 < pts.length; k++) strut(white, pts[k], pts[k + 1], along, k ? 0.3 : 0.4, k ? 0.3 : 0.4);
    }
  }
  return { s0: S0, s1: S1, onSpine: (x, z) => { const s = project(x, z); return s >= S0 - 10 && s <= S1 + 10; } };
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
      road: new THREE.MeshStandardMaterial({ color: 0x3a3c3f, roughness: 0.95, metalness: 0, envMapIntensity: 0.3 }), // asphalt
      concrete: stone(0xc7c2b6, { roughness: 0.75, key: "bridge-concrete" }),
      steel: steel(),
      paint: new THREE.MeshStandardMaterial({ color: 0x7d888c, roughness: 0.5, metalness: 0.6, envMapIntensity: 0.8 }),
      // Sheikh Zayed Bridge's white painted steel and white concrete (project choice).
      white: new THREE.MeshStandardMaterial({ color: 0xdcdad3, roughness: 0.6, metalness: 0, envMapIntensity: 0.5 }),
    };
  }

  build(list) {
    this.clear();
    const tiles = new Map(), landmarks = new Map();
    for (const b of list ?? []) {
      if (b.pts.length < 2) continue;
      const p = centreline(b), mid = p[Math.floor(p.length / 2)];
      const key = `${Math.floor(mid.x / TILE_M)},${Math.floor(mid.z / TILE_M)}`;
      if (!tiles.has(key)) tiles.set(key, { road: new Collector(), concrete: new Collector(), steel: new Collector(), paint: new Collector(), white: new Collector(), tubes: [], x: (Math.floor(mid.x / TILE_M) + 0.5) * TILE_M, z: (Math.floor(mid.z / TILE_M) + 0.5) * TILE_M });
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
        // Its decks wait for the spine (which carries them: no ordinary piers there).
        b.width_m = b.structure.deck_width_m ?? b.width_m;
        if (!landmarks.has(b.landmark)) landmarks.set(b.landmark, { cols, decks: [] });
        landmarks.get(b.landmark).decks.push({ ...d, cols });
        continue;
      }
      piers ??= spacedPiers(p, p.some((q) => q.wet) ? PIER_SPACING_M.water : PIER_SPACING_M.land);
      deck(b, p, cols, depthAt, piers);
    }
    for (const { cols, decks } of landmarks.values()) {
      for (const d of decks) d.p = centreline(d.b); // with the published width
      const spine = waveArch(decks, cols.white);
      for (const d of decks) {
        const piers = spacedPiers(d.p, d.p.some((q) => q.wet) ? PIER_SPACING_M.water : PIER_SPACING_M.land).filter((s) => {
          const q = at(d.p, s);
          return !spine.onSpine(q.x, q.z);
        });
        deck(d.b, d.p, d.cols, () => d.b.depth_m, piers, d.cols.white);
      }
    }
    this.tiles = [];
    for (const t of tiles.values()) {
      const group = new THREE.Group();
      for (const k of ["road", "concrete", "steel", "paint", "white"]) {
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
