// A real-world region's moving traffic (its traffic.json, from the scenery build's
// scenery_traffic.py): each entry's model (realAirfields.js loadModel, rotors turning)
// flown along its route at its speed and altitude, timed by trafficCore.js to pass its
// meeting point when the flown aircraft does; its shadow flattened onto the ground below.
// Visual only: it reads the shown frames, nothing reaches the physics.

import * as THREE from "three";
import { loadModel } from "./realAirfields.js";
import { Schedule } from "./trafficCore.js";
import { world } from "./world.js";

export class Traffic {
  constructor(scene, skyLight) {
    this.scene = scene;
    this.skyLight = skyLight;
    this.group = new THREE.Group();
    scene.add(this.group);
    this.movers = [];
  }

  // The region's traffic list (null or empty: none).
  set(list) {
    for (const m of this.movers) {
      this.group.remove(m.root);
      m.root.traverse((o) => {
        o.geometry?.dispose();
        o.material?.dispose();
      });
    }
    this.movers = (list ?? []).map((t) => {
      const root = new THREE.Group();
      root.matrixAutoUpdate = false;
      root.visible = false;
      this.group.add(root);
      const mover = { route: t, schedule: new Schedule(t), root, groundY: 0 };
      loadModel({ model: t.model, turning: true }, root, {
        skyLight: this.skyLight, flatBody: true, shadowY: () => mover.groundY + 0.06, alive: () => this.movers.includes(mover),
      });  // fmt: skip
      return mover;
    });
  }

  // Each shown frame: the flown aircraft's place and ground velocity on the map.
  update(row, geodesy) {
    if (!this.movers.length) return;
    const [north, east] = geodesy.toMap(row.lat_rad, row.lon_rad);
    const g = geodesy.convergence(row.lat_rad, row.lon_rad); // map bearing = true bearing - g
    const [cg, sg] = [Math.cos(g), Math.sin(g)];
    const f = { t_s: row.t_s, north, east, v_north: row.v_north_mps * cg + row.v_east_mps * sg, v_east: -row.v_north_mps * sg + row.v_east_mps * cg };
    for (const m of this.movers) {
      const p = m.schedule.update(f);
      m.root.visible = p !== null;
      if (!p) continue;
      const x = p.east, z = -p.north, y = m.route.alt_m;
      m.groundY = world.groundAt(x, z);
      const rad = Math.PI / 180;
      m.root.matrix
        .makeRotationY(Math.PI / 2 - p.heading)
        .multiply(new THREE.Matrix4().makeRotationZ((m.route.pitch_deg ?? 0) * rad))
        .multiply(new THREE.Matrix4().makeRotationX(p.bank))
        .setPosition(x, y, z);
      m.root.matrixWorldNeedsUpdate = true;
    }
  }
}
