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
// drawn ground, both faces, walls WALL_M high and solid (render colour), fences FENCE_M
// and see-through (the material's mesh pattern). Heights: project choices.
const WALL_M = 3.0, FENCE_M = 2.4;

export function barrierData(f, tiles, segments = null) {
  const ground = drawnGround(tiles, segments);
  const out = {};
  for (const [kind, h] of [["wall", WALL_M], ["fence", FENCE_M]]) {
    const m = new Mesh(false, true);
    for (const line of f[kind] ?? []) {
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
const ROOF_SHADE = 0.85;
const WALL_FROM_ROOF = 0.45; // how much of the roof's colour the walls take (project choice)

// Buildings with at least `minHeightM` (e.g. only towers on distant tiles), one mesh with
// vertex colours and, for the facade shader (terrain.js), a "facade" attribute in metres:
// along the wall, up from the building's base, the building's height (walls); along = -1
// marks a roof. Null where none.
export function buildingData(f, tiles, minHeightM = 0) {
  const mesh = new Mesh(true, true);
  let i = 0;
  for (const [height, , ring, colour] of f.buildings ?? []) {
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
    let wall = height >= 40 ? GLASS[hash % GLASS.length] : WALLS[hash % WALLS.length];
    let roof = wall.map((v) => v * ROOF_SHADE);
    if (colour !== undefined && height < 40) {
      roof = linear(colour).map((v) => Math.min(1, v * IMAGERY_GAIN));
      wall = wall.map((v, k) => v + (roof[k] - v) * WALL_FROM_ROOF);
    }
    const n = ring.length / 2;
    let area = 0;
    for (let k = 0; k < n; k++) {
      const j = (k + 1) % n;
      area += ring[2 * k] * ring[2 * j + 1] - ring[2 * j] * ring[2 * k + 1];
    }
    const ccw = area > 0; // in (x, z)
    let along = 0;
    for (let k = 0; k < n; k++) {
      const j = (k + 1) % n;
      const [ax, az, bx, bz] = ccw ? [ring[2 * k], ring[2 * k + 1], ring[2 * j], ring[2 * j + 1]] : [ring[2 * j], ring[2 * j + 1], ring[2 * k], ring[2 * k + 1]];
      const len = Math.hypot(bx - ax, bz - az);
      if (len < 0.01) continue;
      // Outward normal of an edge of a counter-clockwise (x, z) ring: (dz, -dx) / len.
      const nx = (bz - az) / len, nz = -(bx - ax) / len;
      const v0 = mesh.vertex(ax, y0, az, nx, 0, nz, wall, along, -0.5, height);
      mesh.vertex(bx, y0, bz, nx, 0, nz, wall, along + len, -0.5, height);
      mesh.vertex(ax, y1, az, nx, 0, nz, wall, along, height, height);
      mesh.vertex(bx, y1, bz, nx, 0, nz, wall, along + len, height, height);
      along += len;
      mesh.idx.push(v0, v0 + 2, v0 + 1, v0 + 1, v0 + 2, v0 + 3);
    }
    const tris = triangulate(ring);
    const base = mesh.pos.length / 3;
    for (let k = 0; k < ring.length; k += 2) mesh.vertex(ring[k], y1, ring[k + 1], 0, 1, 0, roof, -1, 0, height);
    for (let k = 0; k < tris.length; k += 3) mesh.idx.push(base + tris[k], base + tris[k + 2], base + tris[k + 1]);
  }
  return mesh.arrays();
}
