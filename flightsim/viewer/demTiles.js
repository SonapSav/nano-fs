// Terrain tile data of a real-world region (demCore.js), the same shape as terrainCore.js's
// tileGeometryData / tileObjectsData so terrain.js draws both alike. No three.js: runs in
// the tile worker (terrainWorker.js) as well as on the page.
//
// Heights are the physics' ground (heightAt: bilinear between the posts, never below sea
// level, so the sea is the ground at 0 m); at segments = HEIGHT_CELLS the vertices are the
// posts themselves. The colour is a land cover texture per tile (one texel per WorldCover
// cell, 15.6 m, or every 4th on distant tiles), the sea and lakes painted in: crisp
// coastlines, smoothed by the texture filter.

import { HEIGHT_CELLS, LANDCOVER_CELLS, POST_M, TILE_SIZE_M, heightAt, landcoverAt } from "./demCore.js";

export const SEA_SURFACE_M = -0.3; // (kept for terrain.js: a region draws its water in the texture)

// WorldCover class -> land colour (sRGB hex; project choices, by eye from aerial views of
// the region).
const CLASS_HEX = {
  10: 0x4b6a3c, 20: 0xa89c6e, 30: 0x9c9f63, 40: 0x6f8a4a, 50: 0xb3ab9c, 60: 0xd8c29b, 70: 0xf2f4f5,
  80: 0x3b7d93, 90: 0x8b9a6f, 95: 0x3e5b37, 100: 0xa09f84,
};  // fmt: skip
const DUNE_HEX = 0xcda677; // higher sand: warmer

// `textureSize`: texels per side of the land cover texture (LANDCOVER_CELLS or a divisor).
export function demTileGeometryData(tx, tz, segments, tiles, textureSize = LANDCOVER_CELLS) {
  if (!tiles.heights(tx, tz)) return null;
  const n = segments + 1;
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M, step = TILE_SIZE_M / segments;
  const pos = [], col = [], fld = [], nrm = [], uv = [], idx = [];
  const d = POST_M;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const x = x0 + j * step, z = z0 + i * step;
      const h = heightAt(tiles, x, z);
      const dhdx = (heightAt(tiles, x + d, z) - heightAt(tiles, x - d, z)) / (2 * d);
      const dhdz = (heightAt(tiles, x, z + d) - heightAt(tiles, x, z - d)) / (2 * d);
      const inv = 1 / Math.sqrt(dhdx * dhdx + 1 + dhdz * dhdz);
      nrm.push(-dhdx * inv, inv, -dhdz * inv);
      pos.push(x, h, z);
      col.push(1, 1, 1); // the texture gives the colour
      fld.push(0);
      uv.push(j / segments, i / segments); // texture rows go south, as the land cover cells
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
    uv.push(uv[2 * v], uv[2 * v + 1]);
  }
  for (let k = 0; k < edge.length; k++) {
    const a = edge[k], b = edge[(k + 1) % edge.length], sa = base + k, sb = base + ((k + 1) % edge.length);
    idx.push(a, b, sa, b, sb, sa, a, sa, b, b, sa, sb);
  }
  return {
    position: new Float32Array(pos), color: new Float32Array(col), fieldness: new Float32Array(fld),
    normal: new Float32Array(nrm), uv: new Float32Array(uv), index: new Uint32Array(idx), hasWater: false,
    texture: landcoverTexture(tx, tz, textureSize, tiles), textureSize, shore: shoreTexture(tx, tz, textureSize, tiles),
  };
}

// The shore distance per texel (scenery.py shore files: 0 land, else 1 + metres to land / 8),
// sampled like the land cover; null when the region has none (an older build).
function shoreTexture(tx, tz, size, tiles) {
  const src = tiles.shore?.(tx, tz);
  if (!src) return null;
  const out = new Uint8Array(size * size), step = LANDCOVER_CELLS / size;
  for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) out[r * size + c] = src[Math.floor((r + 0.5) * step) * LANDCOVER_CELLS + Math.floor((c + 0.5) * step)];
  return out;
}

// RGBA (sRGB) texels: the land cover class colour at each texel's cell, sand warmer on
// high dunes.
function landcoverTexture(tx, tz, size, tiles) {
  const out = new Uint8Array(size * size * 4);
  const cell = TILE_SIZE_M / size, x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M;
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      const x = x0 + (c + 0.5) * cell, z = z0 + (r + 0.5) * cell;
      const cls = landcoverAt(tiles, x, z);
      let hex = CLASS_HEX[cls] ?? CLASS_HEX[60];
      let k = 0;
      if (cls === 60) k = Math.min(1, Math.max(0, (heightAt(tiles, x, z) - 20) / 80)); // dunes inland
      const o = 4 * (r * size + c);
      for (let ch = 0; ch < 3; ch++) {
        const a = (hex >> (16 - 8 * ch)) & 255, b = (DUNE_HEX >> (16 - 8 * ch)) & 255;
        out[o + ch] = Math.round(a + (b - a) * k);
      }
      out[o + 3] = 255;
    }
  }
  return out;
}

function seededRandom(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
}

// Trees, seeded per tile: palms where WorldCover has tree cover (in this region, mostly
// date palms in gardens, parks and along roads), low bushes in mangroves and a few in
// shrubland. [x, h, z, size] each.
const TREE_CHANCE = { 10: [1, "palms"], 95: [1, "bushes"], 20: [0.03, "bushes"] };
export function demTileObjectsData(tx, tz, maxTreesNear, far, tiles) {
  const rnd = seededRandom(((tx * 73856093) ^ (tz * 19349663) ^ 0x5eed) >>> 0);
  const x0 = tx * TILE_SIZE_M, z0 = tz * TILE_SIZE_M;
  const out = { palms: [], bushes: [] };
  const maxTrees = far ? Math.round(maxTreesNear / 3) : maxTreesNear;
  let count = 0;
  for (let k = 0; k < 4000 && count < maxTrees; k++) {
    const x = x0 + rnd() * TILE_SIZE_M, z = z0 + rnd() * TILE_SIZE_M;
    const [chance, kind] = TREE_CHANCE[landcoverAt(tiles, x, z)] ?? [0];
    if (chance > 0 && rnd() < chance) {
      out[kind].push(x, heightAt(tiles, x, z), z, 0.7 + rnd() * 0.6);
      count++;
    }
  }
  const empty = new Float64Array(0);
  return { trees: empty, houses: empty, landmarks: empty, palms: new Float64Array(out.palms), bushes: new Float64Array(out.bushes) };
}

// Tiles a build needs: the tile itself and its 8 neighbours (heights for the edge normals).
export function neededTiles(tx, tz) {
  const out = [];
  for (let dz = -1; dz <= 1; dz++) for (let dx = -1; dx <= 1; dx++) out.push([tx + dx, tz + dz]);
  return out;
}

export { HEIGHT_CELLS };
