// Roads between the villages (terrain.js villageCentre) and to the airfield: each village
// joins its two nearest neighbours within 5 km; the village nearest the airfield also gets
// a road to the airfield's north side. Visual only: the roads are ribbons draped over the
// terrain (the physics ground is unchanged), lifted over lakes as causeways.
//
// Rebuilt when the camera moves to another 2 km village cell, out to ~10 km around it.

import * as THREE from "three";
import { addGroundDetail } from "./groundDetail.js";
import { VILLAGE_CELL_M, WATER_LEVEL_M, height } from "./terrain.js";
import { roadSegments } from "./roadNet.js";

export { AIRFIELD_GATE, roadSegments } from "./roadNet.js";

const WIDTH_M = 7;
const STEP_M = 25; // drape sample spacing
const LIFT_M = 0.6; // above the terrain (the coarser far tiles can sit a little higher)

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
