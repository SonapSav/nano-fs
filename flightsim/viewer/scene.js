// Three.js scene: flat-earth local frame, simple C172 model, trail, target line, chase camera.
//
// World frame: x = east, y = up, z = south (three.js is y-up, right-handed): the flight's
// map (geo.js; metres north and east of the world's origin, where the procedural scenery
// and its airfield are, or a real-world region's origin: world.js) and height above mean
// sea level. Headings and velocities are true
// (north); on the map they turn by the grid convergence (zero on the origin's meridian
// and at the equator).

import * as THREE from "three";
import { DEFAULT_ORBIT, RECENTRE_S, recentreView } from "./view.js";
import { Geodesy } from "./geo.js";
import { Papi, Windsock, addAirfield, addAirfieldDetail, addGroundFallback, addRunwayLights } from "./scenery.js";
import { SkyController, TIMES } from "./sky.js";
import { CloudField } from "./clouds.js";
import { RoadNetwork } from "./roads.js";
import { QUALITY, Terrain, waterUniforms } from "./terrain.js";
import { LIFT_M as RUNWAY_LIFT_M, RealAirfields } from "./realAirfields.js";
import { Landmarks } from "./landmarks.js";
import { Bridges } from "./bridges.js";
import { ImageryClip } from "./imageryClip.js";
import { SunShadows } from "./sunShadows.js";
import "./haze.js"; // height-aware haze in every material (patches three's fog chunks)
import { world } from "./world.js";
import { groundDetailStrength } from "./groundDetail.js";
import { buildC172 } from "./aircraft.js";
import { buildPattern } from "./pattern.js";
import { ASPECT, MOUNT_BODY_M, cameraAxes, vfov } from "./camera.js";

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


// The aircraft's shadow: its real shape. The model's solid parts are merged once (in body
// axes). Every frame the merged shape is flattened onto the ground plane under the
// aircraft along the sun (a parallel projection, see updateShadow), so the outline follows
// the attitude, the heading and the sun's direction. The flattened shape is drawn white
// into a small mask image from straight above, and the mask darkens a patch of ground:
// overlapping parts (wing over fuselage, both wing skins) darken once. The mask is fitted
// to the shadow's extent each frame (about 1 cm per pixel) and drawn antialiased, so the
// outline stays clean seen from the cockpit, a metre or two away.
const SHADOW_MASK_PX = 1024;

function buildShadow(model) {
  model.group.updateMatrixWorld(true);
  const toBody = new THREE.Matrix4().copy(model.group.matrixWorld).invert();
  const m = new THREE.Matrix4(), v = new THREE.Vector3(), pos = [];
  const visible = (o) => {
    for (let n = o; n && n !== model.group; n = n.parent) if (!n.visible || n.userData.noShadow) return false;
    return true;
  };
  model.group.traverse((o) => {
    if (!o.isMesh || o.isInstancedMesh || o.material.transparent || !visible(o)) return; // no glass, glows, propeller disc
    m.multiplyMatrices(toBody, o.matrixWorld);
    const g = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry;
    const a = g.attributes.position;
    for (let i = 0; i < a.count; i++) {
      v.fromBufferAttribute(a, i).applyMatrix4(m);
      pos.push(v.x, v.y, v.z);
    }
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geometry.computeBoundingBox();
  const b = geometry.boundingBox; // corners in body axes, projected each frame to fit the mask
  const corners = [];
  for (const x of [b.min.x, b.max.x]) for (const y of [b.min.y, b.max.y]) for (const z of [b.min.z, b.max.z]) corners.push(new THREE.Vector3(x, y, z));
  const flat = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }));
  flat.matrixAutoUpdate = false;
  flat.frustumCulled = false; // its matrix flattens it: the bounding sphere does not apply
  const maskScene = new THREE.Scene();
  maskScene.add(flat);
  const target = new THREE.WebGLRenderTarget(SHADOW_MASK_PX, SHADOW_MASK_PX, { depthBuffer: false, samples: 4 });
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
  camera.up.set(0, 0, -1); // north up in the mask, as on the ground patch
  // Drawn with the opaque things, after the ground (renderOrder 1) and before the aircraft
  // (its group's renderOrder 2): blended onto what is already there, it darkens the ground
  // and never the aircraft's own tyres, whatever their height above the patch.
  const patch = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: 0x000000, alphaMap: target.texture, transparent: false, blending: THREE.CustomBlending, opacity: 0.5, depthWrite: false }),
  );
  patch.renderOrder = 1;
  patch.visible = false;
  return { flat, maskScene, target, camera, patch, corners, corner: new THREE.Vector3() };
}

export class FlightScene {
  constructor(container, quality = "high", { antialias = true } = {}) {
    this.container = container;
    this.quality = quality;
    this.antialias = antialias; // the viewer's setting (the performance test may switch it for a while)
    // Logarithmic depth: from 0.5 m to 100+ km without distant surfaces flickering.
    this.renderer = new THREE.WebGLRenderer({ antialias, logarithmicDepthBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, QUALITY[quality].pixelRatio));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping; // the physical sky is HDR
    this.renderer.toneMappingExposure = 0.55;
    this.renderer.shadowMap.enabled = true; // a region's sun shadows (sunShadows.js)
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    container.prepend(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.skyLight = new SkyController(this.scene, this.renderer);
    this.sunDir = this.skyLight.sunDir; // updated in place with the time of day
    // A real-world region's water reflects the haze and the sun (both updated in place).
    waterUniforms.waterSun.value = this.sunDir;
    waterUniforms.waterSky.value = this.scene.fog.color;
    this.clouds = new CloudField(this.scene);
    this.fallback = addGroundFallback(this.scene);
    // The procedural world's airfield, roads and lights (hidden over a real-world region,
    // which has its own runways: setWorld).
    this.procedural = new THREE.Group();
    this.scene.add(this.procedural);
    this.roads = new RoadNetwork(this.procedural);
    addAirfield(this.procedural);
    addAirfieldDetail(this.procedural);
    addRunwayLights(this.procedural);
    this.proceduralPapi = this.papi = new Papi(this.procedural);
    this.proceduralWindsock = this.windsock = new Windsock(this.procedural);
    this.realAirfields = new RealAirfields(this.scene);
    this.landmarks = new Landmarks(this.scene);
    this.bridges = new Bridges(this.scene);
    this.imageryClip = new ImageryClip(this.renderer); // a region's 1 m and 4 m imagery around the camera
    this.wind = [0, 0]; // the windsock's wind (from deg, kt), kept across a world change
    this.terrain = new Terrain(this.scene, quality);
    this.sunShadows = new SunShadows(this.renderer, this.skyLight.sun);
    this.terrain.onChange = () => this.sunShadows.invalidate();
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

    this.shadow = buildShadow(this.model);
    this.aircraft.renderOrder = 2; // after the shadow patch (buildShadow)
    this.scene.add(this.shadow.patch);
    this.shadowFlatten = new THREE.Matrix4();

    this.camera = new THREE.PerspectiveCamera(60, 1, 0.5, 120000);
    this.orbit = { ...DEFAULT_ORBIT };
    this.recentre = null;
    this._bindPointer();
    this.reset();
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
  }

  reset() {
    this.geodesy ??= new Geodesy(null); // set per flight (setGeodesy)
    this.convergence = 0;
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
    this.sunShadows?.invalidate(); // the sun moved
    this.skyLight.setVisibility(visibility);
    this.groundUnder = null; // the shadow follows the new sun (_followGround)
    this._environment(); // reflections of this sky
    const tint = new THREE.Color(TIMES[this.skyLight.time].sun).lerp(new THREE.Color(0xffffff), 0.55);
    this.clouds.setTint(tint);
    this.clouds.set(clouds, cloud_seed);
  }

  // A new renderer with other context options (the performance test, bench.js): edge
  // smoothing and logarithmic depth can only be chosen when the WebGL context is created.
  rebuildRenderer({ antialias = true, logDepth = true } = {}) {
    const old = this.renderer;
    const r = new THREE.WebGLRenderer({ antialias, logarithmicDepthBuffer: logDepth });
    r.setPixelRatio(old.getPixelRatio());
    r.toneMapping = old.toneMapping;
    r.toneMappingExposure = old.toneMappingExposure;
    r.shadowMap.enabled = true;
    r.shadowMap.type = old.shadowMap.type;
    old.domElement.replaceWith(r.domElement);
    old.dispose();
    old.forceContextLoss();
    this.renderer = r;
    this.skyLight.renderer = r;
    this.imageryClip.setRenderer(r); // its textures lived in the old context
    this.sunShadows.renderer = r;
    this.sunShadows.set(this.sunShadows.enabled, QUALITY[this.quality].shadowMap); // its map too
    this.shadowMaskDirty = true; // the mask texture lived in the old context
    this._environment();
    this._bindPointer();
    this.resize();
  }

  // Edge smoothing (the viewer's setting): a new renderer when it changes.
  setAntialias(on) {
    if (on === this.antialias) return;
    this.antialias = on;
    this.rebuildRenderer({ antialias: on });
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
    this.sunShadows.set(Boolean(world.real), q.shadowMap);
    this.skyLight.setVisibility(this.skyLight.visibility, q.fog);
  }

  setApproach(a) {
    this.approach = a;
    const w = a?.wind;
    this.setWindsock(w ? w.from_deg : 0, w ? w.u20_mps * 1.943844 : 0);
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

  // The windsock's wind: from (deg true), speed at 20 ft (kt).
  setWindsock(fromDeg, kt) {
    this.wind = [fromDeg, kt];
    this.windsock.setWind(fromDeg, kt);
  }

  // The flight's world (world.js): procedural, or a real-world region with its terrain and
  // runways. `near` (world x, z): the runway nearest it gets the PAPI and windsock (the
  // task's runway; default the origin's).
  setWorld(w, near = { x: 0, z: 0 }) {
    const real = Boolean(w.real && w.airfields);
    this.procedural.visible = !real;
    this.fallback.material.color.setHex(real ? 0xcdb58c : 0x6f7f4a); // beyond a region: desert sand
    if (this.papi !== this.proceduralPapi) this.papi.dispose(this.scene);
    if (this.windsock !== this.proceduralWindsock) this.windsock.dispose(this.scene);
    this.papi = this.proceduralPapi;
    this.windsock = this.proceduralWindsock;
    this.realAirfields.build(real ? w.airfields : null);
    this.landmarks.build(real ? w.landmarks : null);
    this.bridges.build(real ? w.bridges : null);
    this.imageryClip.set(w.scenery, real ? w.hires : null);
    this.sunShadows.set(real, QUALITY[this.quality].shadowMap);
    this._environment();
    const home = real ? this.realAirfields.nearest(near.x, near.z) : null;
    if (home) {
      this.papi = new Papi(this.scene, home);
      this.windsock = new Windsock(this.scene, home);
      const sock = this.realAirfields.windsockNear(w.airfields, home); // OSM's windsock, where mapped
      if (sock) this.windsock.group.position.set(sock.x, home.ends[0].y, sock.z);
      this.realAirfields.furnish(w.airfields, home);
    }
    this.windsock.setWind(...this.wind);
    this.terrain.setWorld(w);
  }

  // Circuit: draw the pattern legs (null clears them). Needs the runway from setApproach.
  setPattern(p) {
    this.pattern.clear();
    if (p && this.approach) this.pattern.add(buildPattern(this.approach, p));
  }

  // The aircraft's shadow: its outline cast along the sun onto the ground below, fading
  // out with height (the strongest height cue close to the ground).
  updateShadow() {
    const p = this.position, s = this.sunDir, sh = this.shadow;
    // The ground where the sun's ray through the aircraft meets it (two steps on hills).
    let ground = world.groundAt(p.x, p.z);
    let t = (p.y - ground) / s.y;
    ground = world.groundAt(p.x - s.x * t, p.z - s.z * t);
    const agl = p.y - ground;
    sh.patch.visible = agl < 200 && s.y > 0.02;
    if (!sh.patch.visible) return;
    // Flatten onto the plane y = h along the sun: (x, y, z) -> (x - sx/sy (y - h), h, z - sz/sy (y - h)).
    // Just above the highest paved marking (hold bars, 0.08 m; runways 0.03, taxiways
    // 0.05 m) and no higher: the patch darkens whatever lies under it, so higher it would
    // cut across the tyres (bottoms at the runway, centres 0.22 m up).
    const h = ground + 0.09, kx = s.x / s.y, kz = s.z / s.y;
    this.shadowFlatten.set(1, -kx, 0, kx * h, 0, 0, 0, h, 0, -kz, 1, kz * h, 0, 0, 0, 1);
    sh.flat.matrix.multiplyMatrices(this.shadowFlatten, this.aircraft.matrix);
    sh.flat.matrixWorld.copy(sh.flat.matrix);
    // Patch and mask camera: the shadow's extent on the ground (the model's bounding box
    // flattened the same way), plus a small margin, at most 80 m across.
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    for (const c of sh.corners) {
      const q = sh.corner.copy(c).applyMatrix4(sh.flat.matrix);
      x0 = Math.min(x0, q.x); x1 = Math.max(x1, q.x); z0 = Math.min(z0, q.z); z1 = Math.max(z1, q.z);
    }
    const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
    const w = Math.min(80, x1 - x0 + 0.5), d = Math.min(80, z1 - z0 + 0.5);
    sh.patch.position.set(cx, h, cz);
    sh.patch.scale.set(w, 1, d);
    sh.camera.left = -w / 2;
    sh.camera.right = w / 2;
    sh.camera.top = d / 2;
    sh.camera.bottom = -d / 2;
    sh.camera.position.set(cx, h + 100, cz);
    sh.camera.lookAt(cx, h, cz);
    sh.camera.updateProjectionMatrix();
    sh.patch.material.opacity = 0.5 * Math.max(0, 1 - agl / 200);
    this.shadowMaskDirty = true;
  }

  // Draw the shadow mask (before the frame; only when the aircraft or the sun moved).
  _renderShadowMask() {
    if (!this.shadowMaskDirty || !this.shadow.patch.visible) return;
    this.shadowMaskDirty = false;
    const r = this.renderer, sh = this.shadow;
    const clear = r.getClearColor(new THREE.Color()), alpha = r.getClearAlpha();
    r.setRenderTarget(sh.target);
    r.setClearColor(0x000000, 1);
    r.clear(true, false, false);
    r.render(sh.maskScene, sh.camera);
    r.setRenderTarget(null);
    r.setClearColor(clear, alpha);
  }

  // The flight's geodesy (the stream's `world`; null: the original sphere, older flights).
  setGeodesy(world) {
    this.geodesy = new Geodesy(world);
  }

  update(row) {
    const [n, e] = this.geodesy.toMap(row.lat_rad, row.lon_rad);
    this.position = nedToWorld(n, e, -row.alt_msl_m);
    this.convergence = this.geodesy.convergence(row.lat_rad, row.lon_rad);

    // Body axes in NED from ZYX Euler angles (heading on the map), mapped to world. The
    // model is built in body axes, so these three vectors are its basis.
    const psiMap = row.psi_rad - this.convergence;
    const [cf, sf, ct, st, cp, sp] = [row.phi_rad, row.theta_rad, psiMap].flatMap((a) => [Math.cos(a), Math.sin(a)]);
    const xb = nedToWorld(ct * cp, ct * sp, -st);
    const yb = nedToWorld(sf * st * cp - cf * sp, sf * st * sp + cf * cp, sf * ct);
    const zb = nedToWorld(cf * st * cp + sf * sp, cf * st * sp - sf * cp, cf * ct);
    this.aircraft.matrix.makeBasis(xb, yb, zb).setPosition(this.position);
    this.heading = psiMap;
    // Control surfaces and propeller; dt from the frame times (0 after a jump, e.g. a seek).
    const dt = this.lastT === null || row.t_s < this.lastT || row.t_s - this.lastT > 1 ? 0 : row.t_s - this.lastT;
    this.lastT = row.t_s;
    this.model.update(row, dt);
    this.groundUnder = null; // gear and shadow placed again on the next drawing (_followGround)

    // Trail: a point every 1/30 s of flight time at most (the view is drawn at the screen's
    // rate, between frames), so it keeps covering the same stretch of flight.
    const pos = this.trailGeo.attributes.position;
    if (!this.trailCount || row.t_s < this.trailT || row.t_s - this.trailT >= 1 / 30 - 1e-9) {
      if (this.trailCount === TRAIL_POINTS) {
        pos.array.copyWithin(0, 3);
        this.trailCount--;
      }
      pos.setXYZ(this.trailCount++, this.position.x, this.position.y, this.position.z);
      pos.needsUpdate = true;
      this.trailGeo.setDrawRange(0, this.trailCount);
      this.trailT = row.t_s;
    }

    if (this.targets && !this.approach) {
      const h = this.targets.heading_rad - this.convergence;
      const start = new THREE.Vector3(this.position.x, this.targets.alt_msl_m, this.position.z);
      const end = start.clone().add(nedToWorld(Math.cos(h), Math.sin(h), 0).multiplyScalar(3000));
      this.targetLine.geometry.setFromPoints([start, end]);
      this.targetLine.visible = true;
    }
  }

  // The belly camera's picture (camera.js; gimbal: {pan, tilt, hfov}), drawn into the
  // bottom-left corner of this view's canvas before the main view is drawn over it, so it
  // can be copied out in the same frame (copyCameraTo). widthPx: the picture's width in
  // canvas pixels (16:9), at most the canvas's. Returns the canvas region it occupies.
  renderCamera(gimbal, widthPx) {
    this._followGround();
    const r = this.renderer, canvas = r.domElement, pr = r.getPixelRatio();
    let w = Math.min(widthPx, canvas.width), h = Math.round(w / ASPECT);
    if (h > canvas.height) {
      h = canvas.height;
      w = Math.round(h * ASPECT);
    }
    if (!w || !h) return null;
    this.camCamera ??= new THREE.PerspectiveCamera(60, ASPECT, 0.05, 120000);
    const cam = this.camCamera;
    cam.fov = vfov(gimbal.hfov) / (Math.PI / 180);
    cam.aspect = ASPECT;
    cam.updateProjectionMatrix();
    cam.position.set(...MOUNT_BODY_M).applyMatrix4(this.aircraft.matrix);
    const { forward, up, right } = cameraAxes(this.heading ?? 0, gimbal.pan, gimbal.tilt);
    const back = nedToWorld(...forward).negate();
    cam.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(nedToWorld(...right), nedToWorld(...up), back));
    this._renderShadowMask();
    const wasVisible = this.aircraft.visible;
    this.aircraft.visible = true; // the belly camera sees the gear, whatever the main view
    this.model.cameraPod.visible = false;
    const size = r.getSize(new THREE.Vector2());
    r.setViewport(0, 0, w / pr, h / pr);
    r.setScissor(0, 0, w / pr, h / pr);
    r.setScissorTest(true);
    const shadows = r.shadowMap.autoUpdate;
    r.shadowMap.autoUpdate = false; // the landmark shadow map of the main view serves here too
    r.render(this.scene, cam);
    r.shadowMap.autoUpdate = shadows;
    r.setScissorTest(false);
    r.setViewport(0, 0, size.x, size.y);
    this.aircraft.visible = wasVisible;
    this.model.cameraPod.visible = true;
    return { x: 0, y: canvas.height - h, w, h };
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

  // Gear and shadow on the ground under the aircraft: after each frame, and again whenever
  // that ground changes without a new frame (a region's height tile arriving after the
  // first frame, e.g. the preview shown before Play).
  _followGround() {
    if (!this.position) return;
    const g = world.groundAt(this.position.x, this.position.z);
    if (g === this.groundUnder) return;
    this.groundUnder = g;
    // Tyres on the drawn runway (a few cm above the physics' ground; on grass they float
    // as much, unseen).
    this.model.settle(this.aircraft.matrix, (x, z) => world.groundAt(x, z) + RUNWAY_LIFT_M);
    this.updateShadow();
  }

  // The sky's environment map for the landmarks and bridges.
  _environment() {
    this.landmarks.setEnvironment(this.renderer, this.skyLight.sky);
    this.bridges.setEnvironment(this.landmarks.envTarget?.texture);
  }

  render() {
    this._followGround();
    this.bridges.update(this.camera.position);
    this.imageryClip.update(this.camera.position.x, this.camera.position.z); // last frame's camera
    waterUniforms.waterTime.value = (performance.now() / 1000) % 10000;
    this._stepRecentre();
    this._renderShadowMask();
    this.papi.update(new THREE.Vector3().copy(EYE_BODY).applyMatrix4(this.aircraft.matrix)); // as the pilot sees them
    if (this.view === "cockpit") {
      // Eye fixed in the aircraft; the camera rotates with it, plus the pilot's head turn.
      this.camera.position.copy(EYE_BODY).applyMatrix4(this.aircraft.matrix);
      const rot = new THREE.Matrix4().extractRotation(this.aircraft.matrix).multiply(BODY_FROM_CAMERA);
      const head = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(this.head.pitch, this.head.yaw, 0, "YXZ"));
      this.camera.quaternion.setFromRotationMatrix(rot.multiply(head));
      this.terrain.update(this.camera.position.x, this.camera.position.z);
      this.clouds.update(this.camera.position.x, this.camera.position.z);
      if (this.procedural.visible) this.roads.update(this.camera.position.x, this.camera.position.z);
      this.sunShadows.update(this.camera, world.groundAt(this.camera.position.x, this.camera.position.z), this.sunDir);
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
    if (this.procedural.visible) this.roads.update(this.camera.position.x, this.camera.position.z);
    this.sunShadows.update(this.camera, world.groundAt(this.camera.position.x, this.camera.position.z), this.sunDir);
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
