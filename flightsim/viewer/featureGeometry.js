// Meshes of a real-world region's OpenStreetMap features (a tile's features file,
// flightsim/world/scenery_osm.py) as typed arrays, without three.js: runs in the tile
// worker. World frame: x east, y up, z south; heights from the region's ground (demCore.js
// heightAt), so roads lie on the terrain the physics flies over.
//
// Roads and railways: ribbons draped every DRAPE_M, lifted ROAD_LIFT_M (the material also
// pulls them forward in depth). Taxiways: ribbons; aprons: polygons (ear clipping).
// Buildings: footprints extruded to their height, flat roofs (vertex colours): the roof
// the imagery's colour over the footprint where the build gives one (scenery_colours.py),
// brightened as the ground's imagery is (terrain.js imageryGain), the walls that blended
// with light stone and render; else stone and render; towers blue-grey glass (project
// choices).

import { TILE_SIZE_M, heightAt } from "./demCore.js";

const DRAPE_M = 25;
const ROAD_LIFT_M = 0.35;
const PAVED_LIFT_M = 0.05; // taxiways (aprons 2 cm lower): aircraft roll on them, so close to the physics' ground
export const ROAD_WIDTH_M = { motorway: 24, trunk: 20, primary: 14, secondary: 11, tertiary: 9, unclassified: 7, residential: 7 };
const RAIL_WIDTH_M = 4;
const TAXIWAY_WIDTH_M = 23;
const TAXILINE_WIDTH_M = 0.45; // taxiway centreline marking (project choice; about 15 cm in reality, wider so it shows)

class Mesh {
  constructor(colours = false, uvs = false) {
    this.pos = [];
    this.nrm = [];
    this.idx = [];
    this.col = colours ? [] : null;
    this.uv = uvs ? [] : null; // three numbers per vertex (the facade attribute)
  }
  vertex(x, y, z, nx, ny, nz, c, u = 0, v = 0, w = 0) {
    this.pos.push(x, y, z);
    this.nrm.push(nx, ny, nz);
    if (this.col) {
      const k = c ?? this.paint; // a vertex's own colour, or the mesh's current paint
      this.col.push(k[0], k[1], k[2]);
    }
    if (this.uv) this.uv.push(u, v, w);
    return this.pos.length / 3 - 1;
  }
  arrays() {
    if (!this.idx.length) return null;
    const out = { position: new Float32Array(this.pos), normal: new Float32Array(this.nrm), index: new Uint32Array(this.idx) };
    if (this.col) out.color = new Float32Array(this.col);
    if (this.uv) out.facade = new Float32Array(this.uv);
    return out;
  }
}

// The ground as drawn: a tile's terrain mesh (demTiles.js) is `segments` x `segments`
// quads of two planar triangles (split along the quad's north-east to south-west
// diagonal), its corners on the region's heights. Ground features drape on it, not on
// the finer heights between its corners: on an embankment or a ramp the drawn terrain
// otherwise rose through the road (or the road floated off its low side).
// `segments` null: the heights themselves, sampled every DRAPE_M (tests, no mesh).
export function drawnGround(tiles, segments) {
  if (!segments) {
    return {
      height: (x, z) => heightAt(tiles, x, z),
      cuts: (x0, z0, x1, z1) => {
        const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, z1 - z0) / DRAPE_M));
        return Array.from({ length: n - 1 }, (_, k) => (k + 1) / n);
      },
    };
  }
  const step = TILE_SIZE_M / segments;
  const corner = (gx, gz) => heightAt(tiles, gx * step, gz * step); // grid lines are shared by neighbouring tiles
  return {
    height(x, z) {
      const u = x / step, v = z / step, j = Math.floor(u), i = Math.floor(v), fx = u - j, fz = v - i;
      const ha = corner(j, i), hb = corner(j + 1, i), hc = corner(j, i + 1);
      if (fx + fz <= 1) return ha + (hb - ha) * fx + (hc - ha) * fz;
      const he = corner(j + 1, i + 1);
      return he + (hc - he) * (1 - fx) + (hb - he) * (1 - fz);
    },
    // Where a segment crosses the mesh's grid lines (x, z) and quad diagonals (u + v whole):
    // its fractions (0..1, exclusive), sorted.
    cuts(x0, z0, x1, z1) {
      const ts = [];
      const across = (a, b) => {
        if (Math.abs(b - a) < 1e-9) return;
        const lo = Math.min(a, b), hi = Math.max(a, b);
        for (let k = Math.floor(lo) + 1; k < hi; k++) ts.push((k - a) / (b - a));
      };
      across(x0 / step, x1 / step);
      across(z0 / step, z1 / step);
      across((x0 + z0) / step, (x1 + z1) / step);
      return ts.filter((t) => t > 1e-6 && t < 1 - 1e-6).sort((a, b) => a - b);
    },
  };
}

// A polyline [x0, z0, x1, z1, ...] as one continuous draped ribbon of the given width:
// at each bend the edges meet along the bisector (a mitre, its length capped at
// MITRE_MAX widths so hairpins stay tidy); a vertex pair wherever the centreline crosses
// the drawn ground's edges, each vertex at the drawn ground's height under it.
const MITRE_MAX = 2;
function ribbon(mesh, line, width, lift, ground) {
  const hw = width / 2;
  // The points (repeated ones dropped) and each segment's unit direction.
  const pts = [];
  for (let k = 0; k + 1 < line.length; k += 2) {
    const x = line[k], z = line[k + 1], last = pts[pts.length - 1];
    if (!last || Math.hypot(x - last[0], z - last[1]) > 0.01) pts.push([x, z]);
  }
  if (pts.length < 2) return;
  const dirs = [];
  for (let i = 0; i + 1 < pts.length; i++) {
    const dx = pts[i + 1][0] - pts[i][0], dz = pts[i + 1][1] - pts[i][1], len = Math.hypot(dx, dz);
    dirs.push([dx / len, dz / len, len]);
  }
  // Side offset at point i: the mitre of its two segments (one at the ends).
  const side = (i) => {
    const a = dirs[Math.max(0, i - 1)], b = dirs[Math.min(dirs.length - 1, i)];
    let nx = -(a[1] + b[1]), nz = a[0] + b[0];
    const nl = Math.hypot(nx, nz);
    if (nl < 1e-6) return [-a[1] * hw, a[0] * hw]; // a full reversal
    nx /= nl;
    nz /= nl;
    const cos = nx * -a[1] + nz * a[0]; // against the first segment's own side direction
    const k = hw / Math.max(cos, 1 / MITRE_MAX);
    return [nx * k, nz * k];
  };
  let prev = -1;
  const put = (x, z, sx, sz) => {
    const a = mesh.vertex(x + sx, ground.height(x + sx, z + sz) + lift, z + sz, 0, 1, 0);
    mesh.vertex(x - sx, ground.height(x - sx, z - sz) + lift, z - sz, 0, 1, 0);
    if (prev >= 0) mesh.idx.push(prev, a, prev + 1, prev + 1, a, a + 1);
    prev = a;
  };
  for (let i = 0; i < dirs.length; i++) {
    const [x0, z0] = pts[i], [ux, uz, len] = dirs[i];
    if (i === 0) put(x0, z0, ...side(0));
    // Drape points inside the segment (plain side offset), then its end (a mitre).
    // (where the centreline or either edge crosses the drawn ground's edges, so each
    // stretch of every edge lies on one terrain triangle).
    const [x1, z1] = pts[i + 1], sx = -uz * hw, sz = ux * hw;
    const ts = [...new Set([0, 1, -1].flatMap((k) => ground.cuts(x0 + k * sx, z0 + k * sz, x1 + k * sx, z1 + k * sz)))].sort((a, b) => a - b);
    for (let k = 0; k < ts.length; k++) if (k === 0 || ts[k] - ts[k - 1] > 1e-4) put(x0 + (x1 - x0) * ts[k], z0 + (z1 - z0) * ts[k], sx, sz);
    put(pts[i + 1][0], pts[i + 1][1], ...side(i + 1));
  }
}

// Ear clipping of a simple polygon [x0, z0, x1, z1, ...] (no holes): triangles as index
// triples into its points. O(n^2), fine for footprints and aprons.
export function triangulate(flat) {
  const n = flat.length / 2;
  if (n < 3) return [];
  const X = (i) => flat[2 * i], Z = (i) => flat[2 * i + 1];
  let area = 0;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    area += X(i) * Z(j) - X(j) * Z(i);
  }
  const idx = [...Array(n).keys()];
  if (area < 0) idx.reverse(); // counter-clockwise in (x, z)
  const cross = (a, b, c) => (X(b) - X(a)) * (Z(c) - Z(a)) - (Z(b) - Z(a)) * (X(c) - X(a));
  const inside = (p, a, b, c) => cross(a, b, p) >= 0 && cross(b, c, p) >= 0 && cross(c, a, p) >= 0;
  const tris = [];
  let guard = 0;
  while (idx.length > 3 && guard++ < 10000) {
    let clipped = false;
    for (let k = 0; k < idx.length; k++) {
      const a = idx[(k + idx.length - 1) % idx.length], b = idx[k], c = idx[(k + 1) % idx.length];
      if (cross(a, b, c) <= 0) continue; // reflex
      if (idx.some((p) => p !== a && p !== b && p !== c && inside(p, a, b, c))) continue;
      tris.push(a, b, c);
      idx.splice(k, 1);
      clipped = true;
      break;
    }
    if (!clipped) break; // degenerate: give up on the rest
  }
  if (idx.length === 3) tris.push(idx[0], idx[1], idx[2]);
  return tris;
}

function polygon(mesh, flat, lift, tiles) {
  const tris = triangulate(flat);
  if (!tris.length) return;
  const base = mesh.pos.length / 3;
  for (let k = 0; k < flat.length; k += 2) mesh.vertex(flat[k], heightAt(tiles, flat[k], flat[k + 1]) + lift, flat[k + 1], 0, 1, 0);
  // Upward faces: (x, z) counter-clockwise seen from above is clockwise in three.js's
  // y-up frame, so swap two corners.
  for (let k = 0; k < tris.length; k += 3) mesh.idx.push(base + tris[k], base + tris[k + 2], base + tris[k + 1]);
}

// Roads, railways (one mesh) and taxiways and aprons (another), or null where none. Roads,
// taxiways and aprons take the colour the imagery shows for them (the features file's
// `colours`, scenery_colours.py: per road class, taxiways, aprons), brightened as the ground's imagery
// is (terrain.js imageryGain), as vertex colours; older builds a plain grey.
export function featureGroundData(f, tiles, segments = null) {
  const ground = drawnGround(tiles, segments);
  const roads = new Mesh(true), rail = new Mesh(), paved = new Mesh(true), taxilines = new Mesh();
  const C = f.colours;
  const paint = (hex, grey) => (hex !== undefined ? imageryColour(hex) : grey);
  for (const [cls, lines] of Object.entries(f.roads ?? {})) {
    roads.paint = C?.roads?.[cls] !== undefined ? imageryColour(C.roads[cls]) : ROAD_GREY;
    for (const line of lines) ribbon(roads, line, ROAD_WIDTH_M[cls] ?? 7, ROAD_LIFT_M, ground);
  }
  for (const line of f.rail ?? []) ribbon(rail, line, RAIL_WIDTH_M, ROAD_LIFT_M, ground);
  paved.paint = paint(C?.taxiway ?? C?.paved, PAVED_GREY);
  for (const line of f.taxiway ?? []) {
    ribbon(paved, line, TAXIWAY_WIDTH_M, PAVED_LIFT_M, ground);
    ribbon(taxilines, line, TAXILINE_WIDTH_M, PAVED_LIFT_M + 0.02, ground); // the yellow centreline
  }
  paved.paint = paint(C?.apron ?? C?.paved, PAVED_GREY);
  for (const ring of f.apron ?? []) polygon(paved, ring, PAVED_LIFT_M - 0.02, tiles);
  return { roads: roads.arrays(), rail: rail.arrays(), paved: paved.arrays(), taxilines: taxilines.arrays() };
}

// Lights at night (nightLights.js): street lamps along the roads, every LAMP_SPACING_M
// and staggered side to side (both sides of wide roads), LAMP_HEIGHT_M up; blue edge
// lights along both sides of the taxiways every TAXI_LIGHT_M. Positions and colours
// (linear, above 1 for the glow), or null where none. Spacings, heights and colours:
// project choices (LED street lighting, ICAO blue taxiway edges).
const LAMP_SPACING_M = 36;
const LAMP_HEIGHT_M = 10;
const TAXI_LIGHT_M = 30;
const LAMP_COLOUR = { motorway: [1.2, 1.12, 0.98], trunk: [1.2, 1.12, 0.98] }; // whiter on the highways
const STREET_COLOUR = [1.25, 0.92, 0.58];
const TAXI_COLOUR = [0.3, 0.45, 1.6];

// Calls fn(x, z, ux, uz, k) every `step` metres along a polyline (from step / 2).
function along(line, step, fn) {
  let next = step / 2, s = 0, k = 0;
  for (let i = 0; i + 3 < line.length; i += 2) {
    const ax = line[i], az = line[i + 1], bx = line[i + 2], bz = line[i + 3];
    const len = Math.hypot(bx - ax, bz - az);
    if (len < 1e-6) continue;
    while (next <= s + len) {
      const t = (next - s) / len;
      fn(ax + (bx - ax) * t, az + (bz - az) * t, (bx - ax) / len, (bz - az) / len, k++);
      next += step;
    }
    s += len;
  }
}

export function lightData(f, tiles) {
  const pos = [], col = [];
  const put = (x, z, h, c) => {
    pos.push(x, heightAt(tiles, x, z) + h, z);
    col.push(c[0], c[1], c[2]);
  };
  for (const [cls, lines] of Object.entries(f.roads ?? {})) {
    const half = (ROAD_WIDTH_M[cls] ?? 7) / 2, c = LAMP_COLOUR[cls] ?? STREET_COLOUR, both = half >= 7;
    for (const line of lines) {
      along(line, LAMP_SPACING_M, (x, z, ux, uz, k) => {
        for (const side of both ? [1, -1] : [k % 2 ? 1 : -1]) put(x - uz * side * (half + 1), z + ux * side * (half + 1), LAMP_HEIGHT_M, c);
      });
    }
  }
  for (const line of f.taxiway ?? []) {
    along(line, TAXI_LIGHT_M, (x, z, ux, uz) => {
      for (const side of [1, -1]) put(x - uz * side * (TAXIWAY_WIDTH_M / 2), z + ux * side * (TAXIWAY_WIDTH_M / 2), 0.4, TAXI_COLOUR);
    });
  }
  return pos.length ? { position: new Float32Array(pos), color: new Float32Array(col) } : null;
}

const srgbToLinear = (c) => (c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4));
const linear = (hex) => [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255].map((v) => srgbToLinear(v / 255));

// An imagery colour (0xRRGGBB, sRGB) as the drawn surfaces' linear albedo, brightened as
// the ground's imagery (terrain.js imageryGain), so both look alike under the same light.
export const IMAGERY_GAIN = 1.4;
export const imageryColour = (hex) => linear(hex).map((v) => Math.min(1, v * IMAGERY_GAIN));
const ROAD_GREY = [0x5a, 0x5c, 0x5e].map((v) => srgbToLinear(v / 255)); // builds without colours
const PAVED_GREY = [0x7d, 0x7f, 0x80].map((v) => srgbToLinear(v / 255));

// Walls and fences (OSM barrier=wall / fence): upright strips along their lines on the
// drawn ground, both faces, walls WALL_M high (or their own height: the tile's `wall_m`,
// from OSM or the region file, e.g. OMAD's 4 m perimeter wall) and solid (render colour),
// fences FENCE_M and see-through (the material's mesh pattern). Heights: project choices.
const WALL_M = 3.0, FENCE_M = 2.4;

export function barrierData(f, tiles, segments = null) {
  const ground = drawnGround(tiles, segments);
  const out = {};
  for (const [kind, hDefault] of [["wall", WALL_M], ["fence", FENCE_M]]) {
    const m = new Mesh(false, true);
    for (const [n, line] of (f[kind] ?? []).entries()) {
      const h = (kind === "wall" && f.wall_m?.[n]) || hDefault;
      let along = 0;
      for (let k = 0; k + 3 < line.length; k += 2) {
        const ax = line[k], az = line[k + 1], bx = line[k + 2], bz = line[k + 3], len = Math.hypot(bx - ax, bz - az);
        if (len < 0.05) continue;
        const nx = -(bz - az) / len, nz = (bx - ax) / len;
        const ts = [0, ...ground.cuts(ax, az, bx, bz), 1];
        for (let i = 0; i + 1 < ts.length; i++) {
          const [t0, t1] = [ts[i], ts[i + 1]];
          const x0 = ax + (bx - ax) * t0, z0 = az + (bz - az) * t0, x1 = ax + (bx - ax) * t1, z1 = az + (bz - az) * t1;
          const g0 = ground.height(x0, z0) - 0.3, g1 = ground.height(x1, z1) - 0.3;
          const s0 = along + len * t0, s1 = along + len * t1;
          const v = m.vertex(x0, g0, z0, nx, 0, nz, null, s0, 0, h);
          m.vertex(x1, g1, z1, nx, 0, nz, null, s1, 0, h);
          m.vertex(x0, g0 + h + 0.3, z0, nx, 0, nz, null, s0, h, h);
          m.vertex(x1, g1 + h + 0.3, z1, nx, 0, nz, null, s1, h, h);
          m.idx.push(v, v + 2, v + 1, v + 1, v + 2, v + 3);
        }
        along += len;
      }
    }
    out[kind] = m.arrays();
  }
  return out;
}

const WALLS = [0xe9e2d2, 0xdcd3bf, 0xf1eee6, 0xcfc4ad, 0xe3dccb].map(linear); // render and stone
const GLASS = [0x7d93a6, 0x8aa1ae, 0x6f8494].map(linear); // towers
const CLADDING = [0xd8dbdc, 0xcfd3d5, 0xe2e3e1].map(linear); // hangars: light grey metal (project choice)
const DOOR = linear(0x9aa0a4); // hangar doors: darker grey panels (project choice)
const ROOF_SHADE = 0.85;
const WALL_FROM_ROOF = 0.45; // how much of the roof's colour the walls take (project choice)
// Airport buildings (scenery_osm.py _airport_kind): their kind rides in the facade
// attribute's third value as kind x 1000 + the building's height, for the shader (terrain.js).
// Offices (region file overrides): a terminal's glazed ground floor; open sheds: a hangar's walls.
export const FACADE_KIND = { hangar: 1, shelter: 2, terminal: 3, tower: 4, door: 5, office: 3, open_shed: 1 };
const SHED_RISE = 0.2; // an open shed's barrel roof: its rise, of the span (at most 0.6 of the height; project choice)
const SHED_STEPS = 12;
const DOOR_WIDTH = 0.85; // of the door wall's length (project choice)
const DOOR_HEIGHT = 0.8; // of the eaves' height, and 1.5 m under them at least (project choice)
const GABLE_RISE = 0.08; // a hangar's ridge above its eaves, of the span across (project choice; four-sided footprints)
const SHELTER_ROOF_M = 0.4; // shelters: the roof's depth and its posts' size and spacing (project choices)
const POST_M = 0.35;
const POST_SPACING_M = 12;

// Buildings with at least `minHeightM` (e.g. only towers on distant tiles), one mesh with
// vertex colours and, for the facade shader (terrain.js), a "facade" attribute in metres:
// along the wall, up from the building's base, the building's height (walls; plus 1000 x its
// FACADE_KIND for airport buildings); along = -1 marks a roof. Hangars: cladding, a door
// panel on the wall the build chose, a gable roof on four-sided footprints; shelters: a roof
// on posts, open sides. Null where none.
export function buildingData(f, tiles, minHeightM = 0) {
  const mesh = new Mesh(true, true);
  let i = 0;
  for (const [height, , ring, colour, info] of f.buildings ?? []) {
    i++;
    if (height < minHeightM || ring.length < 6) continue;
    let cx = 0, cz = 0;
    for (let k = 0; k < ring.length; k += 2) {
      cx += ring[k];
      cz += ring[k + 1];
    }
    cx /= ring.length / 2;
    cz /= ring.length / 2;
    const y0 = heightAt(tiles, cx, cz) - 0.5; // a little into the ground on slopes
    const y1 = y0 + 0.5 + height;
    const hash = Math.abs(Math.round(cx * 7 + cz * 13 + i)) % 15;
    const kind = info?.kind ?? null;
    const code = FACADE_KIND[kind] ?? 0;
    const tag = height + 1000 * code;
    let wall = kind === "hangar" || kind === "open_shed" ? CLADDING[hash % CLADDING.length] : height >= 40 && !code ? GLASS[hash % GLASS.length] : WALLS[hash % WALLS.length];
    let roof = wall.map((v) => v * ROOF_SHADE);
    if (colour != null && (height < 40 || code)) {
      roof = linear(colour).map((v) => Math.min(1, v * IMAGERY_GAIN));
      if (kind !== "hangar" && kind !== "open_shed") wall = wall.map((v, k) => v + (roof[k] - v) * WALL_FROM_ROOF);
    }
    const n = ring.length / 2;
    let area = 0;
    for (let k = 0; k < n; k++) {
      const j = (k + 1) % n;
      area += ring[2 * k] * ring[2 * j + 1] - ring[2 * j] * ring[2 * k + 1];
    }
    const ccw = area > 0; // in (x, z)
    if (kind === "shelter") {
      shelter(mesh, ring, n, ccw, y0, y1, wall, roof, tag);
      continue;
    }
    if (kind === "open_shed" && n === 4 && info.open) {
      openShed(mesh, ring, info.open[0], y0, y1, height, wall, roof, tag);
      continue;
    }
    // A hangar's gable: a four-sided footprint, its ridge along the longer pair of sides.
    const gable = kind === "hangar" && n === 4 ? gableOf(ring) : null;
    const rise = gable ? gable.rise : 0;
    const eaves = y1 - rise;
    let along = 0;
    for (let k = 0; k < n; k++) {
      const j = (k + 1) % n;
      const [ax, az, bx, bz] = ccw ? [ring[2 * k], ring[2 * k + 1], ring[2 * j], ring[2 * j + 1]] : [ring[2 * j], ring[2 * j + 1], ring[2 * k], ring[2 * k + 1]];
      const len = Math.hypot(bx - ax, bz - az);
      if (len < 0.01) continue;
      // Outward normal of an edge of a counter-clockwise (x, z) ring: (dz, -dx) / len.
      const nx = (bz - az) / len, nz = -(bx - ax) / len;
      const v0 = mesh.vertex(ax, y0, az, nx, 0, nz, wall, along, -0.5, tag);
      mesh.vertex(bx, y0, bz, nx, 0, nz, wall, along + len, -0.5, tag);
      mesh.vertex(ax, eaves, az, nx, 0, nz, wall, along, eaves - y0 - 0.5, tag);
      mesh.vertex(bx, eaves, bz, nx, 0, nz, wall, along + len, eaves - y0 - 0.5, tag);
      mesh.idx.push(v0, v0 + 2, v0 + 1, v0 + 1, v0 + 2, v0 + 3);
      if (gable && gable.ends.includes(k)) { // the gable end's triangle up to the ridge
        const t = mesh.vertex((ax + bx) / 2, y1, (az + bz) / 2, nx, 0, nz, wall, along + len / 2, height, tag);
        mesh.idx.push(v0 + 2, t, v0 + 3);
      }
      if (kind === "hangar" && k === info.door) {
        const w = len * DOOR_WIDTH, dh = Math.max(0, Math.min((eaves - y0 - 0.5) * DOOR_HEIGHT, eaves - y0 - 0.5 - 1.5));
        const ux = (bx - ax) / len, uz = (bz - az) / len, s0 = (len - w) / 2, off = 0.06; // in front of the wall
        const px = ax + nx * off + ux * s0, pz = az + nz * off + uz * s0;
        const d0 = mesh.vertex(px, y0 + 0.5, pz, nx, 0, nz, DOOR, 0, 0, dh + 1000 * FACADE_KIND.door);
        mesh.vertex(px + ux * w, y0 + 0.5, pz + uz * w, nx, 0, nz, DOOR, w, 0, dh + 1000 * FACADE_KIND.door);
        mesh.vertex(px, y0 + 0.5 + dh, pz, nx, 0, nz, DOOR, 0, dh, dh + 1000 * FACADE_KIND.door);
        mesh.vertex(px + ux * w, y0 + 0.5 + dh, pz + uz * w, nx, 0, nz, DOOR, w, dh, dh + 1000 * FACADE_KIND.door);
        if (dh > 0) mesh.idx.push(d0, d0 + 2, d0 + 1, d0 + 1, d0 + 2, d0 + 3);
      }
      along += len;
    }
    if (gable) {
      // Two roof planes from the eaves (the long sides) up to the ridge.
      for (const [p, q] of gable.sides) {
        const [ax, az, bx, bz] = [ring[2 * p], ring[2 * p + 1], ring[2 * q], ring[2 * q + 1]];
        const [rax, raz] = gable.ridge[p === gable.sides[0][0] ? 0 : 1], [rbx, rbz] = gable.ridge[q === gable.sides[0][1] ? 1 : 0];
        // the plane's normal: up and away from the ridge
        const ox = (ax + bx) / 2 - (rax + rbx) / 2, oz = (az + bz) / 2 - (raz + rbz) / 2, ol = Math.hypot(ox, oz) || 1;
        const slope = rise / ol, nl = Math.hypot(slope, 1);
        const nx = ox / ol * slope / nl, ny = 1 / nl, nz = oz / ol * slope / nl;
        const b0 = mesh.vertex(ax, eaves, az, nx, ny, nz, roof, -1, 0, tag);
        mesh.vertex(bx, eaves, bz, nx, ny, nz, roof, -1, 0, tag);
        mesh.vertex(rax, y1, raz, nx, ny, nz, roof, -1, 0, tag);
        mesh.vertex(rbx, y1, rbz, nx, ny, nz, roof, -1, 0, tag);
        mesh.idx.push(b0, b0 + 1, b0 + 2, b0 + 1, b0 + 3, b0 + 2, b0, b0 + 2, b0 + 1, b0 + 1, b0 + 2, b0 + 3); // both faces (winding either way)
      }
      continue;
    }
    const tris = triangulate(ring);
    const base = mesh.pos.length / 3;
    for (let k = 0; k < ring.length; k += 2) mesh.vertex(ring[k], y1, ring[k + 1], 0, 1, 0, roof, -1, 0, tag);
    for (let k = 0; k < tris.length; k += 3) mesh.idx.push(base + tris[k], base + tris[k + 2], base + tris[k + 1]);
  }
  return mesh.arrays();
}

// A four-sided hangar footprint's gable: the ridge joins the middles of the two shorter
// sides (the gable ends), GABLE_RISE of the span between the long sides above the eaves
// (at most 3 m). sides: the long sides' corner indices; ridge: its two ends, next to
// sides[0][0] and sides[0][1].
function gableOf(ring) {
  const P = (k) => [ring[2 * (k % 4)], ring[2 * (k % 4) + 1]];
  const len = (k) => Math.hypot(P(k + 1)[0] - P(k)[0], P(k + 1)[1] - P(k)[1]);
  const s = len(0) + len(2) >= len(1) + len(3) ? 0 : 1; // the long sides: k = s and s + 2
  const ends = [(s + 1) % 4, (s + 3) % 4];
  const mid = (k) => [(P(k)[0] + P(k + 1)[0]) / 2, (P(k)[1] + P(k + 1)[1]) / 2];
  const span = (len(ends[0]) + len(ends[1])) / 2;
  const rise = Math.min(3, GABLE_RISE * span);
  // side s runs from corner s to s + 1; the ridge end next to corner s is the middle of the end wall (s + 3) % 4
  return { rise, ends, sides: [[s % 4, (s + 1) % 4], [(s + 2) % 4, (s + 3) % 4]], ridge: [mid((s + 3) % 4), mid((s + 1) % 4)] };
}

// An open shed (a wash bay, sheltered parking): a barrel roof across, from the top of its two
// side walls to its height, open at both ends (walls `front` and front + 2 of a four-sided
// footprint); the roof seen from below too.
function openShed(mesh, ring, front, y0, y1, height, wall, roof, tag) {
  const P = (k) => [ring[2 * (k % 4)], ring[2 * (k % 4) + 1]];
  const [a, b, c, d] = [P(front), P(front + 1), P(front + 2), P(front + 3)]; // front a-b, back d-c
  const span = (Math.hypot(b[0] - a[0], b[1] - a[1]) + Math.hypot(c[0] - d[0], c[1] - d[1])) / 2;
  const rise = Math.min(SHED_RISE * span, 0.6 * height), eaves = y1 - rise;
  const lerp = (p, q, t) => [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
  // side walls: d-a (beside a) and b-c (beside b), up to the eaves, facing out
  for (const [p, q] of [[d, a], [b, c]]) {
    const len = Math.hypot(q[0] - p[0], q[1] - p[1]);
    // outward: away from the other side wall's middle
    const o = p === d ? lerp(b, c, 0.5) : lerp(d, a, 0.5);
    let nx = (q[1] - p[1]) / len, nz = -(q[0] - p[0]) / len;
    const m = lerp(p, q, 0.5);
    if ((m[0] - o[0]) * nx + (m[1] - o[1]) * nz < 0) [nx, nz] = [-nx, -nz];
    const [s, e] = nx * (q[1] - p[1]) - nz * (q[0] - p[0]) > 0 ? [q, p] : [p, q]; // wound like the other walls
    const v = mesh.vertex(s[0], y0, s[1], nx, 0, nz, wall, 0, -0.5, tag);
    mesh.vertex(e[0], y0, e[1], nx, 0, nz, wall, len, -0.5, tag);
    mesh.vertex(s[0], eaves, s[1], nx, 0, nz, wall, 0, eaves - y0 - 0.5, tag);
    mesh.vertex(e[0], eaves, e[1], nx, 0, nz, wall, len, eaves - y0 - 0.5, tag);
    mesh.idx.push(v, v + 2, v + 1, v + 1, v + 2, v + 3, v, v + 1, v + 2, v + 1, v + 3, v + 2); // both faces: open ends show the inside
  }
  // the barrel: strips from the front edge to the back, across from side a to side b
  const across = [b[0] - a[0], b[1] - a[1]], al = Math.hypot(...across);
  const ux = across[0] / al, uz = across[1] / al;
  const under = roof.map((v) => v * 0.55);
  const top = [], bottom = [];
  for (let i = 0; i <= SHED_STEPS; i++) {
    const t = i / SHED_STEPS, y = eaves + rise * Math.sin(Math.PI * t);
    const slope = (rise * Math.PI * Math.cos(Math.PI * t)) / span; // dy / d(across)
    const nl = Math.hypot(slope, 1), nx = (-slope * ux) / nl, ny = 1 / nl, nz = (-slope * uz) / nl;
    const f = lerp(a, b, t), k = lerp(d, c, t);
    top.push(mesh.vertex(f[0], y, f[1], nx, ny, nz, roof, -1, 0, tag), mesh.vertex(k[0], y, k[1], nx, ny, nz, roof, -1, 0, tag));
    bottom.push(mesh.vertex(f[0], y - 0.15, f[1], -nx, -ny, -nz, under, -1, 0, tag), mesh.vertex(k[0], y - 0.15, k[1], -nx, -ny, -nz, under, -1, 0, tag));
  }
  for (let i = 0; i < SHED_STEPS; i++) {
    const [f0, k0, f1, k1] = [top[2 * i], top[2 * i + 1], top[2 * i + 2], top[2 * i + 3]];
    mesh.idx.push(f0, f1, k0, k0, f1, k1, f0, k0, f1, k0, k1, f1); // both windings (the side the ring was drawn from varies)
    const [g0, h0, g1, h1] = [bottom[2 * i], bottom[2 * i + 1], bottom[2 * i + 2], bottom[2 * i + 3]];
    mesh.idx.push(g0, h0, g1, h0, h1, g1, g0, g1, h0, h0, g1, h1);
  }
}

// An open shelter: its roof (SHELTER_ROOF_M deep, top at y1) on square posts at its
// corners and every POST_SPACING_M along its sides; no walls.
function shelter(mesh, ring, n, ccw, y0, y1, wall, roof, tag) {
  const yb = y1 - SHELTER_ROOF_M;
  const box = (x0, z0, x1, z1, ya, yc) => { // posts: four faces
    for (const [ax, az, bx, bz, nx, nz] of [[x0, z1, x1, z1, 0, 1], [x1, z1, x1, z0, 1, 0], [x1, z0, x0, z0, 0, -1], [x0, z0, x0, z1, -1, 0]]) {
      const v = mesh.vertex(ax, ya, az, nx, 0, nz, wall, 0, ya - y0 - 0.5, tag);
      mesh.vertex(bx, ya, bz, nx, 0, nz, wall, POST_M, ya - y0 - 0.5, tag);
      mesh.vertex(ax, yc, az, nx, 0, nz, wall, 0, yc - y0 - 0.5, tag);
      mesh.vertex(bx, yc, bz, nx, 0, nz, wall, POST_M, yc - y0 - 0.5, tag);
      mesh.idx.push(v, v + 2, v + 1, v + 1, v + 2, v + 3);
    }
  };
  let along = 0;
  for (let k = 0; k < n; k++) {
    const j = (k + 1) % n;
    const [ax, az, bx, bz] = ccw ? [ring[2 * k], ring[2 * k + 1], ring[2 * j], ring[2 * j + 1]] : [ring[2 * j], ring[2 * j + 1], ring[2 * k], ring[2 * k + 1]];
    const len = Math.hypot(bx - ax, bz - az);
    if (len < 0.01) continue;
    const ux = (bx - ax) / len, uz = (bz - az) / len, nx = uz, nz = -ux;
    // posts just inside the edge, at its start and every POST_SPACING_M
    const posts = Math.max(1, Math.round(len / POST_SPACING_M));
    for (let p = 0; p < posts; p++) {
      const t = (p * len) / posts, px = ax + ux * t - nx * POST_M, pz = az + uz * t - nz * POST_M;
      box(px - POST_M / 2, pz - POST_M / 2, px + POST_M / 2, pz + POST_M / 2, y0, yb);
    }
    // the roof's edge band
    const v0 = mesh.vertex(ax, yb, az, nx, 0, nz, wall, along, yb - y0 - 0.5, tag);
    mesh.vertex(bx, yb, bz, nx, 0, nz, wall, along + len, yb - y0 - 0.5, tag);
    mesh.vertex(ax, y1, az, nx, 0, nz, wall, along, y1 - y0 - 0.5, tag);
    mesh.vertex(bx, y1, bz, nx, 0, nz, wall, along + len, y1 - y0 - 0.5, tag);
    mesh.idx.push(v0, v0 + 2, v0 + 1, v0 + 1, v0 + 2, v0 + 3);
    along += len;
  }
  const tris = triangulate(ring);
  const top = mesh.pos.length / 3;
  for (let k = 0; k < ring.length; k += 2) mesh.vertex(ring[k], y1, ring[k + 1], 0, 1, 0, roof, -1, 0, tag);
  for (let k = 0; k < tris.length; k += 3) mesh.idx.push(top + tris[k], top + tris[k + 2], top + tris[k + 1]);
  const under = mesh.pos.length / 3; // the underside, seen from below
  const shade = roof.map((v) => v * 0.55);
  for (let k = 0; k < ring.length; k += 2) mesh.vertex(ring[k], yb, ring[k + 1], 0, -1, 0, shade, -1, 0, tag);
  for (let k = 0; k < tris.length; k += 3) mesh.idx.push(under + tris[k], under + tris[k + 1], under + tris[k + 2]);
}
