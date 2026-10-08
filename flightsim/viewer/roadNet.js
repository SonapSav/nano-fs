// The road network between the villages (terrainCore.js villageCentre) and to the
// airfield, as segments (no three.js: drawn as ribbons by roads.js and as lines by the
// moving map). Deterministic: the same cells always give the same roads.

import { villageCentre } from "./terrainCore.js";

const REACH_CELLS = 5; // villages this many cells around the camera's cell
const LINK_M = 5000;
export const AIRFIELD_GATE = [40, -330]; // the end of the airfield's access road (scenery.js), behind the hangars

// Road segments [[x0, z0], [x1, z1]] for the villages around a cell (deterministic).
export function roadSegments(ci, cj, reach = REACH_CELLS) {
  const villages = [];
  for (let i = ci - reach - 2; i <= ci + reach + 2; i++)
    for (let j = cj - reach - 2; j <= cj + reach + 2; j++) {
      const v = villageCentre(i, j);
      if (v) villages.push({ v, near: Math.abs(i - ci) <= reach && Math.abs(j - cj) <= reach });
    }
  const dist = (a, b) => Math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2);
  const edges = new Map();
  for (const a of villages) {
    if (!a.near) continue;
    const others = villages.filter((b) => b !== a).map((b) => [dist(a.v, b.v), b.v]).filter(([d]) => d <= LINK_M).sort((p, q) => p[0] - q[0]);
    for (const [, b] of others.slice(0, 2)) {
      const key = [a.v, b].map((p) => p.join(",")).sort().join(";");
      edges.set(key, [a.v, b]);
    }
  }
  const nearest = villages.map((a) => [dist(a.v, AIRFIELD_GATE), a.v]).sort((p, q) => p[0] - q[0])[0];
  if (nearest && nearest[0] <= LINK_M * 1.6) edges.set("airfield", [AIRFIELD_GATE, nearest[1]]);
  return [...edges.values()];
}
