// Builds terrain tile data off the page's main thread (terrain.js asks; terrainCore.js
// computes procedural tiles, demTiles.js a real-world region's). Answers {key, spec,
// geometry, objects}, the typed arrays transferred; geometry null: no tile there.
//
// A region's tiles (heights of the tile and its neighbours, land cover) are fetched from
// the server and kept here for later builds.

import { tileGeometryData, tileObjectsData } from "./terrainCore.js";
import { demTileGeometryData, demTileObjectsData, neededTiles } from "./demTiles.js";
import { heightsName, landcoverName, shoreName } from "./demCore.js";
import { buildingData, featureGroundData } from "./featureGeometry.js";

const featuresName = (ix, iz) => `tiles/f_${ix}_${iz}.json`;
const imageryName = (ix, iz) => `tiles/i_${ix}_${iz}.jpg`;

// A tile's imagery (a JPEG, decoded here; distant tiles smaller), or null (none built).
async function fetchImagery(tx, tz, px) {
  if (!inside(tx, tz)) return null;
  try {
    const r = await fetch(url(imageryName(tx, tz)));
    if (!r.ok) return null;
    return await createImageBitmap(await r.blob(), px ? { resizeWidth: px, resizeHeight: px, resizeQuality: "medium" } : {});
  } catch {
    return null;
  }
}

let region = null; // {scenery, tiles: {ix_min, ...}, heights: Map, landcover: Map}

const url = (file) => `scenery/${encodeURIComponent(region.scenery.name)}/${file}?h=${region.scenery.hash.slice(0, 16)}`;
const inside = (ix, iz) => ix >= region.tiles.ix_min && ix <= region.tiles.ix_max && iz >= region.tiles.iz_min && iz <= region.tiles.iz_max;

async function fetchTile(store, name, Type, ix, iz) {
  const key = `${ix},${iz}`;
  if (!store.has(key)) {
    const parse = Type === "json" ? (r) => r.json() : (r) => r.arrayBuffer().then((b) => new Type(b));
    store.set(key, !inside(ix, iz) ? null : fetch(url(name(ix, iz))).then((r) => (r.ok ? parse(r) : Promise.reject(new Error(r.status)))));
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
    region = { scenery: r.scenery, tiles: r.tiles, heights: new Map(), landcover: new Map(), shore: new Map(), features: new Map() };
  }
  const h = new Map(), lc = new Map();
  const shore = await fetchTile(region.shore, shoreName, Uint8Array, r.tx, r.tz);
  await Promise.all([
    ...neededTiles(r.tx, r.tz).map(async ([ix, iz]) => h.set(`${ix},${iz}`, await fetchTile(region.heights, heightsName, Float32Array, ix, iz))),
    fetchTile(region.landcover, landcoverName, Uint8Array, r.tx, r.tz).then((t) => lc.set(`${r.tx},${r.tz}`, t)),
  ]);
  const tiles = {
    heights: (ix, iz) => h.get(`${ix},${iz}`) ?? null, landcover: (ix, iz) => lc.get(`${ix},${iz}`) ?? null,
    shore: (ix, iz) => (ix === r.tx && iz === r.tz ? shore : null),
  };  // fmt: skip
  const geometry = demTileGeometryData(r.tx, r.tz, r.segments, tiles, r.textureSize);
  if (geometry) geometry.imagery = await fetchImagery(r.tx, r.tz, r.objects ? 0 : 100);
  const objects = geometry && r.objects ? demTileObjectsData(r.tx, r.tz, r.maxTrees, r.far, tiles) : null;
  // OpenStreetMap features: roads and paving on near tiles, buildings (all near, only the
  // taller ones farther out).
  let features = null;
  if (geometry && r.buildingsMinM !== null) {
    const f = await fetchTile(region.features, featuresName, "json", r.tx, r.tz);
    if (f) features = { ...(r.ground ? featureGroundData(f, tiles) : {}), buildings: buildingData(f, tiles, r.buildingsMinM) };
  }
  return { geometry, objects, features };
}

const featureBuffers = (f) => Object.values(f ?? {}).filter(Boolean).flatMap((m) => Object.values(m).map((a) => a.buffer));

self.onmessage = async ({ data: r }) => {
  const { geometry, objects, features = null } = r.scenery
    ? await demBuild(r)
    : { geometry: tileGeometryData(r.tx, r.tz, r.segments), objects: r.objects ? tileObjectsData(r.tx, r.tz, r.maxTrees, r.far) : null };
  const buffers = geometry ? [geometry.position, geometry.color, geometry.fieldness, geometry.normal, geometry.index, geometry.uv, geometry.texture, geometry.shore].filter(Boolean).map((a) => a.buffer) : [];
  if (geometry?.imagery) buffers.push(geometry.imagery); // an ImageBitmap
  if (objects) buffers.push(...[objects.trees, objects.houses, objects.landmarks, objects.palms, objects.bushes].filter(Boolean).map((a) => a.buffer));
  buffers.push(...featureBuffers(features));
  self.postMessage({ key: r.key, spec: r.spec, geometry, objects, features }, [...new Set(buffers)]); // (arrays may share a buffer)
};
