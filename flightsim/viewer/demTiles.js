// Terrain tile data of a real-world region (demCore.js), the same shape as terrainCore.js's
// tileGeometryData / tileObjectsData so terrain.js draws both alike. No three.js: runs in
// the tile worker (terrainWorker.js) as well as on the page.
//
// Heights are the physics' ground (heightAt: bilinear between the posts, never below sea
// level); at segments = HEIGHT_CELLS the vertices are the posts themselves. Water cells
// (WorldCover 80) sink to WATER_FLOOR_M so the sea surface (SEA_SURFACE_M) shows there and
// nowhere else. Colours come from the land cover class under each vertex.

import { HEIGHT_CELLS, POST_M, TILE_SIZE_M, WATER_CLASS, heightAt, landcoverAt } from "./demCore.js";

export const SEA_SURFACE_M = -0.3; // drawn just under the 0 m shore, so it never covers land
const WATER_FLOOR_M = -3;

const srgbToLinear = (c) => (c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4));
const linear = (hex) => [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255].map((v) => srgbToLinear(v / 255));

// WorldCover class -> land colour (sRGB hex; project choices, by eye from aerial views of
// the region) and how much of the field patchwork shows (cropland only).
const CLASS_COLOUR = {
  10: linear(0x4b6a3c), // tree cover
  20: linear(0xa89c6e), // shrubland
  30: linear(0x9c9f63), // grassland
  40: linear(0x7e8c53), // cropland (fields drawn on top)
  50: linear(0xb3ab9c), // built-up
  60: linear(0xd8c29b), // bare / sparse: desert sand
  70: linear(0xf2f4f5), // snow and ice
  80: linear(0x8a8571), // water (the bottom, under the sea surface)
  90: linear(0x8b9a6f), // herbaceous wetland
  95: linear(0x3e5b37), // mangroves
  100: linear(0xa09f84), // moss and lichen
};
const DUNE = linear(0xcda677); // higher sand: warmer
const FALLBACK = CLASS_COLOUR[60];

// Mesh data for tile (tx, tz), or null when the region has no heights there. `tiles` as in
// demCore.js; the tile and its neighbours (for the normals at the edges) must be loaded.
export function demTileGeometryData(tx, tz, segments, tiles) {
  if (!tiles.heights(tx, tz)) return null;
  const n = segments + 1;
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M, step = TILE_SIZE_M / segments;
  const pos = [], col = [], fld = [], nrm = [], idx = [];
  let hasWater = false;
  const d = POST_M;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const x = x0 + j * step, z = z0 + i * step;
      const cls = landcoverAt(tiles, Math.min(x, x0 + TILE_SIZE_M - 0.01), Math.min(z, z0 + TILE_SIZE_M - 0.01));
      let h = heightAt(tiles, x, z);
      if (cls === WATER_CLASS) {
        h = Math.min(h, WATER_FLOOR_M);
        hasWater = true;
      }
      const dhdx = (heightAt(tiles, x + d, z) - heightAt(tiles, x - d, z)) / (2 * d);
      const dhdz = (heightAt(tiles, x, z + d) - heightAt(tiles, x, z - d)) / (2 * d);
      const inv = 1 / Math.sqrt(dhdx * dhdx + 1 + dhdz * dhdz);
      nrm.push(-dhdx * inv, inv, -dhdz * inv);
      pos.push(x, h, z);
      let c = CLASS_COLOUR[cls] ?? FALLBACK;
      if (cls === 60) {
        const k = Math.min(1, Math.max(0, (h - 20) / 80)); // dunes inland
        c = [c[0] + (DUNE[0] - c[0]) * k, c[1] + (DUNE[1] - c[1]) * k, c[2] + (DUNE[2] - c[2]) * k];
      }
      col.push(c[0], c[1], c[2]);
      fld.push(cls === 40 ? 1 : 0);
    }
  }
  const grid = (i, j) => i * n + j;
  for (let i = 0; i < segments; i++) {
    for (let j = 0; j < segments; j++) {
      const a = grid(i, j), b = grid(i, j + 1), c = grid(i + 1, j), e = grid(i + 1, j + 1);
      idx.push(a, c, b, b, c, e);
    }
  }
  // Skirt, as the procedural tiles: hides cracks between tiles of different detail.
  const edge = [];
  for (let j = 0; j < n; j++) edge.push(grid(0, j));
  for (let i = 1; i < n; i++) edge.push(grid(i, n - 1));
  for (let j = n - 2; j >= 0; j--) edge.push(grid(n - 1, j));
  for (let i = n - 2; i > 0; i--) edge.push(grid(i, 0));
  const base = pos.length / 3;
  for (const v of edge) {
    pos.push(pos[3 * v], pos[3 * v + 1] - 40, pos[3 * v + 2]);
    col.push(col[3 * v], col[3 * v + 1], col[3 * v + 2]);
    fld.push(fld[v]);
    nrm.push(nrm[3 * v], nrm[3 * v + 1], nrm[3 * v + 2]);
  }
  for (let k = 0; k < edge.length; k++) {
    const a = edge[k], b = edge[(k + 1) % edge.length], sa = base + k, sb = base + ((k + 1) % edge.length);
    idx.push(a, b, sa, b, sb, sa, a, sa, b, b, sa, sb);
  }
  return {
    position: new Float32Array(pos), color: new Float32Array(col), fieldness: new Float32Array(fld),
    normal: new Float32Array(nrm), index: new Uint32Array(idx), hasWater,
  };
}

function seededRandom(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
}

// Trees where WorldCover has tree cover or mangroves (fewer in shrubland), seeded per tile.
const TREE_CHANCE = { 10: 1, 95: 1, 20: 0.15 };
export function demTileObjectsData(tx, tz, maxTreesNear, far, tiles) {
  const rnd = seededRandom(((tx * 73856093) ^ (tz * 19349663) ^ 0x5eed) >>> 0);
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M;
  const trees = [];
  const maxTrees = far ? Math.round(maxTreesNear / 3) : maxTreesNear;
  let count = 0;
  for (let k = 0; k < 4000 && count < maxTrees; k++) {
    const x = x0 + rnd() * TILE_SIZE_M, z = z0 + rnd() * TILE_SIZE_M;
    const chance = TREE_CHANCE[landcoverAt(tiles, x, z)] ?? 0;
    if (chance > 0 && rnd() < chance) {
      trees.push(x, heightAt(tiles, x, z), z, 0.5 + rnd() * 0.5); // palms, ghaf and mangroves: smaller than European forest
      count++;
    }
  }
  return { trees: new Float64Array(trees), houses: new Float64Array(0), landmarks: new Float64Array(0) };
}

// Tiles a build needs: the tile itself and its 8 neighbours (heights for the edge normals).
export function neededTiles(tx, tz) {
  const out = [];
  for (let dz = -1; dz <= 1; dz++) for (let dx = -1; dx <= 1; dx++) out.push([tx + dx, tz + dz]);
  return out;
}

export { HEIGHT_CELLS };
