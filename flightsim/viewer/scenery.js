// Ground fallback, airfield, runway lights, PAPI and windsock. Visual only (sky and light: sky.js).

import * as THREE from "three";
import { addGroundDetail } from "./groundDetail.js";
import { AIRFIELD, height as terrainHeight } from "./terrain.js";
import { buildC172 } from "./aircraft.js";

export function addGroundFallback(scene) {
  // Plain land just below the terrain: shows only where a tile is not built yet (distant
  // tiles stream in over the first second), so gaps read as far-off land, not sea.
  // Split into ~3 km cells: as a single 200 km quad, its depth (interpolated across
  // triangles with corners 100 km away) was imprecise enough that at some camera positions
  // it covered the runway, which sits just above the terrain.
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(200000, 200000, 64, 64),
    new THREE.MeshLambertMaterial({ color: 0x6f7f4a }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -3;
  scene.add(ground);
  return ground;
}

function runwayTexture() {
  // 4 px per metre across (30 m -> 120 px), 2 px per metre along (1000 m -> 2000 px).
  const pxW = 4, pxL = 2;
  const W = AIRFIELD.widthM * pxW, L = AIRFIELD.lengthM * pxL;
  const c = document.createElement("canvas");
  c.width = L;
  c.height = W;
  const g = c.getContext("2d");
  g.fillStyle = "#3b3d40";
  g.fillRect(0, 0, L, W);
  g.fillStyle = "#e8e8e2";
  // Edge stripes, 0.9 m wide, along both sides.
  g.fillRect(0, 0.3 * pxW, L, 0.9 * pxW);
  g.fillRect(0, W - 1.2 * pxW, L, 0.9 * pxW);
  // Centreline: 30 m stripes, 20 m gaps, 0.9 m wide.
  for (let x = 120; x < AIRFIELD.lengthM - 120; x += 50) g.fillRect(x * pxL, W / 2 - 0.45 * pxW, 30 * pxL, 0.9 * pxW);
  for (const [x0, dir, label] of [[0, 1, "09"], [AIRFIELD.lengthM, -1, "27"]]) {
    // Threshold "piano keys": 8 stripes, 30 m long, 1.8 m wide.
    for (let k = 0; k < 8; k++) {
      const y = (3 + k * 3.1 + (k >= 4 ? 2 : 0)) * pxW;
      g.fillRect(Math.min(x0 + dir * 6, x0 + dir * 36) * pxL, y, 30 * pxL, 1.8 * pxW);
    }
    // Runway number, read when approaching from that end.
    g.save();
    g.translate((x0 + dir * 55) * pxL, W / 2);
    g.rotate(dir > 0 ? Math.PI / 2 : -Math.PI / 2);
    g.font = `bold ${9 * pxW}px sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.scale(1, pxL / pxW * 2.2); // long, thin numbers as painted on runways
    g.fillText(label, 0, 0);
    g.restore();
    // Aiming point markers: 45 m long, 6 m wide, 250 m in.
    for (const y of [5, AIRFIELD.widthM - 11]) g.fillRect(Math.min(x0 + dir * 250, x0 + dir * 295) * pxL, y * pxW, 45 * pxL, 6 * pxW);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

export function addAirfield(scene) {
  const group = new THREE.Group();
  const { x, z, lengthM, widthM } = AIRFIELD;
  const flat = (w, l, material, px, pz, y) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(l, w), material);
    m.rotation.x = -Math.PI / 2;
    m.position.set(px, y, pz);
    group.add(m);
    return m;
  };
  // Runway 09/27 along x (east-west); markings texture u runs west -> east.
  // Asphalt grain close up (the runway is the main height reference in the flare).
  const grain = { strength: 0.25, tint: 0, fadeEndM: 250 };
  flat(widthM, lengthM, addGroundDetail(new THREE.MeshLambertMaterial({ map: runwayTexture() }), grain), x, z, 0.15);
  const asphalt = addGroundDetail(new THREE.MeshLambertMaterial({ color: 0x45474a }), grain);
  flat(15, lengthM, asphalt, x, z - 120, 0.12); // parallel taxiway, north side
  for (const dx of [-lengthM / 2 + 40, 0, lengthM / 2 - 40]) flat(105, 15, asphalt, x + dx, z - 67, 0.11); // connectors
  flat(80, 220, asphalt, x, z - 175, 0.12); // apron
  const hangar = new THREE.MeshLambertMaterial({ color: 0xa9adb1 });
  for (const dx of [-80, 0, 80]) {
    const h = new THREE.Mesh(new THREE.BoxGeometry(50, 12, 35), hangar);
    h.position.set(x + dx, 6, z - 245);
    group.add(h);
  }
  scene.add(group);
  return group;
}

// --- Airfield detail ------------------------------------------------------------------
//
// Taxiway centrelines and hold-short lines, apron stands with parked aircraft, a fuel
// truck, hangar doors, the access road, and a radio mast under the circuit's base turn
// (a ground feature to judge the turn by, as pilots do). Visual only.

const TAXI_YELLOW = 0xd9a92b;
const PARKED_CG_M = 1.33; // c172p CG height and pitch at rest (JSBSim, flightsim reset_on_ground)
const PARKED_PITCH_DEG = 2.4;
export const BASE_TURN_MAST = { x: AIRFIELD.x - AIRFIELD.lengthM / 2 - 1852, z: AIRFIELD.z - 1852 }; // 45 deg, 1 nm north of the 09 threshold

// Body axes (x forward, y right, z down) to the world frame (x east, y up, z south).
export function parkedMatrix(x, z, headingDeg, groundY) {
  const psi = THREE.MathUtils.degToRad(headingDeg), th = THREE.MathUtils.degToRad(PARKED_PITCH_DEG);
  const ned = (n, e, d) => new THREE.Vector3(e, -d, -n);
  const xb = ned(Math.cos(th) * Math.cos(psi), Math.cos(th) * Math.sin(psi), -Math.sin(th));
  const yb = ned(-Math.sin(psi), Math.cos(psi), 0);
  const zb = ned(Math.sin(th) * Math.cos(psi), Math.sin(th) * Math.sin(psi), Math.cos(th));
  return new THREE.Matrix4().makeBasis(xb, yb, zb).setPosition(x, groundY + PARKED_CG_M, z);
}

export function addAirfieldDetail(scene) {
  const group = new THREE.Group();
  const { x, z, lengthM } = AIRFIELD;
  const yellow = new THREE.MeshLambertMaterial({ color: TAXI_YELLOW });
  const stripe = (cx, cz, alongX, length, width, y = 0.14) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(alongX ? length : width, alongX ? width : length), yellow);
    m.rotation.x = -Math.PI / 2;
    m.position.set(cx, y, cz);
    group.add(m);
  };
  // Taxiway centreline (z - 120) and connector centrelines (z - 15 .. z - 120).
  stripe(x, z - 120, true, lengthM - 20, 0.3);
  const connectors = [-lengthM / 2 + 40, 0, lengthM / 2 - 40];
  for (const dx of connectors) {
    stripe(x + dx, z - 70, false, 98, 0.3);
    // Hold-short line 30 m from the runway centreline: two solid lines on the taxiway side,
    // two dashed lines on the runway side (FAA style).
    for (const k of [0, 1]) stripe(x + dx, z - 31 - k * 0.6, true, 15, 0.15);
    for (const k of [0, 1]) for (let d = -6; d <= 6; d += 3) stripe(x + dx + d, z - 29.2 - k * 0.6, true, 1.8, 0.15);
  }
  // Apron stands: lead-in lines and three parked 172s facing the runway.
  const stands = [-70, -35, 35, 70];
  stands.forEach((dx) => stripe(x + dx, z - 175, false, 50, 0.25));
  const regs = ["SX-ABC", "SX-KLM", "SX-PQR"];
  [-70, -35, 70].forEach((dx, i) => {
    const model = buildC172({ registration: regs[i] });
    model.update({});
    model.group.matrixAutoUpdate = false;
    model.group.matrix.copy(parkedMatrix(x + dx, z - 185, 180, 0.12));
    group.add(model.group);
  });
  // Fuel truck by the east end of the apron.
  const truck = new THREE.Group();
  const cab = new THREE.Mesh(new THREE.BoxGeometry(2.4, 2.6, 2.2), new THREE.MeshLambertMaterial({ color: 0xc8312b }));
  cab.position.set(3.2, 1.6, 0);
  const tank = new THREE.Mesh(new THREE.CylinderGeometry(1.1, 1.1, 5, 14).rotateZ(Math.PI / 2), new THREE.MeshLambertMaterial({ color: 0xdedfe0 }));
  tank.position.set(-0.6, 1.7, 0);
  const chassis = new THREE.Mesh(new THREE.BoxGeometry(8, 0.6, 2.2), new THREE.MeshLambertMaterial({ color: 0x2c2e30 }));
  chassis.position.set(0.6, 0.6, 0);
  truck.add(cab, tank, chassis);
  truck.position.set(x + 100, 0.12, z - 205);
  group.add(truck);
  // Hangar doors (dark panels on the south faces) and the access road between two hangars.
  const door = new THREE.MeshLambertMaterial({ color: 0x5d6166 });
  for (const dx of [-80, 0, 80]) {
    const d = new THREE.Mesh(new THREE.PlaneGeometry(40, 9), door);
    d.position.set(x + dx, 4.5, z - 245 + 17.6);
    group.add(d);
  }
  const road = new THREE.Mesh(new THREE.PlaneGeometry(7, 115), addGroundDetail(new THREE.MeshLambertMaterial({ color: 0x55585b }), { strength: 0.25, tint: 0, fadeEndM: 250 }));
  road.rotation.x = -Math.PI / 2;
  road.position.set(x + 40, 0.1, z - 272);
  group.add(road);
  // Radio mast under the base turn: 60 m, red and white bands, a red light on top.
  const mx = BASE_TURN_MAST.x, mz = BASE_TURN_MAST.z, my = Math.max(terrainHeight(mx, mz), 0);
  const red = new THREE.MeshLambertMaterial({ color: 0xc62f25 }), white = new THREE.MeshLambertMaterial({ color: 0xeeeeee });
  for (let k = 0; k < 6; k++) {
    const band = new THREE.Mesh(new THREE.CylinderGeometry(0.9 - k * 0.08, 1.0 - k * 0.08, 10, 8), k % 2 ? white : red);
    band.position.set(mx, my + 5 + k * 10, mz);
    group.add(band);
  }
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.9, 10, 8), new THREE.MeshBasicMaterial({ color: 0xff2a1a }));
  lamp.position.set(mx, my + 61, mz);
  group.add(lamp);
  scene.add(group);
  return group;
}

// --- A runway for lights, PAPI and windsock ------------------------------------------------
//
// {widthM, pavement: [[x, z], [x, z]], ends: [{ident, x, z, y, dx, dz}, ...]}: each end is a
// landing threshold (world x east, z south, y its elevation) with the landing direction
// (dx, dz, unit). The procedural airfield's runway 09/27 and the real ones (realAirfields.js)
// share the lights, PAPI and windsock below.

export const PROCEDURAL_RUNWAY = (() => {
  const { x, z, lengthM, widthM } = AIRFIELD;
  return {
    widthM, lengthM,
    pavement: [[x - lengthM / 2, z], [x + lengthM / 2, z]],
    ends: [{ ident: "09", x: x - lengthM / 2, z, y: 0, dx: 1, dz: 0 }, { ident: "27", x: x + lengthM / 2, z, y: 0, dx: -1, dz: 0 }],
  };
})();

// Left of the landing direction (dx, dz) in the world frame (z south): heading east, north.
const leftOf = (e) => [e.dz, -e.dx];

// --- Runway lights and PAPI -------------------------------------------------------------

// Lights are drawn as points of a fixed size on screen, so they stay visible from miles
// out (as real lights do) while the runway itself is a few pixels wide.
function lightPoints(positions, colour, sizePx) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions.flat(), 3));
  const points = new THREE.Points(geo, new THREE.PointsMaterial({ color: colour, size: sizePx, sizeAttenuation: false, fog: false }));
  points.frustumCulled = false;
  return points;
}

const LIGHT_Y = 0.6; // light height above the runway (m)

export function addRunwayLights(scene, rw = PROCEDURAL_RUNWAY) {
  const { widthM, pavement, ends } = rw;
  const white = [], green = [];
  const [[ax, az], [bx, bz]] = pavement;
  const len = Math.hypot(bx - ax, bz - az), ux = (bx - ax) / len, uz = (bz - az) / len;
  const y0 = ends[0].y, y1 = ends[1].y;
  for (let s = 0; s <= len + 0.1; s += 60) {
    const y = y0 + (y1 - y0) * (s / len) + LIGHT_Y;
    for (const side of [-1, 1]) white.push([ax + ux * s - uz * side * (widthM / 2 + 1.5), y, az + uz * s + ux * side * (widthM / 2 + 1.5)]); // edge lights
  }
  for (const e of ends) {
    const [lx, lz] = leftOf(e);
    for (let c = -widthM / 2; c <= widthM / 2 + 0.1; c += 3) green.push([e.x + e.dx * 1.0 + lx * c, e.y + LIGHT_Y, e.z + e.dz * 1.0 + lz * c]); // threshold bar
    // Approach lights: bars of 5 lights every 60 m, out to 420 m before the threshold.
    for (let d = 60; d <= 420; d += 60) for (let k = -2; k <= 2; k++) white.push([e.x - e.dx * d + lx * k, e.y + LIGHT_Y + 1, e.z - e.dz * d + lz * k]);
  }
  const group = new THREE.Group();
  group.add(lightPoints(white, 0xfff6d8, 5), lightPoints(green, 0x3cff6e, 5));
  scene.add(group);
  return group;
}

// PAPI (precision approach path indicator), FAA L-880 4-box siting for a 3 deg glide path
// (manufacturer manual following FAA AC 150/5345-28 / 150/5340-30): on the left of the
// runway at the glide path's intercept with the runway (the aim point, 250 m in), inboard
// unit 15 m from the edge, units 9 m apart. Aiming angles from the unit nearest the
// runway: glide path +30', +10', -10', -30' (3 deg 30', 3 deg 10', 2 deg 50', 2 deg 30').
// A unit shows white when seen from above its angle, red from below: on the glide path
// the two inner units are red and the two outer ones white.
export const PAPI_AIM_POINT_M = 250;
const PAPI_ANGLES_DEG = [3.5, 3 + 10 / 60, 2 + 50 / 60, 2.5];

export class Papi {
  constructor(scene, rw = PROCEDURAL_RUNWAY) {
    this.units = [];
    this.group = new THREE.Group();
    // On the left of each landing direction (runway 09 lands eastbound: its left is north).
    for (const e of rw.ends) {
      const [lx, lz] = leftOf(e);
      PAPI_ANGLES_DEG.forEach((angle, i) => {
        const off = rw.widthM / 2 + 15 + i * 9;
        const pos = new THREE.Vector3(e.x + e.dx * PAPI_AIM_POINT_M + lx * off, e.y + LIGHT_Y + 0.4, e.z + e.dz * PAPI_AIM_POINT_M + lz * off);
        const box = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.8, 0.9), new THREE.MeshLambertMaterial({ color: 0x3a3d40 }));
        box.position.copy(pos).setY(e.y + 0.4);
        box.rotation.y = Math.atan2(-e.dz, e.dx);
        const light = lightPoints([[pos.x, pos.y, pos.z]], 0xffffff, 9);
        this.group.add(box, light);
        this.units.push({ pos, dx: e.dx, dz: e.dz, angle: (angle * Math.PI) / 180, light });
      });
    }
    scene.add(this.group);
  }

  // Colours as seen from the pilot's eye (world position), not from the camera.
  update(eye) {
    for (const u of this.units) {
      const rx = eye.x - u.pos.x, rz = eye.z - u.pos.z;
      const ahead = -(rx * u.dx + rz * u.dz); // distance out on the approach side
      const side = Math.abs(rx * u.dz - rz * u.dx);
      const visible = ahead > 0 && side < ahead * 0.3; // the beam faces the approach
      u.light.visible = visible;
      if (visible) u.light.material.color.setHex(Math.atan2(eye.y - u.pos.y, ahead) > u.angle ? 0xffffff : 0xff2a1e);
    }
  }

  dispose(scene) {
    scene.remove(this.group);
  }
}

// --- Windsock ----------------------------------------------------------------------------

// Left of the runway's first landing end (runway 09's on the procedural airfield). The sock points downwind and rises with the wind: it hangs
// limp in calm air and stands straight out at 15 kt (a common windsock design point).
export class Windsock {
  constructor(scene, rw = PROCEDURAL_RUNWAY) {
    const e = rw.ends[0];
    const [lx, lz] = leftOf(e);
    this.group = new THREE.Group();
    this.group.position.set(e.x + e.dx * 70 + lx * (rw.widthM / 2 + 30), e.y, e.z + e.dz * 70 + lz * (rw.widthM / 2 + 30));
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.08, 6, 8), new THREE.MeshLambertMaterial({ color: 0xd8d8d8 }));
    pole.position.y = 3;
    this.pivot = new THREE.Group(); // at the top of the pole; the sock extends along its +x
    this.pivot.position.y = 6;
    const stripes = 5, len = 3.6, segment = len / stripes;
    for (let i = 0; i < stripes; i++) {
      const r0 = 0.45 - (0.3 * i) / stripes, r1 = 0.45 - (0.3 * (i + 1)) / stripes;
      const ring = new THREE.Mesh(
        new THREE.CylinderGeometry(r1, r0, segment, 16, 1, true),
        new THREE.MeshLambertMaterial({ color: i % 2 ? 0xffffff : 0xff6a1a, side: THREE.DoubleSide }),
      );
      ring.rotation.z = -Math.PI / 2; // cylinder axis y -> +x
      ring.position.x = segment * (i + 0.5);
      this.pivot.add(ring);
    }
    this.group.add(pole, this.pivot);
    scene.add(this.group);
    this.setWind(0, 0);
  }

  dispose(scene) {
    scene.remove(this.group);
  }

  // fromDeg: the direction the wind comes from (true); speedKt at 20 ft.
  setWind(fromDeg, speedKt) {
    const to = ((fromDeg + 180) * Math.PI) / 180; // downwind, clockwise from north
    this.pivot.rotation.set(0, 0, 0);
    // World: x east, z south. Heading `to` from north: direction (sin, 0, -cos); the sock's +x
    // turns to it by a rotation about +y of (pi/2 - to).
    this.pivot.rotation.y = Math.PI / 2 - to;
    const lift = Math.min(1, speedKt / 15);
    this.pivot.rotation.z = -(Math.PI / 2) * (1 - lift) * 0.92; // droop toward the pole when calm
  }
}
