// Real-world terrain (a built scenery region, flightsim/world/scenery.py): height and water
// at a map position, without three.js. A bit-identical port of flightsim/world/dem.py: the
// same float32 posts, the same double-precision operations in the same order
// (tests/test_world_dem.py compares them). World frame as terrainCore.js: x = east,
// z = south.
//
// Tiles come from the caller: `tiles.heights(ix, iz)` a Float32Array of
// (HEIGHT_CELLS + 1)^2 posts (rows south, columns east), `tiles.landcover(ix, iz)` a
// Uint8Array of LANDCOVER_CELLS^2 WorldCover classes; null for a tile outside the region
// (or not loaded yet).

export const TILE_SIZE_M = 4000;
export const HEIGHT_CELLS = 128;
export const POST_M = TILE_SIZE_M / HEIGHT_CELLS;
export const LANDCOVER_CELLS = 256;
export const SEA_LEVEL_M = 0;
export const WATER_CLASS = 80;
const LC_CELL_M = TILE_SIZE_M / LANDCOVER_CELLS;

export const heightsName = (ix, iz) => `tiles/h_${ix}_${iz}.f32`;
export const landcoverName = (ix, iz) => `tiles/lc_${ix}_${iz}.u8`;

// Ground height (m MSL): bilinear within the post cell, never below sea level.
export function heightAt(tiles, x, z) {
  const ix = Math.floor(x / TILE_SIZE_M), iz = Math.floor(z / TILE_SIZE_M);
  const posts = tiles.heights(ix, iz);
  if (!posts) return SEA_LEVEL_M;
  const u = (x - ix * TILE_SIZE_M) / POST_M;
  const v = (z - iz * TILE_SIZE_M) / POST_M;
  const i = Math.min(Math.floor(u), HEIGHT_CELLS - 1);
  const j = Math.min(Math.floor(v), HEIGHT_CELLS - 1);
  const fx = u - i, fz = v - j;
  const n = HEIGHT_CELLS + 1;
  const h00 = posts[j * n + i], h01 = posts[j * n + i + 1];
  const h10 = posts[(j + 1) * n + i], h11 = posts[(j + 1) * n + i + 1];
  const a = h00 + (h01 - h00) * fx;
  const b = h10 + (h11 - h10) * fx;
  const h = a + (b - a) * fz;
  return h > SEA_LEVEL_M ? h : SEA_LEVEL_M;
}

// WorldCover class at a position (0 outside the region).
export function landcoverAt(tiles, x, z) {
  const ix = Math.floor(x / TILE_SIZE_M), iz = Math.floor(z / TILE_SIZE_M);
  const cells = tiles.landcover(ix, iz);
  if (!cells) return 0;
  const i = Math.min(Math.floor((x - ix * TILE_SIZE_M) / LC_CELL_M), LANDCOVER_CELLS - 1);
  const j = Math.min(Math.floor((z - iz * TILE_SIZE_M) / LC_CELL_M), LANDCOVER_CELLS - 1);
  return cells[j * LANDCOVER_CELLS + i];
}

export const waterAt = (tiles, x, z) => landcoverAt(tiles, x, z) === WATER_CLASS;
