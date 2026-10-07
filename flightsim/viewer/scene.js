// Three.js scene: flat-earth local frame, simple C172 model, trail, target line, chase camera.
//
// World frame: x = east, y = up, z = south (three.js is y-up, right-handed), origin at
// latitude 0, longitude 0 at sea level, where every task starts (so the procedural
// scenery and its airfield are always in the same place). Over the tens of km a flight
// covers, the flat-earth approximation is far below anything visible.

import * as THREE from "three";
import { DEFAULT_ORBIT, RECENTRE_S, recentreView } from "./view.js";
import { Papi, Windsock, addAirfield, addAirfieldDetail, addGroundFallback, addRunwayLights } from "./scenery.js";
import { SkyController, TIMES } from "./sky.js";
import { CloudField } from "./clouds.js";
import { RoadNetwork } from "./roads.js";
import { QUALITY, Terrain, WATER_LEVEL_M, height as terrainHeight } from "./terrain.js";
import { groundDetailStrength } from "./groundDetail.js";
import { buildC172 } from "./aircraft.js";
import { buildPattern } from "./pattern.js";

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


// Top view of the C172 (span 10.9 m, length 8.2 m), nose toward -z, as a shadow.
function buildShadow() {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d"), m = 256 / 12; // 12 m across
  g.fillStyle = "#000";
  const rect = (cx, cy, w, h) => g.fillRect(128 + (cx - w / 2) * m, 128 + (cy - h / 2) * m, w * m, h * m);
  g.beginPath();
  g.ellipse(128, 128 + 0.4 * m, 0.6 * m, 4.1 * m, 0, 0, Math.PI * 2); // fuselage, nose up (toward -z)
  g.fill();
  rect(0, -0.9, 10.9, 1.5); // wing
  rect(0, 3.6, 3.4, 1.1); // tailplane
  const tex = new THREE.CanvasTexture(c);
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(12, 12),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.42, depthWrite: false, color: 0x000000, alphaTest: 0.01 }),
  );
  plane.rotation.x = -Math.PI / 2;
  const group = new THREE.Group();
  group.add(plane);
  group.visible = false;
  return group;
}

export class FlightScene {
  constructor(container, quality = "high") {
    this.container = container;
    this.quality = quality;
    // Logarithmic depth: from 0.5 m to 100+ km without distant surfaces flickering.
    this.renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, QUALITY[quality].pixelRatio));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping; // the physical sky is HDR
    this.renderer.toneMappingExposure = 0.55;
    container.prepend(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.skyLight = new SkyController(this.scene, this.renderer);
    this.sunDir = this.skyLight.sunDir; // updated in place with the time of day
    this.clouds = new CloudField(this.scene);
    this.roads = new RoadNetwork(this.scene);
    addGroundFallback(this.scene);
    addAirfield(this.scene);
    addAirfieldDetail(this.scene);
    addRunwayLights(this.scene);
    this.papi = new Papi(this.scene);
    this.windsock = new Windsock(this.scene);
    this.terrain = new Terrain(this.scene, quality);
    this._applyQuality(quality);

    this.model = buildC172();
    this.aircraft = this.model.group;
    this.aircraft.matrixAutoUpdate = false;
    this.scene.add(this.aircraft);
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

    // Approach: magenta gates along the glide path (setApproach).
    this.glidePath = new THREE.Group();
    this.glidePath.visible = false;
    this.scene.add(this.glidePath);
    this.approach = null;
    // Circuit: the traffic pattern legs (setPattern).
    this.pattern = new THREE.Group();
    this.scene.add(this.pattern);

    this.shadow = buildShadow();
    this.scene.add(this.shadow);

    this.camera = new THREE.PerspectiveCamera(60, 1, 0.5, 120000);
    this.orbit = { ...DEFAULT_ORBIT };
    this.recentre = null;
    this._bindPointer();
    this.reset();
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
  }

  reset() {
    this.origin = { lat: 0, lon: 0 }; // fixed, see the header
    this.lastT = null;
    this.model.update({}); // surfaces neutral, propeller still
    this.trailCount = 0;
    this.trailGeo.setDrawRange(0, 0);
    this.targets = null;
    this.targetLine.visible = false;
    this.position = new THREE.Vector3(0, 1500, 0);
    this.heading = 0;
    // Until the first frame: wings level, nose north. (The model is built in body axes,
    // so an identity matrix would show it rolled 90 degrees in this y-up world.)
    this.aircraft.matrix
      .makeBasis(nedToWorld(1, 0, 0), nedToWorld(0, 1, 0), nedToWorld(0, 0, 1))
      .setPosition(this.position);
  }

  clearTrail() {
    this.trailCount = 0;
    this.trailGeo.setDrawRange(0, 0);
  }

  setTargets(targets) {
    this.targets = targets;
  }

  // Approach task geometry (stream hello "approach"), or null: draws the glide path from
  // the aim point back 5 nm along the extended centreline.
  // Viewer conditions: time of day, visibility, cloud amount and cloud layout seed.
  setVisual({ time_of_day = "afternoon", visibility = "normal", clouds = "few", cloud_seed = 0 } = {}) {
    this.skyLight.setTime(time_of_day);
    this.skyLight.setVisibility(visibility);
    const tint = new THREE.Color(TIMES[this.skyLight.time].sun).lerp(new THREE.Color(0xffffff), 0.55);
    this.clouds.setTint(tint);
    this.clouds.set(clouds, cloud_seed);
  }

  // Quality preset (terrain.js QUALITY): low / medium / high.
  setQuality(quality) {
    if (!(quality in QUALITY) || quality === this.quality) return;
    this.quality = quality;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, QUALITY[quality].pixelRatio));
    this.terrain.setQuality(quality);
    this._applyQuality(quality);
    this.resize();
  }

  _applyQuality(quality) {
    const q = QUALITY[quality];
    groundDetailStrength.value = q.groundDetail;
    this.skyLight.setVisibility(this.skyLight.visibility, q.fog);
  }

  setApproach(a) {
    this.approach = a;
    const w = a?.wind;
    this.windsock.setWind(w ? w.from_deg : 0, w ? w.u20_mps * 1.943844 : 0);
    this.glidePath.visible = Boolean(a);
    this.glidePath.clear();
    if (!a) return;
    const h = (a.heading_deg * Math.PI) / 180, tan = Math.tan((a.glide_path_deg * Math.PI) / 180);
    const at = (along) => {
      const n = a.threshold_north_m + along * Math.cos(h), e = a.threshold_east_m + along * Math.sin(h);
      return nedToWorld(n, e, -(a.elevation_m + Math.max(0, a.aim_point_m - along) * tan));
    };
    // Gates (40 m wide, 24 m tall frames, centred on the glide path) every 400 m from 200 m
    // before the threshold out to 5 nm: fly through their centres.
    // Each gate is one flat frame (a rectangle with a rectangular hole, 1.5 m bars): no
    // overlapping parts, so the translucent corners join cleanly.
    const material = new THREE.MeshBasicMaterial({ color: 0xd23cc8, transparent: true, opacity: 0.8, side: THREE.DoubleSide });
    const rect = (path, hw, hh) => path.moveTo(-hw, -hh).lineTo(hw, -hh).lineTo(hw, hh).lineTo(-hw, hh).lineTo(-hw, -hh);
    const frame = new THREE.Shape();
    rect(frame, 20.75, 12.75);
    const hole = new THREE.Path();
    rect(hole, 19.25, 11.25);
    frame.holes.push(hole);
    const gateGeometry = new THREE.ShapeGeometry(frame);
    for (let along = -200; along > a.aim_point_m - 9260; along -= 400) {
      const gate = new THREE.Mesh(gateGeometry, material);
      gate.position.copy(at(along));
      gate.rotation.y = -h; // face along the approach
      this.glidePath.add(gate);
    }
    this.targetLine.visible = false;
  }

  // Circuit: draw the pattern legs (null clears them). Needs the runway from setApproach.
  setPattern(p) {
    this.pattern.clear();
    if (p && this.approach) this.pattern.add(buildPattern(this.approach, p));
  }

  // The aircraft's shadow: its outline cast along the sun onto the ground below, fading
  // out with height (the strongest height cue close to the ground).
  updateShadow() {
    const p = this.position, s = this.sunDir;
    const ground = Math.max(terrainHeight(p.x, p.z), WATER_LEVEL_M);
    const agl = p.y - ground;
    this.shadow.visible = agl < 200;
    if (!this.shadow.visible) return;
    const t = (p.y - ground) / s.y;
    this.shadow.position.set(p.x - s.x * t, ground + 0.25, p.z - s.z * t);
    this.shadow.rotation.y = -(this.heading ?? 0);
    this.shadow.children[0].material.opacity = 0.42 * Math.max(0, 1 - agl / 200);
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
    // Control surfaces and propeller; dt from the frame times (0 after a jump, e.g. a seek).
    const dt = this.lastT === null || row.t_s < this.lastT || row.t_s - this.lastT > 1 ? 0 : row.t_s - this.lastT;
    this.lastT = row.t_s;
    this.model.update(row, dt);

    const pos = this.trailGeo.attributes.position;
    if (this.trailCount === TRAIL_POINTS) {
      pos.array.copyWithin(0, 3);
      this.trailCount--;
    }
    pos.setXYZ(this.trailCount++, this.position.x, this.position.y, this.position.z);
    pos.needsUpdate = true;
    this.trailGeo.setDrawRange(0, this.trailCount);

    this.updateShadow();
    if (this.targets && !this.approach) {
      const h = this.targets.heading_rad;
      const start = new THREE.Vector3(this.position.x, this.targets.alt_msl_m, this.position.z);
      const end = start.clone().add(nedToWorld(Math.cos(h), Math.sin(h), 0).multiplyScalar(3000));
      this.targetLine.geometry.setFromPoints([start, end]);
      this.targetLine.visible = true;
    }
  }

  // Horizontal screen position (0..1 across the view) of the aircraft's straight-ahead
  // direction from the pilot's eye, or null when not in the cockpit view or out of sight.
  boresightX() {
    if (this.view !== "cockpit") return null;
    const fwd = new THREE.Vector3(1000, 0, 0).add(EYE_BODY).applyMatrix4(this.aircraft.matrix);
    const ndc = fwd.project(this.camera);
    if (ndc.z > 1 || Math.abs(ndc.x) > 0.97) return null;
    return (ndc.x + 1) / 2;
  }

  setView(view) {
    this.view = view;
    const inside = view === "cockpit";
    this.aircraft.visible = !inside; // plain view from the pilot's eye: no aircraft parts drawn
    this.camera.near = inside ? 0.05 : 0.5;
    this.camera.fov = inside ? 70 : 60;
    this.camera.updateProjectionMatrix();
    this.head = { yaw: 0, pitch: 0 };
  }

  // Back to the default view: chase camera behind at its usual height and distance,
  // cockpit head straight ahead (after dragging to look around or zooming), as a short
  // eased move (view.js).
  resetView() {
    this.recentre = { t0: performance.now(), from: { orbit: { ...this.orbit }, head: { ...this.head } } };
  }

  _stepRecentre() {
    if (!this.recentre) return;
    const k = Math.min(1, (performance.now() - this.recentre.t0) / (RECENTRE_S * 1000));
    ({ orbit: this.orbit, head: this.head } = recentreView(this.recentre.from, k));
    if (k >= 1) this.recentre = null;
  }

  render() {
    this._stepRecentre();
    this.papi.update(new THREE.Vector3().copy(EYE_BODY).applyMatrix4(this.aircraft.matrix)); // as the pilot sees them
    if (this.view === "cockpit") {
      // Eye fixed in the aircraft; the camera rotates with it, plus the pilot's head turn.
      this.camera.position.copy(EYE_BODY).applyMatrix4(this.aircraft.matrix);
      const rot = new THREE.Matrix4().extractRotation(this.aircraft.matrix).multiply(BODY_FROM_CAMERA);
      const head = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(this.head.pitch, this.head.yaw, 0, "YXZ"));
      this.camera.quaternion.setFromRotationMatrix(rot.multiply(head));
      this.terrain.update(this.camera.position.x, this.camera.position.z);
      this.clouds.update(this.camera.position.x, this.camera.position.z);
      this.roads.update(this.camera.position.x, this.camera.position.z);
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
    this.clouds.update(this.camera.position.x, this.camera.position.z);
    this.roads.update(this.camera.position.x, this.camera.position.z);
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
      this.recentre = null; // the pilot takes the view back
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
    el.addEventListener("dblclick", () => this.resetView());
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      if (this.view === "cockpit") return;
      this.recentre = null;
      this.orbit.distance = Math.max(8, Math.min(3000, this.orbit.distance * Math.exp(e.deltaY * 0.001)));
    }, { passive: false });
  }
}
