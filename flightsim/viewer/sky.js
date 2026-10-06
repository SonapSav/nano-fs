// Sky, sun, ambient light and haze for a time of day and a visibility. Visual only.
//
// Time-of-day presets set the sun's position (and so the aircraft shadow's direction), the
// light colours, the sky shader's scattering, the haze colour and the exposure.
// "afternoon" is the original look. Visibility scales the haze distances; the quality
// setting caps how far terrain is drawn, so "clear" cannot see beyond it.

import * as THREE from "three";
import { Sky } from "three/addons/objects/Sky.js";

export const TIMES = {
  morning: { elevation: 14, azimuth: 105, sun: 0xffdcb0, sunI: 1.9, sky: 0xc6d8ec, ground: 0x55603f, hemiI: 0.9, haze: 0xd6d2cc, turbidity: 6, rayleigh: 1.6, exposure: 0.62 },
  midday: { elevation: 58, azimuth: 180, sun: 0xfff6e8, sunI: 2.6, sky: 0xd2e5f6, ground: 0x5c6a46, hemiI: 1.15, haze: 0xc8d7e4, turbidity: 3.5, rayleigh: 1.1, exposure: 0.52 },
  afternoon: { elevation: 38, azimuth: 215, sun: 0xfff4e0, sunI: 2.4, sky: 0xcfe3f5, ground: 0x5a6844, hemiI: 1.1, haze: 0xc9d6e2, turbidity: 4, rayleigh: 1.2, exposure: 0.55 },
  evening: { elevation: 7, azimuth: 285, sun: 0xffad6a, sunI: 2.0, sky: 0xc0c8d8, ground: 0x565c40, hemiI: 1.0, haze: 0xd6b8a0, turbidity: 8, rayleigh: 2.4, exposure: 0.85 },
}; // fmt: skip

// Haze (fog near, far) in metres; "normal" is the original, also the quality "high" values.
export const VISIBILITY = { clear: [16000, 30000], normal: [9000, 21000], hazy: [1500, 7000] };

// Sun direction (unit vector, world frame x east, y up, z south) for elevation/azimuth in
// degrees (azimuth from north, clockwise).
export function sunDirection(elevationDeg, azimuthDeg) {
  const phi = THREE.MathUtils.degToRad(90 - elevationDeg), az = THREE.MathUtils.degToRad(azimuthDeg);
  return new THREE.Vector3(Math.sin(phi) * Math.sin(az), Math.cos(phi), -Math.sin(phi) * Math.cos(az));
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
    u.sunPosition.value.copy(this.sunDir);
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
