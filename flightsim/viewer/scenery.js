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
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(200000, 200000),
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
