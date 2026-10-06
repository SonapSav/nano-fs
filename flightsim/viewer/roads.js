// Roads between the villages (terrain.js villageCentre) and to the airfield: each village
// joins its two nearest neighbours within 5 km; the village nearest the airfield also gets
// a road to the airfield's north side. Visual only: the roads are ribbons draped over the
// terrain (the physics ground is unchanged), lifted over lakes as causeways.
//
// Rebuilt when the camera moves to another 2 km village cell, out to ~10 km around it.

import * as THREE from "three";
import { addGroundDetail } from "./groundDetail.js";
import { VILLAGE_CELL_M, WATER_LEVEL_M, height, villageCentre } from "./terrain.js";

const REACH_CELLS = 5; // villages this many cells around the camera's cell
const LINK_M = 5000;
const WIDTH_M = 7;
const STEP_M = 25; // drape sample spacing
const LIFT_M = 0.6; // above the terrain (the coarser far tiles can sit a little higher)
export const AIRFIELD_GATE = [40, -330]; // the end of the airfield's access road (scenery.js), behind the hangars

// Road segments [[x0, z0], [x1, z1]] for the villages around a cell (deterministic).
export function roadSegments(ci, cj) {
  const villages = [];
  for (let i = ci - REACH_CELLS - 2; i <= ci + REACH_CELLS + 2; i++)
    for (let j = cj - REACH_CELLS - 2; j <= cj + REACH_CELLS + 2; j++) {
      const v = villageCentre(i, j);
      if (v) villages.push({ v, near: Math.abs(i - ci) <= REACH_CELLS && Math.abs(j - cj) <= REACH_CELLS });
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

function ribbon(segments) {
  const pos = [], nrm = [], idx = [];
  for (const [[x0, z0], [x1, z1]] of segments) {
    const len = Math.sqrt((x1 - x0) ** 2 + (z1 - z0) ** 2);
    const n = Math.max(1, Math.ceil(len / STEP_M));
    const ux = (x1 - x0) / len, uz = (z1 - z0) / len, sx = -uz * WIDTH_M / 2, sz = ux * WIDTH_M / 2;
    const base = pos.length / 3;
    for (let k = 0; k <= n; k++) {
      const x = x0 + (x1 - x0) * (k / n), z = z0 + (z1 - z0) * (k / n);
      const y = Math.max(height(x, z), WATER_LEVEL_M + 0.8) + LIFT_M; // causeway over water
      pos.push(x + sx, y, z + sz, x - sx, y, z - sz);
      nrm.push(0, 1, 0, 0, 1, 0);
      if (k > 0) {
        const a = base + 2 * (k - 1);
        idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
      }
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(nrm, 3));
  g.setIndex(idx);
  return g;
}

export class RoadNetwork {
  constructor(scene) {
    this.scene = scene;
    this.material = addGroundDetail(new THREE.MeshLambertMaterial({ color: 0x55585b, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }), { strength: 0.25, tint: 0, fadeEndM: 250 });
    this.mesh = null;
    this.cell = null;
  }

  update(x, z) {
    const ci = Math.floor(x / VILLAGE_CELL_M), cj = Math.floor(z / VILLAGE_CELL_M);
    if (this.cell && this.cell[0] === ci && this.cell[1] === cj) return;
    this.cell = [ci, cj];
    if (this.mesh) {
      this.scene.remove(this.mesh);
      this.mesh.geometry.dispose();
    }
    this.mesh = new THREE.Mesh(ribbon(roadSegments(ci, cj)), this.material);
    this.scene.add(this.mesh);
  }
}
