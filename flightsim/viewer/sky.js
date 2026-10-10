// Sky, sun, ambient light and haze for a time of day and a visibility. Visual only.
//
// Time-of-day presets set the sun's position (and so the aircraft shadow's direction), the
// light colours, the sky shader's scattering, the haze colour and the exposure.
// "afternoon" is the original look. Visibility scales the haze distances; the quality
// setting caps how far terrain is drawn, so "clear" cannot see beyond it.

import * as THREE from "three";
import { Sky } from "three/addons/objects/Sky.js";
import { glintLevel, nightLevel } from "./nightLights.js";

// Dusk and night: the light is the afterglow, then the moon (`elevation`, `azimuth`: the
// light's direction; `skySun`: where the sky shader's sun is, below the horizon); `night`
// (0-1) switches the city's lights on (nightLights.js), `stars` their brightness, `cloud`
// the clouds' brightness, `glint` the sun's (or moon's) glint on the water. Night colours,
// intensities and the moon's position: project choices, by eye.
export const TIMES = {
  morning: { elevation: 14, azimuth: 105, sun: 0xffdcb0, sunI: 1.9, sky: 0xc6d8ec, ground: 0x55603f, hemiI: 0.9, haze: 0xd6d2cc, turbidity: 6, rayleigh: 1.6, exposure: 0.62, night: 0 },
  midday: { elevation: 58, azimuth: 180, sun: 0xfff6e8, sunI: 2.6, sky: 0xd2e5f6, ground: 0x5c6a46, hemiI: 1.15, haze: 0xc8d7e4, turbidity: 3.5, rayleigh: 1.1, exposure: 0.52, night: 0 },
  afternoon: { elevation: 38, azimuth: 215, sun: 0xfff4e0, sunI: 2.4, sky: 0xcfe3f5, ground: 0x5a6844, hemiI: 1.1, haze: 0xc9d6e2, turbidity: 4, rayleigh: 1.2, exposure: 0.55, night: 0 },
  evening: { elevation: 7, azimuth: 285, sun: 0xffad6a, sunI: 2.0, sky: 0xc0c8d8, ground: 0x565c40, hemiI: 1.0, haze: 0xd6b8a0, turbidity: 8, rayleigh: 2.4, exposure: 0.85, night: 0.15 },
  dusk: { elevation: 3, azimuth: 285, skySun: -1.5, sun: 0xff8a5a, sunI: 0.35, sky: 0x5a6a90, ground: 0x2a2620, hemiI: 0.55, haze: 0x8a7a84, turbidity: 8, rayleigh: 3, exposure: 1.6, night: 0.75, stars: 0.15, cloud: 0.6, glint: 0.3 },
  night: { elevation: 42, azimuth: 120, skySun: -24, sun: 0x9fb4e0, sunI: 0.18, sky: 0x24304c, ground: 0x0e1014, hemiI: 0.32, haze: 0x1c2234, turbidity: 2, rayleigh: 1, exposure: 1.25, night: 1, stars: 0.6, cloud: 0.12, glint: 0.12 },
}; // fmt: skip

// Haze (fog near, far) in metres; "normal" is the original, also the quality "high" values.
export const VISIBILITY = { clear: [16000, 30000], normal: [9000, 21000], hazy: [1500, 7000] };

// Sun direction (unit vector, world frame x east, y up, z south) for elevation/azimuth in
// degrees (azimuth from north, clockwise).
export function sunDirection(elevationDeg, azimuthDeg) {
  const phi = THREE.MathUtils.degToRad(90 - elevationDeg), az = THREE.MathUtils.degToRad(azimuthDeg);
  return new THREE.Vector3(Math.sin(phi) * Math.sin(az), Math.cos(phi), -Math.sin(phi) * Math.cos(az));
}

// Stars: 3000 points on a sphere around the camera (it follows the camera, as the sky
// does), seeded; brightness from a magnitude-like spread, a few tinted. Visual only.
function starField(n = 3000, seed = 7) {
  let a = seed >>> 0;
  const rand = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const y = rand() * 1.04 - 0.04, r = Math.sqrt(1 - y * y), ph = rand() * Math.PI * 2; // above the horizon (and a little below)
    pos.set([r * Math.cos(ph) * 100000, y * 100000, r * Math.sin(ph) * 100000], 3 * i); // inside the camera's far plane
    const b = 0.12 + 0.88 * rand() ** 5, tint = rand(); // most faint, a few bright
    col.set(tint < 0.1 ? [b, b * 0.85, b * 0.7] : tint < 0.2 ? [b * 0.8, b * 0.9, b] : [b, b, b], 3 * i);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const m = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true, transparent: true, depthWrite: false, fog: false, toneMapped: false });
  const p = new THREE.Points(g, m);
  p.frustumCulled = false;
  p.renderOrder = -1; // over the sky dome, under everything else
  p.onBeforeRender = (renderer, scene, camera) => p.position.copy(camera.position);
  return p;
}

export class SkyController {
  constructor(scene, renderer) {
    this.scene = scene;
    this.renderer = renderer;
    this.sky = new Sky();
    this.sky.scale.setScalar(450000);
    const u = this.sky.material.uniforms;
    u.mieCoefficient.value = 0.004;
    u.mieDirectionalG.value = 0.8;
    scene.add(this.sky);
    this.sun = new THREE.DirectionalLight(0xffffff, 1);
    scene.add(this.sun, this.sun.target);
    this.hemi = new THREE.HemisphereLight(0xffffff, 0xffffff, 1);
    scene.add(this.hemi);
    scene.fog = new THREE.Fog(0xffffff, 9000, 21000);
    this.stars = starField();
    scene.add(this.stars);
    this.sunDir = new THREE.Vector3();
    this.fogCap = [9000, 21000]; // from the quality setting
    this.visibility = "normal";
    this.setTime("afternoon");
  }

  setTime(name) {
    const t = TIMES[name] ?? TIMES.afternoon;
    this.time = name in TIMES ? name : "afternoon";
    this.sunDir.copy(sunDirection(t.elevation, t.azimuth));
    const u = this.sky.material.uniforms;
    u.sunPosition.value.copy(t.skySun === undefined ? this.sunDir : sunDirection(t.skySun, t.azimuth > 180 ? t.azimuth : 285));
    this.night = t.night ?? 0;
    nightLevel.value = this.night;
    glintLevel.value = t.glint ?? 1;
    this.stars.material.opacity = t.stars ?? 0;
    this.stars.visible = (t.stars ?? 0) > 0;
    u.turbidity.value = t.turbidity;
    u.rayleigh.value = t.rayleigh;
    this.sun.color.setHex(t.sun);
    this.sun.intensity = t.sunI;
    this.sun.position.copy(this.sunDir).multiplyScalar(10000);
    this.hemi.color.setHex(t.sky);
    this.hemi.groundColor.setHex(t.ground);
    this.hemi.intensity = t.hemiI;
    this.scene.fog.color.setHex(t.haze);
    this.renderer.toneMappingExposure = t.exposure;
  }

  // Haze for a visibility, never beyond the quality setting's reach (`cap`, [near, far]).
  setVisibility(name, cap = this.fogCap) {
    this.visibility = name in VISIBILITY ? name : "normal";
    this.fogCap = cap;
    if (this.visibility === "normal") {
      [this.scene.fog.near, this.scene.fog.far] = cap; // the quality setting's own haze
      return;
    }
    const [near, far] = VISIBILITY[this.visibility];
    this.scene.fog.far = Math.min(far, cap[1]);
    this.scene.fog.near = Math.min(near, this.scene.fog.far * 0.6);
  }
}
