// Close-up ground detail: procedural noise in world coordinates (about 0.7, 2.7 and 11 m
// wavelengths, plus drier patches at ~23 m), strongest near the camera and gone by a few
// hundred metres. It gives the texture and motion cues for judging height in the flare and
// speed on the ground, without image files. Display only.
//
// The noise cells wrap every 1024 cells: world coordinates reach tens of kilometres, where
// sin()-based GPU hashes lose precision; this hash only sees small numbers.

// Shared by every ground material, so the quality setting can turn it off at run time.
export const groundDetailStrength = { value: 1.0 };

const NOISE_GLSL = `
uniform float groundDetailStrength;
varying vec2 vDetailXZ;
float gdHash(vec2 p) {
  p = mod(p, 1024.0);
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float gdNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = gdHash(i), b = gdHash(i + vec2(1.0, 0.0)), c = gdHash(i + vec2(0.0, 1.0)), d = gdHash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}`;

// Adds the detail to a Lambert material (keeping any onBeforeCompile it already has).
// strength: brightness variation (0.4 = +/-40%, about what survives the tone mapping as a
// visible texture); tint: how much drier patches yellow it.
export function addGroundDetail(material, { strength = 0.4, tint = 0.5, fadeStartM = 40, fadeEndM = 350 } = {}) {
  const previous = material.onBeforeCompile;
  material.onBeforeCompile = (shader, renderer) => {
    previous?.call(material, shader, renderer);
    shader.uniforms.groundDetailStrength = groundDetailStrength;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec2 vDetailXZ;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvDetailXZ = (modelMatrix * vec4(position, 1.0)).xz;");
    shader.fragmentShader = shader.fragmentShader.replace("#include <common>", `#include <common>\n${NOISE_GLSL}`).replace(
      "#include <color_fragment>",
      `#include <color_fragment>
{
  float dist = length(vViewPosition);
  float fade = (1.0 - smoothstep(${fadeStartM.toFixed(1)}, ${fadeEndM.toFixed(1)}, dist)) * groundDetailStrength;
  if (fade > 0.001) {
    vec2 p = vDetailXZ;
    float fine = 1.0 - smoothstep(20.0, 120.0, dist); // the finest octave only up close (no shimmer)
    float n = 0.5 + fine * 0.5 * (gdNoise(p / 0.7) - 0.5) + 0.3 * (gdNoise(p / 2.7) - 0.5) + 0.2 * (gdNoise(p / 11.0) - 0.5);
    vec3 c = diffuseColor.rgb * (1.0 + ${strength.toFixed(3)} * (n - 0.5) * 2.0);
    c = mix(c, c * vec3(1.08, 1.02, 0.86), ${tint.toFixed(3)} * gdNoise(p / 23.0 + 17.0));
    diffuseColor.rgb = mix(diffuseColor.rgb, c, fade);
  }
}`,
    );
  };
  const previousKey = material.hasOwnProperty("customProgramCacheKey") ? material.customProgramCacheKey.bind(material) : () => "";
  material.customProgramCacheKey = () => `${previousKey()}|groundDetail-${strength}-${tint}-${fadeStartM}-${fadeEndM}`;
  return material;
}
