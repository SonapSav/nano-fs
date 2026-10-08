// Builds moving-map tile images off the map window's main thread (mapTerrain.js).
// Request {key, mpp, ix, iz}; answer {key, pixels} (transferred).

import { mapTilePixels } from "./mapTerrain.js";

self.onmessage = ({ data: r }) => {
  const pixels = mapTilePixels(r.mpp, r.ix, r.iz);
  self.postMessage({ key: r.key, pixels }, [pixels.buffer]);
};
