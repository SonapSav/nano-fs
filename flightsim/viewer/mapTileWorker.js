// Builds moving-map tile images off the map window's main thread: the procedural world's
// (mapTerrain.js) or a real-world region's (mapRegion.js, from the region's tiles, fetched
// here and kept). Request {key, mpp, ix, iz, scenery?, tiles?}; answer {key, pixels}
// (transferred).

import { mapTilePixels } from "./mapTerrain.js";
import { regionTilePixels, regionTilesFor } from "./mapRegion.js";
import { heightsName, landcoverName } from "./demCore.js";

let region = null; // {scenery, tiles (index range), heights: Map, landcover: Map}

function fetchTile(store, name, Type, ix, iz) {
  const key = `${ix},${iz}`, t = region.tiles;
  if (!store.has(key)) {
    const inside = ix >= t.ix_min && ix <= t.ix_max && iz >= t.iz_min && iz <= t.iz_max;
    const url = `scenery/${encodeURIComponent(region.scenery.name)}/${name(ix, iz)}?h=${region.scenery.hash.slice(0, 16)}`;
    store.set(key, inside ? fetch(url).then((r) => (r.ok ? r.arrayBuffer() : null)).then((b) => (b ? new Type(b) : null)).catch(() => null) : Promise.resolve(null));
  }
  return store.get(key);
}

async function regionPixels(r) {
  if (!region || region.scenery.hash !== r.scenery.hash) region = { scenery: r.scenery, tiles: r.tiles, heights: new Map(), landcover: new Map() };
  const h = new Map(), lc = new Map();
  await Promise.all(regionTilesFor(r.mpp, r.ix, r.iz).map(async ([i, k]) => {
    h.set(`${i},${k}`, await fetchTile(region.heights, heightsName, Float32Array, i, k));
    lc.set(`${i},${k}`, await fetchTile(region.landcover, landcoverName, Uint8Array, i, k));
  }));  // fmt: skip
  return regionTilePixels(r.mpp, r.ix, r.iz, { heights: (i, k) => h.get(`${i},${k}`) ?? null, landcover: (i, k) => lc.get(`${i},${k}`) ?? null });
}

self.onmessage = async ({ data: r }) => {
  const pixels = r.scenery ? await regionPixels(r) : mapTilePixels(r.mpp, r.ix, r.iz);
  self.postMessage({ key: r.key, pixels }, [pixels.buffer]);
};
