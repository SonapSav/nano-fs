// Meshes of a real-world region's OpenStreetMap features (a tile's features file,
// flightsim/world/scenery_osm.py) as typed arrays, without three.js: runs in the tile
// worker. World frame: x east, y up, z south; heights from the region's ground (demCore.js
// heightAt), so roads lie on the terrain the physics flies over.
//
// Roads and railways: ribbons draped every DRAPE_M, lifted ROAD_LIFT_M (the material also
// pulls them forward in depth). Taxiways: ribbons; aprons: polygons (ear clipping).
// Buildings: footprints extruded to their height, flat roofs, one colour each (vertex
// colours: light stone and render for most, blue-grey glass for towers; project choices).

import { heightAt } from "./demCore.js";

const DRAPE_M = 25;
const ROAD_LIFT_M = 0.35;
const PAVED_LIFT_M = 0.25;
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
    if (this.col) this.col.push(c[0], c[1], c[2]);
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

// A polyline [x0, z0, x1, z1, ...] as a draped ribbon of the given width.
function ribbon(mesh, line, width, lift, tiles) {
  const hw = width / 2;
  for (let k = 0; k + 3 < line.length; k += 2) {
    const x0 = line[k], z0 = line[k + 1], x1 = line[k + 2], z1 = line[k + 3];
    const len = Math.hypot(x1 - x0, z1 - z0);
    if (len < 0.01) continue;
    const ux = (x1 - x0) / len, uz = (z1 - z0) / len, sx = -uz * hw, sz = ux * hw;
    const n = Math.max(1, Math.ceil(len / DRAPE_M));
    let prev = -1;
    for (let s = 0; s <= n; s++) {
      const x = x0 + (x1 - x0) * (s / n), z = z0 + (z1 - z0) * (s / n);
      const y = heightAt(tiles, x, z) + lift;
      const a = mesh.vertex(x + sx, y, z + sz, 0, 1, 0);
      mesh.vertex(x - sx, y, z - sz, 0, 1, 0);
      if (prev >= 0) mesh.idx.push(prev, a, prev + 1, prev + 1, a, a + 1);
      prev = a;
    }
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

// Roads, railways (one mesh) and taxiways and aprons (another), or null where none.
export function featureGroundData(f, tiles) {
  const roads = new Mesh(), rail = new Mesh(), paved = new Mesh(), taxilines = new Mesh();
  for (const [cls, lines] of Object.entries(f.roads ?? {})) for (const line of lines) ribbon(roads, line, ROAD_WIDTH_M[cls] ?? 7, ROAD_LIFT_M, tiles);
  for (const line of f.rail ?? []) ribbon(rail, line, RAIL_WIDTH_M, ROAD_LIFT_M, tiles);
  for (const line of f.taxiway ?? []) {
    ribbon(paved, line, TAXIWAY_WIDTH_M, PAVED_LIFT_M, tiles);
    ribbon(taxilines, line, TAXILINE_WIDTH_M, PAVED_LIFT_M + 0.04, tiles); // the yellow centreline
  }
  for (const ring of f.apron ?? []) polygon(paved, ring, PAVED_LIFT_M - 0.05, tiles);
  return { roads: roads.arrays(), rail: rail.arrays(), paved: paved.arrays(), taxilines: taxilines.arrays() };
}

const srgbToLinear = (c) => (c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4));
const linear = (hex) => [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255].map((v) => srgbToLinear(v / 255));
const WALLS = [0xe9e2d2, 0xdcd3bf, 0xf1eee6, 0xcfc4ad, 0xe3dccb].map(linear); // render and stone
const GLASS = [0x7d93a6, 0x8aa1ae, 0x6f8494].map(linear); // towers
const ROOF_SHADE = 0.85;

// Buildings with at least `minHeightM` (e.g. only towers on distant tiles), one mesh with
// vertex colours and, for the facade shader (terrain.js), a "facade" attribute in metres:
// along the wall, up from the building's base, the building's height (walls); along = -1
// marks a roof. Null where none.
export function buildingData(f, tiles, minHeightM = 0) {
  const mesh = new Mesh(true, true);
  let i = 0;
  for (const [height, , ring] of f.buildings ?? []) {
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
    const wall = height >= 40 ? GLASS[hash % GLASS.length] : WALLS[hash % WALLS.length];
    const roof = wall.map((v) => v * ROOF_SHADE);
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
