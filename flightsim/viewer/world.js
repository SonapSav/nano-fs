// The world a flight is in: the procedural terrain (terrainCore.js), or a real-world region
// built from open data (flightsim/world/scenery.py), whose files the server serves under
// /scenery/<name>/ (manifest.json, airfields.json, tiles/). The stream's hello says which
// (`world.scenery`: {name, hash}; none: procedural). Every part of the viewer asks this
// module for the ground, so they agree with each other and with the physics.
//
// A region's tiles load on demand: groundAt() answers at once from the tiles already here
// (sea level where a tile has not arrived yet) and asks for the missing one.

import { WATER_LEVEL_M, height as proceduralHeight } from "./terrainCore.js";
import { TILE_SIZE_M, heightAt, heightsName, landcoverAt, landcoverName } from "./demCore.js";

const listeners = new Set();
const state = {
  scenery: null, // {name, hash} or null (procedural)
  manifest: null,
  airfields: null, // the region's airfields.json: {aerodromes, runways}
  heights: new Map(), // "ix,iz" -> Float32Array | null (outside the region) | "loading"
  landcover: new Map(),
};

const sameScenery = (a, b) => (a?.name ?? null) === (b?.name ?? null) && (a?.hash ?? null) === (b?.hash ?? null);

export const sceneryUrl = (scenery, file) => `scenery/${encodeURIComponent(scenery.name)}/${file}?h=${scenery.hash.slice(0, 16)}`;

function inRegion(ix, iz) {
  const t = state.manifest?.tiles;
  return Boolean(t) && ix >= t.ix_min && ix <= t.ix_max && iz >= t.iz_min && iz <= t.iz_max;
}

function load(store, name, Type, ix, iz) {
  const key = `${ix},${iz}`;
  if (store.has(key)) return;
  if (!inRegion(ix, iz)) {
    store.set(key, null);
    return;
  }
  store.set(key, "loading");
  const scenery = state.scenery;
  fetch(sceneryUrl(scenery, name(ix, iz)))
    .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`${r.status}`))))
    .then((buf) => {
      if (sameScenery(scenery, state.scenery)) store.set(key, new Type(buf));
    })
    .catch(() => store.delete(key)); // asked again on the next query
}

const tiles = {
  heights(ix, iz) {
    load(state.heights, heightsName, Float32Array, ix, iz);
    const t = state.heights.get(`${ix},${iz}`);
    return t instanceof Float32Array ? t : null;
  },
  landcover(ix, iz) {
    load(state.landcover, landcoverName, Uint8Array, ix, iz);
    const t = state.landcover.get(`${ix},${iz}`);
    return t instanceof Uint8Array ? t : null;
  },
};

export const world = {
  get scenery() {
    return state.scenery;
  },
  get airfields() {
    return state.airfields;
  },
  get manifest() {
    return state.manifest;
  },
  get real() {
    return state.scenery !== null;
  },

  // A flight's world (the stream's `world`; null: procedural). Resolves once the region's
  // manifest and airfields are here; listeners hear of every change.
  async set(streamWorld) {
    const scenery = streamWorld?.scenery ?? null;
    if (sameScenery(scenery, state.scenery)) return;
    state.scenery = scenery;
    state.manifest = state.airfields = null;
    state.heights.clear();
    state.landcover.clear();
    if (scenery) {
      try {
        const [manifest, airfields] = await Promise.all(
          ["manifest.json", "airfields.json"].map((f) => fetch(sceneryUrl(scenery, f)).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${f}: ${r.status}`))))),
        );
        if (!sameScenery(scenery, state.scenery)) return; // another flight came meanwhile
        state.manifest = manifest;
        state.airfields = airfields;
      } catch (e) {
        console.warn(`scenery ${scenery.name} not available: ${e.message}`);
      }
    }
    for (const f of listeners) f(world);
  },

  onChange(f) {
    listeners.add(f);
    return () => listeners.delete(f);
  },

  // The surface the aircraft can touch at world x (east), z (south): as the physics.
  groundAt(x, z) {
    if (!state.scenery) return Math.max(proceduralHeight(x, z), WATER_LEVEL_M);
    return heightAt(tiles, x, z);
  },

  // WorldCover class at a point (0: procedural world, outside the region or not loaded yet).
  landcoverAt(x, z) {
    return state.scenery ? landcoverAt(tiles, x, z) : 0;
  },

  // For the tile builders and tests: the region's tiles (loading on demand).
  tiles,
  tileKey: (x, z) => `${Math.floor(x / TILE_SIZE_M)},${Math.floor(z / TILE_SIZE_M)}`,
};
