// Sun shadows over a real-world region: buildings, bridges, landmarks and palms cast onto
// the ground and each other. One shadow map for the sun (a directional light), framing a
// square of ground around and ahead of the camera, sized by the camera's height (sharp
// close to the ground, wider from altitude). The map is drawn only when that square moves
// (its centre snapped to a grid of an eighth of its size, its size to steps of 25 %), when
// new scenery arrives or the sun changes: the scene's casters are static, so most frames
// reuse it. The flown aircraft keeps its own shadow (scene.js). Sizes and the map
// resolution per quality setting: project choices.

import * as THREE from "three";

const MIN_HALF_M = 250; // half size of the shadowed square near the ground
const MAX_HALF_M = 2500;
const PER_HEIGHT = 2.5; // its growth per metre of height
const AHEAD = 0.55; // of the half size: the square's centre ahead of the camera
const TOWERS_M = 450; // room above the ground for the tallest casters

export class SunShadows {
  constructor(renderer, sun) {
    this.renderer = renderer;
    this.sun = sun;
    this.enabled = false;
    this.mapSize = 4096;
    this.key = null; // the framed square (centre and size) of the current map
    this.dirty = true;
  }

  // On or off (a real-world region and the quality setting: map size 0 is off).
  set(enabled, mapSize = this.mapSize) {
    const sun = this.sun;
    this.enabled = enabled && mapSize > 0;
    if (mapSize && mapSize !== this.mapSize) {
      this.mapSize = mapSize;
      sun.shadow.map?.dispose();
      sun.shadow.map = null; // made again at the new size
    }
    sun.castShadow = this.enabled;
    this.renderer.shadowMap.autoUpdate = false;
    if (this.enabled) {
      sun.shadow.mapSize.set(this.mapSize, this.mapSize);
      sun.shadow.bias = -0.0004;
    }
    this.key = null;
    this.dirty = true;
  }

  // Something casting shadows appeared or changed (tiles, bridges, landmarks, the sun).
  invalidate() {
    this.dirty = true;
  }

  // Each frame, before rendering: frame the square for this camera; draw the map again only
  // if it changed.
  update(camera, groundY, sunDir) {
    if (!this.enabled) return;
    const h = Math.max(0, camera.position.y - groundY);
    const half = MIN_HALF_M * 1.25 ** Math.round(Math.log(Math.min(MAX_HALF_M, Math.max(MIN_HALF_M, MIN_HALF_M + PER_HEIGHT * h)) / MIN_HALF_M) / Math.log(1.25));
    const fwd = new THREE.Vector3();
    camera.getWorldDirection(fwd);
    fwd.y = 0;
    if (fwd.lengthSq() > 1e-6) fwd.normalize();
    const grid = half / 4;
    const cx = Math.round((camera.position.x + fwd.x * AHEAD * half) / grid) * grid;
    const cz = Math.round((camera.position.z + fwd.z * AHEAD * half) / grid) * grid;
    const key = `${cx},${cz},${half}`;
    if (key === this.key && !this.dirty) return;
    this.key = key;
    this.dirty = false;
    const sun = this.sun, cam = sun.shadow.camera;
    sun.target.position.set(cx, groundY, cz);
    sun.target.updateMatrixWorld();
    const dist = 2 * half + TOWERS_M + 500;
    sun.position.copy(sun.target.position).addScaledVector(sunDir, dist);
    sun.updateMatrixWorld();
    // The square seen along the sun: a little wider than it (its corners), deep enough for
    // towers on its far side.
    const r = half * 1.15;
    [cam.left, cam.right, cam.top, cam.bottom] = [-r, r, r, -r];
    cam.near = Math.max(1, dist - 3 * half - TOWERS_M);
    cam.far = dist + 2 * half + 100;
    cam.updateProjectionMatrix();
    sun.shadow.normalBias = ((2 * r) / this.mapSize) * 1.2; // about a texel, in metres
    this.renderer.shadowMap.needsUpdate = true;
  }
}
