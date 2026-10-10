// Haze that thins with height: three.js's linear fog (scene.fog near, far: the visibility
// setting, sky.js) replaced in every material by an atmosphere whose density falls off
// exponentially above sea level (HAZE_SCALE_M). Along the line of sight the density is
// integrated in closed form, so from altitude the ground below is clearer than the
// horizon, and tall towers rise out of the haze layer. Near the ground it keeps the
// setting's ranges: clear to `near`, about 95 % haze at `far`. Visual only.
//
// Imported once, before any material is compiled (it patches THREE.ShaderChunk).

import * as THREE from "three";

export const HAZE_SCALE_M = 900; // summer haze layer, project choice

THREE.ShaderChunk.fog_pars_vertex = `#ifdef USE_FOG
  varying float vFogDepth;
  varying vec3 vFogWorld;
#endif`;

// World position from the view-space one (the view matrix is a rotation and a shift).
THREE.ShaderChunk.fog_vertex = `#ifdef USE_FOG
  vFogDepth = - mvPosition.z;
  vFogWorld = transpose(mat3(viewMatrix)) * (mvPosition.xyz - viewMatrix[3].xyz);
#endif`;

THREE.ShaderChunk.fog_pars_fragment = `#ifdef USE_FOG
  uniform vec3 fogColor;
  varying float vFogDepth;
  varying vec3 vFogWorld;
  #ifdef FOG_EXP2
    uniform float fogDensity;
  #else
    uniform float fogNear;
    uniform float fogFar;
  #endif
#endif`;

THREE.ShaderChunk.fog_fragment = `#ifdef USE_FOG
  #ifdef FOG_EXP2
    float fogFactor = 1.0 - exp( - fogDensity * fogDensity * vFogDepth * vFogDepth );
  #else
    // Mean density along the ray (relative to sea level's) from the eye's height to the
    // point's: H (e^(-y0/H) - e^(-y1/H)) / (y1 - y0).
    float fogH = ${HAZE_SCALE_M.toFixed(1)};
    float fogY0 = max(cameraPosition.y, 0.0), fogY1 = max(vFogWorld.y, 0.0), fogDy = fogY1 - fogY0;
    float fogMean = abs(fogDy) < 1.0 ? exp(-fogY0 / fogH) : fogH * (exp(-fogY0 / fogH) - exp(-fogY1 / fogH)) / fogDy;
    float fogX = max(length(vFogWorld - cameraPosition) - fogNear, 0.0) / max(fogFar - fogNear, 1.0);
    float fogFactor = 1.0 - exp(-3.0 * pow(fogX, 1.4) * fogMean);
  #endif
  gl_FragColor.rgb = mix( gl_FragColor.rgb, fogColor, fogFactor );
#endif`;
