// How dark it is, for everything that lights up at night (sky.js sets it from the time of
// day): `night` 0 by day to 1 at night (windows, street lamps, airport and landmark
// lights fade in with it); `glint` scales the sun's (or the moon's) glints on water and
// glass. Shared shader uniforms. Visual only.

import * as THREE from "three";

export const nightLevel = { value: 0 };
export const glintLevel = { value: 1 };

// Points of light (street lamps, runway and taxiway lights, lamp posts): a soft round glow
// `sizeM` across in the world, never smaller than MIN_PX on screen (so a lit city reads
// from altitude), coloured per point, added to the scene's light, hazed with distance (the
// scene's fog), shown only as night falls (`nightLevel`; `dayToo` lights shine by day
// as well, dimmer).
const MIN_PX = 2.2;
const MAX_PX = 40;

const VERTEX = `
attribute vec3 color;
uniform float sizeM;
uniform float scale; // pixels per metre at 1 m distance: viewport height / (2 tan(fov / 2))
uniform float nightLevel;
uniform float dayToo;
varying vec3 vColor;
varying float vOn;
#include <common>
#include <fog_pars_vertex>
#include <logdepthbuf_pars_vertex>
void main() {
  vColor = color;
  vOn = max(nightLevel, dayToo);
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mvPosition;
  gl_PointSize = vOn > 0.001 ? clamp(sizeM * scale / max(-mvPosition.z, 1.0), ${MIN_PX.toFixed(1)}, ${MAX_PX.toFixed(1)}) : 0.0;
  #include <logdepthbuf_vertex>
  #include <fog_vertex>
}`;

const FRAGMENT = `
uniform float nightLevel;
varying vec3 vColor;
varying float vOn;
#include <common>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
void main() {
  #include <logdepthbuf_fragment>
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float r2 = dot(c, c);
  if (r2 > 1.0) discard;
  float core = exp(-r2 * 6.0) * 1.6, halo = exp(-r2 * 2.0) * 0.4;
  gl_FragColor = vec4(vColor * (core + halo) * vOn, 1.0);
  #include <fog_fragment>
}`;

export function lightMaterial(sizeM = 6, { dayToo = 0 } = {}) {
  const m = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { sizeM: { value: sizeM }, scale: { value: 500 }, dayToo: { value: dayToo } }]),
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    blending: THREE.AdditiveBlending,
    transparent: true,
    depthWrite: false,
    fog: true,
  });
  m.uniforms.nightLevel = nightLevel; // shared (after merge, which copies)
  m.uniforms.scale = pointScale;
  return m;
}

// Pixels per metre at 1 m (scene.js sets it from the camera and the canvas).
export const pointScale = { value: 500 };

// A Points object of lights: positions and colours (Float32Arrays, x y z / r g b).
export function lightPoints(position, color, material) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(position, 3));
  g.setAttribute("color", new THREE.BufferAttribute(color, 3));
  const p = new THREE.Points(g, material);
  p.renderOrder = 3; // after the opaque scene
  return p;
}

// A surface glowing at night (lit roads): `rgb` times `strength` added to its light.
export function glowAtNight(material, rgb, strength) {
  const previous = material.onBeforeCompile;
  material.onBeforeCompile = (shader, renderer) => {
    previous?.call(material, shader, renderer);
    shader.uniforms.nightLevel = nightLevel;
    if (!shader.fragmentShader.includes("uniform float nightLevel;")) shader.fragmentShader = shader.fragmentShader.replace("#include <common>", "#include <common>\nuniform float nightLevel;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <opaque_fragment>", `outgoingLight += vec3(${rgb.map((v) => v.toFixed(3)).join(", ")}) * ${strength.toFixed(3)} * nightLevel;\n#include <opaque_fragment>`);
  };
  const previousKey = material.hasOwnProperty("customProgramCacheKey") ? material.customProgramCacheKey.bind(material) : () => "";
  material.customProgramCacheKey = () => `${previousKey()}|glowAtNight`;
  return material;
}
