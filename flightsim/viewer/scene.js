// Three.js scene: flat-earth local frame, simple C172 model, trail, target line, chase camera.
//
// World frame: x = east, y = up, z = south (three.js is y-up, right-handed), origin at
// the first frame's position at sea level. Over the tens of km a flight covers, the
// flat-earth approximation is far below anything visible.

import * as THREE from "three";

const R_EARTH = 6371000;
const TRAIL_POINTS = 4000;

// NED vector -> world vector.
const nedToWorld = (n, e, d) => new THREE.Vector3(e, -d, -n);

function seededRandom(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
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

function buildGround(scene) {
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(400000, 400000),
    new THREE.MeshLambertMaterial({ color: 0x7c8b55 }),
  );
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  const grid = new THREE.GridHelper(200000, 200, 0x4c5733, 0x55613a); // 1 km squares
  grid.position.y = 0.5;
  scene.add(grid);
  // Scattered farm buildings for motion cues; fixed seed so every viewer sees the same land.
  const rnd = seededRandom(172);
  const geo = new THREE.BoxGeometry(1, 1, 1);
  const mats = [0xb3a68a, 0x8f3b2f, 0xd9d4c5].map((c) => new THREE.MeshLambertMaterial({ color: c }));
  const count = 1500;
  for (let m = 0; m < mats.length; m++) {
    const inst = new THREE.InstancedMesh(geo, mats[m], count / mats.length);
    const mtx = new THREE.Matrix4();
    for (let i = 0; i < count / mats.length; i++) {
      const w = 15 + rnd() * 40, h = 6 + rnd() * 12, d = 15 + rnd() * 30;
      mtx.compose(
        new THREE.Vector3((rnd() - 0.5) * 60000, h / 2, (rnd() - 0.5) * 60000),
        new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), rnd() * Math.PI),
        new THREE.Vector3(w, h, d),
      );
      inst.setMatrixAt(i, mtx);
    }
    scene.add(inst);
  }
}

export class FlightScene {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.prepend(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x9cc3e4);
    this.scene.fog = new THREE.Fog(0xc9dbe8, 4000, 45000);
    this.scene.add(new THREE.HemisphereLight(0xdfeefa, 0x5d6b3f, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.8);
    sun.position.set(-0.4, 1, 0.3);
    this.scene.add(sun);
    buildGround(this.scene);

    this.aircraft = buildAircraft();
    this.aircraft.matrixAutoUpdate = false;
    this.scene.add(this.aircraft);

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
    this.origin = null;
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
    if (!this.origin) this.origin = { lat: row.lat_rad, lon: row.lon_rad };
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

  render() {
    // Chase camera: follows heading only (not bank or pitch), plus the user's orbit offset.
    const az = (this.heading ?? 0) + Math.PI + this.orbit.azimuth;
    const el = this.orbit.elevation;
    const d = this.orbit.distance;
    const offset = nedToWorld(Math.cos(az) * Math.cos(el) * d, Math.sin(az) * Math.cos(el) * d, -Math.sin(el) * d);
    this.camera.position.copy(this.position).add(offset);
    this.camera.lookAt(this.position);
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
      this.orbit.azimuth -= (e.clientX - drag.x) * 0.005;
      this.orbit.elevation = Math.max(-0.3, Math.min(1.4, this.orbit.elevation + (e.clientY - drag.y) * 0.005));
      drag = { x: e.clientX, y: e.clientY };
    });
    el.addEventListener("pointerup", () => (drag = null));
    el.addEventListener("dblclick", () => (this.orbit.azimuth = 0));
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.orbit.distance = Math.max(12, Math.min(3000, this.orbit.distance * Math.exp(e.deltaY * 0.001)));
    }, { passive: false });
  }
}
