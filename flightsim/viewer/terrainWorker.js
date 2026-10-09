// Builds terrain tile data off the page's main thread (terrain.js asks; terrainCore.js
// computes procedural tiles, demTiles.js a real-world region's). Answers {key, spec,
// geometry, objects}, the typed arrays transferred; geometry null: no tile there.
//
// A region's tiles (heights of the tile and its neighbours, land cover) are fetched from
// the server and kept here for later builds.

import { tileGeometryData, tileObjectsData } from "./terrainCore.js";
import { demTileGeometryData, demTileObjectsData, neededTiles } from "./demTiles.js";
import { heightsName, landcoverName } from "./demCore.js";

let region = null; // {scenery, tiles: {ix_min, ...}, heights: Map, landcover: Map}

const url = (file) => `scenery/${encodeURIComponent(region.scenery.name)}/${file}?h=${region.scenery.hash.slice(0, 16)}`;
const inside = (ix, iz) => ix >= region.tiles.ix_min && ix <= region.tiles.ix_max && iz >= region.tiles.iz_min && iz <= region.tiles.iz_max;

async function fetchTile(store, name, Type, ix, iz) {
  const key = `${ix},${iz}`;
  if (!store.has(key)) {
    store.set(key, !inside(ix, iz) ? null : fetch(url(name(ix, iz))).then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(r.status)))).then((b) => new Type(b)));
  }
  try {
    return await store.get(key);
  } catch {
    store.delete(key);
    return null;
  }
}

async function demBuild(r) {
  if (!region || region.scenery.hash !== r.scenery.hash) {
    region = { scenery: r.scenery, tiles: r.tiles, heights: new Map(), landcover: new Map() };
  }
  const h = new Map(), lc = new Map();
  await Promise.all([
    ...neededTiles(r.tx, r.tz).map(async ([ix, iz]) => h.set(`${ix},${iz}`, await fetchTile(region.heights, heightsName, Float32Array, ix, iz))),
    fetchTile(region.landcover, landcoverName, Uint8Array, r.tx, r.tz).then((t) => lc.set(`${r.tx},${r.tz}`, t)),
  ]);
  const tiles = { heights: (ix, iz) => h.get(`${ix},${iz}`) ?? null, landcover: (ix, iz) => lc.get(`${ix},${iz}`) ?? null };
  const geometry = demTileGeometryData(r.tx, r.tz, r.segments, tiles, r.textureSize);
  const objects = geometry && r.objects ? demTileObjectsData(r.tx, r.tz, r.maxTrees, r.far, tiles) : null;
  return { geometry, objects };
}

self.onmessage = async ({ data: r }) => {
  const { geometry, objects } = r.scenery
    ? await demBuild(r)
    : { geometry: tileGeometryData(r.tx, r.tz, r.segments), objects: r.objects ? tileObjectsData(r.tx, r.tz, r.maxTrees, r.far) : null };
  const buffers = geometry ? [geometry.position, geometry.color, geometry.fieldness, geometry.normal, geometry.index, geometry.uv, geometry.texture].filter(Boolean).map((a) => a.buffer) : [];
  if (objects) buffers.push(objects.trees.buffer, objects.houses.buffer, objects.landmarks.buffer);
  self.postMessage({ key: r.key, spec: r.spec, geometry, objects }, buffers);
};
