// Sky, sun, water and the airfield. Visual only (see terrain.js for the height model).

import * as THREE from "three";
import { Sky } from "three/addons/objects/Sky.js";
import { AIRFIELD } from "./terrain.js";

const SUN_ELEVATION_DEG = 38;
const SUN_AZIMUTH_DEG = 215; // from north, clockwise (south-west, afternoon)
export const HAZE = 0xc9d6e2;

export function addSky(scene) {
  const sky = new Sky();
  sky.scale.setScalar(450000);
  const u = sky.material.uniforms;
  u.turbidity.value = 4;
  u.rayleigh.value = 1.2;
  u.mieCoefficient.value = 0.004;
  u.mieDirectionalG.value = 0.8;
  const phi = THREE.MathUtils.degToRad(90 - SUN_ELEVATION_DEG);
  const az = THREE.MathUtils.degToRad(SUN_AZIMUTH_DEG);
  // World frame: x = east, y = up, z = south. Azimuth from north (-z) towards east (+x).
  const sunDir = new THREE.Vector3(Math.sin(phi) * Math.sin(az), Math.cos(phi), -Math.sin(phi) * Math.cos(az));
  u.sunPosition.value.copy(sunDir);
  scene.add(sky);

  const sun = new THREE.DirectionalLight(0xfff4e0, 2.4);
  sun.position.copy(sunDir).multiplyScalar(10000);
  scene.add(sun, sun.target);
  scene.add(new THREE.HemisphereLight(0xcfe3f5, 0x5a6844, 1.1));
  scene.fog = new THREE.Fog(HAZE, 9000, 21000);
  return { sky, sun, sunDir };
}

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
  flat(widthM, lengthM, new THREE.MeshLambertMaterial({ map: runwayTexture() }), x, z, 0.15);
  const asphalt = new THREE.MeshLambertMaterial({ color: 0x45474a });
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

export function addRunwayLights(scene) {
  const { x, z, lengthM, widthM } = AIRFIELD;
  const white = [], green = [];
  for (let dx = -lengthM / 2; dx <= lengthM / 2 + 0.1; dx += 60) {
    for (const side of [-1, 1]) white.push([x + dx, LIGHT_Y, z + side * (widthM / 2 + 1.5)]); // edge lights
  }
  for (const end of [-1, 1]) {
    const tx = x + end * lengthM / 2;
    for (let dz = -widthM / 2; dz <= widthM / 2 + 0.1; dz += 3) green.push([tx - end * 1.0, LIGHT_Y, z + dz]); // threshold bar
    // Approach lights: bars of 5 lights every 60 m, out to 420 m before the threshold.
    for (let d = 60; d <= 420; d += 60) for (let k = -2; k <= 2; k++) white.push([tx + end * d, LIGHT_Y + 1, z + k * 1.0]);
  }
  scene.add(lightPoints(white, 0xfff6d8, 5), lightPoints(green, 0x3cff6e, 5));
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
  constructor(scene) {
    const { x, z, lengthM, widthM } = AIRFIELD;
    this.units = [];
    // Runway 09 lands eastbound (+x): its left is north (-z). Runway 27 lands westbound.
    for (const [end, dir, leftZ] of [[-1, 1, -1], [1, -1, 1]]) {
      const ux = x + end * lengthM / 2 + dir * PAPI_AIM_POINT_M;
      PAPI_ANGLES_DEG.forEach((angle, i) => {
        const pos = new THREE.Vector3(ux, LIGHT_Y + 0.4, z + leftZ * (widthM / 2 + 15 + i * 9));
        const box = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.8, 0.9), new THREE.MeshLambertMaterial({ color: 0x3a3d40 }));
        box.position.copy(pos).setY(0.4);
        const light = lightPoints([[pos.x, pos.y, pos.z]], 0xffffff, 9);
        scene.add(box, light);
        this.units.push({ pos, dir, angle: (angle * Math.PI) / 180, light });
      });
    }
  }

  // Colours as seen from the pilot's eye (world position), not from the camera.
  update(eye) {
    for (const u of this.units) {
      const ahead = (eye.x - u.pos.x) * -u.dir; // distance out on the approach side
      const visible = ahead > 0 && Math.abs(eye.z - u.pos.z) < ahead * 0.3; // the beam faces the approach
      u.light.visible = visible;
      if (visible) u.light.material.color.setHex(Math.atan2(eye.y - u.pos.y, ahead) > u.angle ? 0xffffff : 0xff2a1e);
    }
  }
}
