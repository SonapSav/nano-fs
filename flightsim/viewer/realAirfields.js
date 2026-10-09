// The runways of a real-world region (its airfields.json, from OpenStreetMap; see
// flightsim/world/scenery_osm.py): asphalt with markings, edge, threshold and approach
// lights, on the ground the build flattened for them (a straight slope along each runway).
// Taxiways, aprons and buildings come from the region's other features. Visual only.

import * as THREE from "three";
import { addGroundDetail } from "./groundDetail.js";
import { addRunwayLights } from "./scenery.js";
import { runwayDescriptor } from "./runwayGeometry.js";

export { runwayDescriptor };

const MAX_TEXTURE_PX = 4096; // along the runway (a safe texture size on any WebGL device)
const LIFT_M = 0.15; // the runway surface above the flattened terrain

// Markings (FAA style, simplified; project choices where noted): edge stripes, centreline
// (30 m stripes, 20 m gaps), threshold bars and "piano keys" (one 1.8 m stripe per 3.75 m
// of width, project choice), the runway number, aiming point markers 250 m past each
// threshold (as the approach task's aim point); the pavement before a displaced threshold
// stays plain, with a white bar at the threshold.
function runwayTexture(d, thresholdsAlong) {
  const L = d.lengthM, W = d.widthM;
  const pxL = Math.min(2, MAX_TEXTURE_PX / L), pxW = 4;
  const c = document.createElement("canvas");
  c.width = Math.round(L * pxL);
  c.height = Math.round(W * pxW);
  const g = c.getContext("2d");
  g.fillStyle = "#3b3d40";
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = "#e8e8e2";
  const [t0, t1] = thresholdsAlong;
  g.fillRect(t0 * pxL, 0.3 * pxW, (t1 - t0) * pxL, 0.9 * pxW);
  g.fillRect(t0 * pxL, c.height - 1.2 * pxW, (t1 - t0) * pxL, 0.9 * pxW);
  for (let s = t0 + 120; s < t1 - 120; s += 50) g.fillRect(s * pxL, c.height / 2 - 0.45 * pxW, 30 * pxL, 0.9 * pxW);
  const keys = Math.max(4, Math.round(W / 3.75));
  for (const [s0, dir, label] of [[t0, 1, d.ends[0].ident], [t1, -1, d.ends[1].ident]]) {
    g.fillRect(Math.min(s0, s0 + dir * 3) * pxL, 0, 3 * pxL, c.height); // threshold bar
    for (let k = 0; k < keys; k++) {
      const y = ((k + 0.5) * (W - 6)) / keys + 3 - 0.9 + (k >= keys / 2 ? 0.9 : -0.9);
      g.fillRect(Math.min(s0 + dir * 6, s0 + dir * 36) * pxL, y * pxW, 30 * pxL, 1.8 * pxW);
    }
    g.save();
    g.translate((s0 + dir * 60) * pxL, c.height / 2);
    g.rotate(dir > 0 ? Math.PI / 2 : -Math.PI / 2);
    g.font = `bold ${Math.min(9, W / 4) * pxW}px sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.scale(1, (pxL / pxW) * 2.2);
    g.fillText(label, 0, 0);
    g.restore();
    for (const y of [W / 2 - 15, W / 2 + 9]) g.fillRect(Math.min(s0 + dir * 250, s0 + dir * 295) * pxL, y * pxW, 45 * pxL, 6 * pxW);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

function runwayMesh(d) {
  const [[ax, az], [bx, bz]] = d.pavement;
  const L = Math.hypot(bx - ax, bz - az), ux = (bx - ax) / L, uz = (bz - az) / L;
  const lx = uz, lz = -ux; // left of the direction from the first end (v = 1 there)
  const hw = d.widthM / 2;
  const y0 = d.base + LIFT_M, y1 = d.base + d.slope * L + LIFT_M;
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute([
    ax + lx * hw, y0, az + lz * hw, ax - lx * hw, y0, az - lz * hw, bx + lx * hw, y1, bz + lz * hw, bx - lx * hw, y1, bz - lz * hw,
  ], 3));  // fmt: skip
  g.setAttribute("uv", new THREE.Float32BufferAttribute([0, 1, 0, 0, 1, 1, 1, 0], 2));
  g.setAttribute("normal", new THREE.Float32BufferAttribute([0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0], 3));
  g.setIndex([0, 1, 2, 2, 1, 3]);
  const along = (e) => (e.x - ax) * ux + (e.z - az) * uz;
  const tex = runwayTexture({ ...d, lengthM: L }, [along(d.ends[0]), along(d.ends[1])]);
  const material = addGroundDetail(new THREE.MeshLambertMaterial({ map: tex, side: THREE.DoubleSide }), { strength: 0.25, tint: 0, fadeEndM: 250 });
  return new THREE.Mesh(g, material);
}

export class RealAirfields {
  constructor(scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    scene.add(this.group);
    this.runways = []; // descriptors
  }

  // Draws a region's runways (null: none).
  build(airfields) {
    this.clear();
    for (const rw of airfields?.runways ?? []) {
      const d = runwayDescriptor(rw);
      this.runways.push(d);
      this.group.add(runwayMesh(d));
      addRunwayLights(this.group, d);
    }
  }

  // The runway nearest a world point (x, z): where the PAPI and windsock go.
  nearest(x, z) {
    let best = null, dist = Infinity;
    for (const d of this.runways) {
      const [[ax, az], [bx, bz]] = d.pavement;
      const r = Math.hypot((ax + bx) / 2 - x, (az + bz) / 2 - z);
      if (r < dist) [best, dist] = [d, r];
    }
    return best;
  }

  clear() {
    this.group.traverse((o) => {
      o.geometry?.dispose();
      if (o.material?.map) o.material.map.dispose();
    });
    this.group.clear();
    this.runways = [];
  }
}
