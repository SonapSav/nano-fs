// Three.js scene: flat-earth local frame, simple C172 model, trail, target line, chase camera.
//
// World frame: x = east, y = up, z = south (three.js is y-up, right-handed), origin at
// latitude 0, longitude 0 at sea level, where every task starts (so the procedural
// scenery and its airfield are always in the same place). Over the tens of km a flight
// covers, the flat-earth approximation is far below anything visible.

import * as THREE from "three";
import { addAirfield, addGroundFallback, addSky } from "./scenery.js";
import { Terrain } from "./terrain.js";

const R_EARTH = 6371000;
const TRAIL_POINTS = 4000;

// NED vector -> world vector.
const nedToWorld = (n, e, d) => new THREE.Vector3(e, -d, -n);

// Pilot's eye in body axes (x forward, y right, z down), metres, relative to the CG:
// c172p EYEPOINT (x 37 in, z 48 in) vs CG (x 41 in, z 36.5 in) in the structural frame
// (x aft, z up), and the pilot's seat at y -14 in (left seat).
const EYE_BODY = new THREE.Vector3(0.10, -0.36, -0.29);
// Camera axes expressed in body axes: camera right = body right, up = -body z, back = -body x.
const BODY_FROM_CAMERA = new THREE.Matrix4().makeBasis(
  new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, -1), new THREE.Vector3(-1, 0, 0),
);

// What a pilot sees of the aircraft from the left seat: the glare shield and the cowling
// sloping down ahead (the sight picture for pitch), and the high wing's roots overhead.
function buildCockpit() {
  // Body axes: x forward, y right, z down; the eye is at EYE_BODY (z = -0.29).
  const g = new THREE.Group();
  const panel = new THREE.MeshBasicMaterial({ color: 0x24272b, side: THREE.DoubleSide });
  const cowl = new THREE.MeshLambertMaterial({ color: 0xe9e9e4, side: THREE.DoubleSide });
  const frame = new THREE.MeshBasicMaterial({ color: 0x2b2f33 });
  const add = (geo, mat, x, y, z, rx = 0, ry = 0, rz = 0) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.rotation.set(rx, ry, rz);
    g.add(m);
  };
  // Cowling: a horizontal surface ahead (PlaneGeometry lies in body x-y), its far end
  // sloping down; its top edge sits ~14 deg below the eye line in level flight.
  add(new THREE.PlaneGeometry(1.7, 1.0), cowl, 1.75, 0, 0.12, 0, -0.12, 0);
  // Instrument panel face below the glare shield (the 2D panel continues below the view).
  add(new THREE.PlaneGeometry(1.0, 1.3), panel, 0.72, 0, 0.42, 0, Math.PI / 2, 0);
  add(new THREE.BoxGeometry(0.3, 1.3, 0.03), panel, 0.66, 0, -0.08); // glare shield top
  // Windscreen posts at the sides, leaning back towards the roof.
  for (const y of [-0.66, 0.66]) add(new THREE.BoxGeometry(0.05, 0.05, 0.85), frame, 0.62, y, -0.48, 0, 0.45, 0);
  add(new THREE.BoxGeometry(1.2, 1.4, 0.04), frame, 0.15, 0, -0.9); // cabin roof under the wing
  // Door panels below the side windows (the window line is about level with the glare shield).
  for (const y of [-0.62, 0.62]) add(new THREE.PlaneGeometry(1.5, 0.8), panel, 0.1, y, 0.32, Math.PI / 2, 0, 0);
  return g;
}

function buildAircraft() {
  // Built in body axes (x forward, y right, z down), metres, C172-like proportions.
  const g = new THREE.Group();
  const white = new THREE.MeshLambertMaterial({ color: 0xf4f4f0 });
  const stripe = new THREE.MeshLambertMaterial({ color: 0x8a1f2b });
  const dark = new THREE.MeshLambertMaterial({ color: 0x2b2f33 });
  const glass = new THREE.MeshLambertMaterial({ color: 0x5f7f99 });
  const box = (sx, sy, sz, mat, x, y, z) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat);
    m.position.set(x, y, z);
    g.add(m);
    return m;
  };
  box(4.2, 1.1, 1.3, white, 0.6, 0, 0); // cabin and engine
  box(3.6, 0.6, 0.7, white, -3.0, 0, -0.15); // tail cone
  box(1.6, 1.12, 0.5, glass, 0.9, 0, -0.55); // windows
  box(7.6, 0.08, 0.14, stripe, -0.9, 0, 0.1).position.y = 0.56; // side stripe
  box(7.6, 0.08, 0.14, stripe, -0.9, 0, 0.1).position.y = -0.56;
  box(1.5, 10.9, 0.14, white, 0.5, 0, -0.75); // high wing
  box(0.25, 0.08, 1.2, dark, 0.7, 1.4, -0.1).rotation.x = 0.35; // struts
  box(0.25, 0.08, 1.2, dark, 0.7, -1.4, -0.1).rotation.x = -0.35;
  box(0.9, 3.4, 0.08, white, -4.6, 0, -0.2); // horizontal tail
  box(1.1, 0.08, 1.5, white, -4.7, 0, -0.95); // fin
  box(0.06, 1.9, 0.12, dark, 2.75, 0, 0.05); // propeller
  box(0.6, 0.4, 0.4, dark, 0.9, 0.9, 0.95); // main gear
  box(0.6, 0.4, 0.4, dark, 0.9, -0.9, 0.95);
  box(0.5, 0.3, 0.35, dark, 2.2, 0, 0.9); // nose gear
  return g;
}

export class FlightScene {
  constructor(container) {
    this.container = container;
    // Logarithmic depth: from 0.5 m to 100+ km without distant surfaces flickering.
    this.renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping; // the physical sky is HDR
    this.renderer.toneMappingExposure = 0.55;
    container.prepend(this.renderer.domElement);

    this.scene = new THREE.Scene();
    addSky(this.scene);
    addGroundFallback(this.scene);
    addAirfield(this.scene);
    this.terrain = new Terrain(this.scene);

    this.aircraft = buildAircraft();
    this.aircraft.matrixAutoUpdate = false;
    this.scene.add(this.aircraft);
    this.cockpit = buildCockpit();
    this.cockpit.matrixAutoUpdate = false;
    this.cockpit.visible = false;
    this.scene.add(this.cockpit);
    this.view = "chase";
    this.head = { yaw: 0, pitch: 0 }; // cockpit head turn, radians

    this.trailGeo = new THREE.BufferGeometry();
    this.trailGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(TRAIL_POINTS * 3), 3));
    this.trailGeo.setDrawRange(0, 0);
    this.trail = new THREE.Line(this.trailGeo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.7 }));
    this.trail.frustumCulled = false;
    this.scene.add(this.trail);

    // Target: a magenta line along the target heading at the target altitude.
    this.targetLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color: 0xd23cc8 }),
    );
    this.targetLine.frustumCulled = false;
    this.targetLine.visible = false;
    this.scene.add(this.targetLine);

    this.camera = new THREE.PerspectiveCamera(60, 1, 0.5, 120000);
    this.orbit = { azimuth: 0, elevation: 0.18, distance: 32 };
    this._bindPointer();
    this.reset();
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
  }

  reset() {
    this.origin = { lat: 0, lon: 0 }; // fixed, see the header
    this.trailCount = 0;
    this.trailGeo.setDrawRange(0, 0);
    this.targets = null;
    this.targetLine.visible = false;
    this.position = new THREE.Vector3(0, 1500, 0);
    this.aircraft.matrix.identity().setPosition(this.position);
  }

  setTargets(targets) {
    this.targets = targets;
  }

  update(row) {
    const n = (row.lat_rad - this.origin.lat) * R_EARTH;
    const e = (row.lon_rad - this.origin.lon) * R_EARTH * Math.cos(this.origin.lat);
    this.position = nedToWorld(n, e, -row.alt_msl_m);

    // Body axes in NED from ZYX Euler angles, mapped to world. The model is built in body
    // axes, so these three vectors are its basis.
    const [cf, sf, ct, st, cp, sp] = [row.phi_rad, row.theta_rad, row.psi_rad].flatMap((a) => [Math.cos(a), Math.sin(a)]);
    const xb = nedToWorld(ct * cp, ct * sp, -st);
    const yb = nedToWorld(sf * st * cp - cf * sp, sf * st * sp + cf * cp, sf * ct);
    const zb = nedToWorld(cf * st * cp + sf * sp, cf * st * sp - sf * cp, cf * ct);
    this.aircraft.matrix.makeBasis(xb, yb, zb).setPosition(this.position);
    this.heading = row.psi_rad;

    const pos = this.trailGeo.attributes.position;
    if (this.trailCount === TRAIL_POINTS) {
      pos.array.copyWithin(0, 3);
      this.trailCount--;
    }
    pos.setXYZ(this.trailCount++, this.position.x, this.position.y, this.position.z);
    pos.needsUpdate = true;
    this.trailGeo.setDrawRange(0, this.trailCount);

    if (this.targets) {
      const h = this.targets.heading_rad;
      const start = new THREE.Vector3(this.position.x, this.targets.alt_msl_m, this.position.z);
      const end = start.clone().add(nedToWorld(Math.cos(h), Math.sin(h), 0).multiplyScalar(3000));
      this.targetLine.geometry.setFromPoints([start, end]);
      this.targetLine.visible = true;
    }
  }

  setView(view) {
    this.view = view;
    const inside = view === "cockpit";
    this.aircraft.visible = !inside; // seen from inside, the exterior model would block the view
    this.cockpit.visible = inside;
    this.camera.near = inside ? 0.05 : 0.5;
    this.camera.fov = inside ? 70 : 60;
    this.camera.updateProjectionMatrix();
    this.head = { yaw: 0, pitch: 0 };
  }

  render() {
    if (this.view === "cockpit") {
      // Eye fixed in the aircraft; the camera rotates with it, plus the pilot's head turn.
      this.cockpit.matrix.copy(this.aircraft.matrix);
      this.camera.position.copy(EYE_BODY).applyMatrix4(this.aircraft.matrix);
      const rot = new THREE.Matrix4().extractRotation(this.aircraft.matrix).multiply(BODY_FROM_CAMERA);
      const head = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(this.head.pitch, this.head.yaw, 0, "YXZ"));
      this.camera.quaternion.setFromRotationMatrix(rot.multiply(head));
      this.terrain.update(this.camera.position.x, this.camera.position.z);
      this.renderer.render(this.scene, this.camera);
      return;
    }
    // Chase camera: follows heading only (not bank or pitch), plus the user's orbit offset.
    const az = (this.heading ?? 0) + Math.PI + this.orbit.azimuth;
    const el = this.orbit.elevation;
    const d = this.orbit.distance;
    const offset = nedToWorld(Math.cos(az) * Math.cos(el) * d, Math.sin(az) * Math.cos(el) * d, -Math.sin(el) * d);
    this.camera.position.copy(this.position).add(offset);
    this.camera.lookAt(this.position);
    this.terrain.update(this.camera.position.x, this.camera.position.z);
    this.renderer.render(this.scene, this.camera);
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _bindPointer() {
    const el = this.renderer.domElement;
    let drag = null;
    el.addEventListener("pointerdown", (e) => {
      drag = { x: e.clientX, y: e.clientY };
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener("pointermove", (e) => {
      if (!drag) return;
      if (this.view === "cockpit") {
        // Turn the head: drag right to look right, up to look up.
        this.head.yaw = Math.max(-2.6, Math.min(2.6, this.head.yaw - (e.clientX - drag.x) * 0.004));
        this.head.pitch = Math.max(-0.9, Math.min(0.9, this.head.pitch - (e.clientY - drag.y) * 0.004));
      } else {
        this.orbit.azimuth -= (e.clientX - drag.x) * 0.005;
        this.orbit.elevation = Math.max(-0.3, Math.min(1.4, this.orbit.elevation + (e.clientY - drag.y) * 0.005));
      }
      drag = { x: e.clientX, y: e.clientY };
    });
    el.addEventListener("pointerup", () => (drag = null));
    el.addEventListener("dblclick", () => {
      this.orbit.azimuth = 0;
      this.head = { yaw: 0, pitch: 0 };
    });
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      if (this.view === "cockpit") return;
      this.orbit.distance = Math.max(12, Math.min(3000, this.orbit.distance * Math.exp(e.deltaY * 0.001)));
    }, { passive: false });
  }
}
