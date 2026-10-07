// Builds terrain tile data off the page's main thread (terrain.js asks; terrainCore.js
// computes). Answers {key, spec, geometry, objects}, the typed arrays transferred.

import { tileGeometryData, tileObjectsData } from "./terrainCore.js";

self.onmessage = ({ data: r }) => {
  const geometry = tileGeometryData(r.tx, r.tz, r.segments);
  const objects = r.objects ? tileObjectsData(r.tx, r.tz, r.maxTrees, r.far) : null;
  const buffers = [geometry.position, geometry.color, geometry.fieldness, geometry.normal, geometry.index].map((a) => a.buffer);
  if (objects) buffers.push(objects.trees.buffer, objects.houses.buffer, objects.landmarks.buffer);
  self.postMessage({ key: r.key, spec: r.spec, geometry, objects }, buffers);
};
