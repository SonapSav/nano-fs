// The runways of a real-world region (its airfields.json, from OpenStreetMap; see
// flightsim/world/scenery_osm.py): asphalt with markings, edge, threshold and approach
// lights, on the ground the build flattened for them (a straight slope along each runway).
// Taxiways, aprons and buildings come from the region's other features; hold-short bars at
// OSM's holding positions; parked aircraft on some of the home airport's stands. Visual only.

const PARKED_MAX = 8; // aircraft parked at the home airport (project choice: each is a model of many parts)
const PARKED_RADIUS_M = 3000; // stands this close to the home runway's middle
const HOLD_BAR_M = 23; // across the taxiway

import * as THREE from "three";
import { addGroundDetail } from "./groundDetail.js";
import { addRunwayLights, parkedMatrix } from "./scenery.js";
import { buildC172 } from "./aircraft.js";
import { mergeByMaterial } from "./staticMerge.js";
import { runwayDescriptor } from "./runwayGeometry.js";

export { runwayDescriptor };

const MAX_TEXTURE_PX = 4096; // along the runway (a safe texture size on any WebGL device)
// The runway surface above the flattened terrain (the physics' ground): just enough to
// stay clear of the terrain in the logarithmic depth buffer (a few cm resolve kilometres
// away); more would sink the wheels into it (JSBSim's gear contacts already sit ~6 cm into
// the ground, its tyre and strut compression).
export const LIFT_M = 0.03;

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

  // Around the home runway: aircraft on some of its stands (every third, at most PARKED_MAX;
  // the same stands each time) and hold-short bars, at the runway's elevation (a flat
  // airfield; the region's ground tiles may still be loading).
  furnish(airfields, home) {
    this.clearFurniture();
    if (!home) return;
    this.furniture = new THREE.Group();
    const [[ax, az], [bx, bz]] = home.pavement, cx = (ax + bx) / 2, cz = (az + bz) / 2;
    const y = (home.ends[0].y + home.ends[1].y) / 2;
    const near = (p) => Math.hypot(p.east - cx, -p.north - cz) < PARKED_RADIUS_M;
    const stands = (airfields?.stands ?? []).filter((s) => near(s) && s.heading_deg !== null);
    // One model, baked at every stand and joined per material: a few draw calls for all
    // of them (parked: no lights, propeller stopped; one registration, A6-ABD, unreadable
    // from the air).
    const model = buildC172({ registration: "A6-ABD" });
    model.update({});
    model.settle(parkedMatrix(0, 0, 0, 0), () => 0); // tyres on the surface (placed at y + LIFT_M below)
    const parked = stands.filter((_, i) => i % 3 === 0).slice(0, PARKED_MAX);
    for (const mesh of mergeByMaterial(parked.map((s) => ({ object: model.group, matrix: parkedMatrix(s.east, -s.north, s.heading_deg, y + LIFT_M) })))) {
      this.furniture.add(mesh);
    }
    model.group.traverse((o) => o.geometry?.dispose());
    // Hold-short bars (FAA style: two solid and two dashed yellow lines across the
    // taxiway), joined into one mesh.
    const yellow = new THREE.MeshBasicMaterial({ color: 0xd9a92b, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4 });
    const bars = new THREE.Group();
    for (const h of airfields?.holds ?? []) {
      if (!near(h) || h.taxiway_deg === null) continue;
      const bar = new THREE.Group();
      for (const [off, dashed] of [[-0.9, false], [-0.3, false], [0.3, true], [0.9, true]]) {
        for (let k = dashed ? -5 : 0; k <= (dashed ? 5 : 0); k += 2) {
          const m = new THREE.Mesh(new THREE.PlaneGeometry(dashed ? 2 : HOLD_BAR_M, 0.3).rotateX(-Math.PI / 2), yellow);
          m.position.set(dashed ? k * 2 : 0, 0, off);
          bar.add(m);
        }
      }
      bar.position.set(h.east, y + 0.08, -h.north); // over the taxiway (featureGeometry.js PAVED_LIFT_M) and its centreline
      bar.rotation.y = -((h.taxiway_deg * Math.PI) / 180); // bars across the taxiway (map bearing, clockwise from north)
      bars.add(bar);
    }
    for (const mesh of mergeByMaterial([{ object: bars, matrix: new THREE.Matrix4() }])) this.furniture.add(mesh);
    bars.traverse((o) => o.geometry?.dispose());
    this.group.add(this.furniture);
  }

  clearFurniture() {
    if (!this.furniture) return;
    this.group.remove(this.furniture);
    this.furniture.traverse((o) => {
      o.geometry?.dispose();
      o.material?.dispose();
    });
    this.furniture = null;
  }

  // The windsock nearest the home runway (OSM), within 2 km of its middle; null if none.
  windsockNear(airfields, home) {
    if (!home) return null;
    const [[ax, az], [bx, bz]] = home.pavement, cx = (ax + bx) / 2, cz = (az + bz) / 2;
    const best = (airfields?.windsocks ?? []).map((w) => [Math.hypot(w.east - cx, -w.north - cz), w]).sort((a, b) => a[0] - b[0])[0];
    return best && best[0] < 2000 ? { x: best[1].east, z: -best[1].north } : null;
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
    this.furniture = null; // disposed with the rest
    this.group.traverse((o) => {
      o.geometry?.dispose();
      if (o.material?.map) o.material.map.dispose();
    });
    this.group.clear();
    this.runways = [];
  }
}
