// Procedural terrain (meshes; the height itself is in terrainCore.js). Its height is shared
// with the physics: flightsim/world/terrain.py is a bit-identical port, used by tasks with `terrain: procedural` (other tasks fly over
// flat ground at 0 m). The airfield, valley floors and lake surfaces sit at about 0 m and
// hills rise above (at most ~350 m).
//
// Everything is a pure function of world position and a fixed seed, so every viewer and
// every flight sees the same world. World frame: x = east, y = up, z = south (metres).

import * as THREE from "three";
import { addGroundDetail } from "./groundDetail.js";
import { CLIP_FRAGMENT, CLIP_GLSL, clipUniforms, hideUnderImagery } from "./imageryClip.js";
import { DETAIL_GLSL, detailTexture, detailUniforms } from "./groundTextures.js";
import { glintLevel, glowAtNight, lightMaterial, lightPoints, nightLevel } from "./nightLights.js";
import { treeBatch, updateTreeBatch } from "./trees.js";
import { TILE_SIZE_M, WATER_LEVEL_M, tileGeometryData, tileObjectsData } from "./terrainCore.js";
import { SEA_SURFACE_M, demTileGeometryData, demTileObjectsData } from "./demTiles.js";
import { world } from "./world.js";

// Height, land cover and tile data live in terrainCore.js (no three.js: also used by the
// tile worker); re-exported here for the rest of the viewer.
export { AIRFIELD, TILE_SIZE_M, VILLAGE_CELL_M, WATER_LEVEL_M, WORLD_SEED, height, isForest, villageCentre } from "./terrainCore.js";

// Field crops; the patchwork itself is drawn per pixel in the terrain shader (fieldMaterial).
const FIELD_COLOURS = [0x7c8b55, 0x8e9a5a, 0x6f8248, 0xa59b62, 0x8b8a4e, 0x74874d, 0x9aa56a].map((c) => new THREE.Color(c));

// Field patchwork and rivers on (1) or off (0, the plain land colour): a switch for the
// performance test (bench.js).
export const terrainEffects = { value: 1.0 };

// Lambert material plus a per-pixel field patchwork: ~450 m cells on a slightly rotated
// grid, one crop colour per cell, darker hedgerows along the edges. Crisp at any range.
// `rivers`: {value: 1} draws the procedural world's rivers, 0 not (a real-world region has
// its own water).
function fieldMaterial(rivers) {
  const material = new THREE.MeshLambertMaterial({ vertexColors: true });
  material.onBeforeCompile = (shader) => {
    shader.uniforms.fieldColours = { value: FIELD_COLOURS };
    shader.uniforms.terrainEffects = terrainEffects;
    shader.uniforms.terrainRivers = rivers;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float fieldness;\nvarying float vFieldness;\nvarying vec2 vWorldXZ;\nvarying float vHeight;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFieldness = fieldness;\nvWorldXZ = (modelMatrix * vec4(position, 1.0)).xz;\nvHeight = position.y;");
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
uniform vec3 fieldColours[${FIELD_COLOURS.length}];
uniform float terrainEffects;
uniform float terrainRivers;
varying float vFieldness;
varying vec2 vWorldXZ;
varying float vHeight;
float fieldHash(vec2 c) { return fract(sin(dot(c, vec2(12.9898, 78.233))) * 43758.5453); }
// River: a contour of a smooth noise field (cells wrap every 1024: small numbers only).
float rvHash(vec2 p) { p = mod(p, 1024.0); vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float rvNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(rvHash(i), rvHash(i + vec2(1.0, 0.0)), u.x), mix(rvHash(i + vec2(0.0, 1.0)), rvHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float rvField(vec2 xz) { vec2 p = xz / 3200.0 + 41.0; return 0.65 * rvNoise(p) + 0.35 * rvNoise(p * 2.3 + 7.0); }`,
      )
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
if (terrainEffects > 0.5) {
  vec2 uv = vec2(vWorldXZ.x * 0.97 + vWorldXZ.y * 0.24, vWorldXZ.y * 0.97 - vWorldXZ.x * 0.24) / 450.0;
  vec2 cell = floor(uv);
  int k = int(fieldHash(cell) * ${FIELD_COLOURS.length}.0);
  vec3 crop = fieldColours[0];
  for (int i = 1; i < ${FIELD_COLOURS.length}; i++) if (i == k) crop = fieldColours[i];
  vec2 f = fract(uv);
  float edge = min(min(f.x, 1.0 - f.x), min(f.y, 1.0 - f.y)) * 450.0; // metres to the cell edge
  crop *= mix(0.72, 1.0, smoothstep(2.0, 7.0, edge)); // hedgerow
  diffuseColor.rgb = mix(diffuseColor.rgb, crop, clamp(vFieldness, 0.0, 1.0));
}
if (terrainEffects > 0.5 && terrainRivers > 0.5) {
  // River on the valley floors (dry land at ~0 m): the 0.5 contour of rvField, its width
  // kept in metres by dividing by the field's gradient; grassy banks either side.
  float valley = (1.0 - smoothstep(0.4, 2.0, vHeight)) * smoothstep(1800.0, 2400.0, length(vWorldXZ)); // not on the airfield
  if (valley > 0.0) {
    float n = rvField(vWorldXZ);
    vec2 grad = vec2(rvField(vWorldXZ + vec2(4.0, 0.0)) - n, rvField(vWorldXZ + vec2(0.0, 4.0)) - n) / 4.0;
    float metres = abs(n - 0.5) / max(length(grad), 1e-6);
    float water = (1.0 - smoothstep(9.0, 12.0, metres)) * valley;
    float bank = (1.0 - smoothstep(12.0, 22.0, metres)) * valley;
    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.30, 0.40, 0.25), bank * 0.6);
    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.17, 0.30, 0.40), water);
  }
}`,
      );
  };
  return addGroundDetail(material); // close-up texture on top of the patchwork
}

const WATER = new THREE.MeshLambertMaterial({ color: 0x3f6b8c });
const SEA = new THREE.MeshLambertMaterial({ color: 0x3b7d93 }); // a real region's sea and lakes (the Gulf's shallow turquoise, by eye)
const WATER_QUAD = new THREE.PlaneGeometry(TILE_SIZE_M, TILE_SIZE_M).rotateX(-Math.PI / 2);

// --- Tiles: meshes from tile data -------------------------------------------------------

function tileGeometry(data) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(data.position, 3));
  g.setAttribute("color", new THREE.BufferAttribute(data.color, 3));
  g.setAttribute("fieldness", new THREE.BufferAttribute(data.fieldness, 1));
  g.setAttribute("normal", new THREE.BufferAttribute(data.normal, 3));
  g.setIndex(new THREE.BufferAttribute(data.index, 1));
  if (data.uv) g.setAttribute("uv", new THREE.BufferAttribute(data.uv, 2));
  g.userData.hasWater = data.hasWater;
  return g;
}

// The sea and lakes of a real-world region, drawn on the terrain itself (the physics' sea is
// the ground at 0 m): colour from shallow turquoise near the shore to deep blue offshore
// (the shore distance file), small moving waves tilting the lighting (fading out with
// distance, where they would shimmer), the sky reflected at low angles (Schlick's Fresnel
// with water's 2 %) and the sun's glint. Colours and wave sizes are project choices, by
// eye. `waterUniforms` are shared by every tile: the scene keeps the time, sun and sky
// (haze colour) current.
export const waterUniforms = {
  waterGlint: glintLevel, // the sun's (or moon's) glint: dimmer at dusk and night
  waterTime: { value: 0 },
  waterSun: { value: new THREE.Vector3(0, 1, 0) },
  waterSky: { value: new THREE.Color(0.7, 0.8, 0.9) },
};
const NO_SHORE = new THREE.DataTexture(new Uint8Array([0]), 1, 1, THREE.RedFormat); // older builds: no water effects
NO_SHORE.needsUpdate = true;
const WATER_GLSL = `
uniform sampler2D shoreMap;
uniform float waterTint;
uniform float imageryGain;
uniform float waterTime;
uniform vec3 waterSun;
uniform vec3 waterSky;
uniform float waterGlint;
varying vec3 vWaterPos;
// Slope of a sum of directional waves (wavelengths ~6-40 m), for the surface normal.
vec2 waterSlope(vec2 p, float t) {
  vec2 g = vec2(0.0);
  vec2 d1 = vec2(0.82, 0.57), d2 = vec2(0.31, 0.95), d3 = vec2(0.97, -0.24), d4 = vec2(-0.45, 0.89);
  g += 0.050 * cos(dot(p, d1) * 0.16 + t * 1.25) * d1;
  g += 0.040 * cos(dot(p, d2) * 0.37 + t * 1.90) * d2;
  g += 0.030 * cos(dot(p, d3) * 0.71 + t * 2.60) * d3;
  g += 0.022 * cos(dot(p, d4) * 1.05 + t * 3.20) * d4;
  return g;
}`;

// A real-world region's tile: its land cover texture (demTiles.js) on a plain material with
// the close-up ground detail, and the water above.
function regionMaterial(data) {
  const texture = (pixels, format, srgb) => {
    const t = new THREE.DataTexture(pixels, data.textureSize, data.textureSize, format);
    if (srgb) t.colorSpace = THREE.SRGBColorSpace;
    t.magFilter = THREE.LinearFilter;
    t.minFilter = THREE.LinearMipmapLinearFilter;
    t.generateMipmaps = true;
    t.anisotropy = 4;
    t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
    t.needsUpdate = true;
    return t;
  };
  // The ground colour: the region's imagery when built (an ImageBitmap from the worker;
  // rows go south as the land cover, so no flip), else the land cover colours.
  let tex;
  if (data.imagery) {
    tex = new THREE.Texture(data.imagery);
    tex.flipY = false;
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    tex.anisotropy = 4;
    tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.needsUpdate = true;
  } else {
    tex = texture(data.texture, THREE.RGBAFormat, true);
  }
  const shore = data.shore ? texture(data.shore, THREE.RedFormat, false) : NO_SHORE;
  const material = new THREE.MeshLambertMaterial({ map: tex });
  material.userData.shore = shore;
  material.onBeforeCompile = (shader) => {
    if (data.imagery) detailUniforms.detailMap.value = detailTexture();
    Object.assign(shader.uniforms, waterUniforms, clipUniforms, data.imagery ? detailUniforms : {}, { shoreMap: { value: shore }, waterTint: { value: data.imagery ? 0.3 : 1.0 }, imageryGain: { value: data.imagery ? 1.4 : 1.0 } });
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vWaterPos;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvWaterPos = (modelMatrix * vec4(position, 1.0)).xyz;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\n${WATER_GLSL}\n${CLIP_GLSL}${data.imagery ? `\n${DETAIL_GLSL}` : ""}`)
      .replace("#include <map_fragment>", `#include <map_fragment>\n${CLIP_FRAGMENT}`)
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
diffuseColor.rgb *= imageryGain; // imagery as an albedo reads dark under the scene's light (project choice, by eye)
float shoreCode = texture2D(shoreMap, vMapUv).r * 255.0;
float water = smoothstep(0.3, 0.9, shoreCode);
${data.imagery ? "diffuseColor.rgb = groundDetail(diffuseColor.rgb, vWaterPos.xz, length(vViewPosition), 1.0 - water); // close-up texture by what the imagery shows (groundTextures.js)" : ""}
float waterDistM = max(0.0, shoreCode - 1.0) * 8.0;
vec3 waterColour = mix(vec3(0.075, 0.36, 0.33), vec3(0.012, 0.10, 0.17), smoothstep(15.0, 1200.0, waterDistM));
// Over imagery: its own colours near the shore (the real shallows), ours offshore, where the
// imagery's deep water is near black.
float tint = waterTint < 0.99 ? mix(0.45, 0.9, smoothstep(60.0, 1500.0, waterDistM)) : 1.0;
diffuseColor.rgb = mix(diffuseColor.rgb, waterColour, water * tint);
vec2 wslope = water * (1.0 - smoothstep(300.0, 2000.0, length(vViewPosition))) * waterSlope(vWaterPos.xz, waterTime);`,
      )
      .replace(
        "#include <normal_fragment_begin>",
        `#include <normal_fragment_begin>
if (water > 0.01) normal = normalize(mix(normal, normalize((viewMatrix * vec4(-wslope.x, 1.0, -wslope.y, 0.0)).xyz), water));`,
      )
      .replace(
        "#include <opaque_fragment>",
        `if (water > 0.01) {
  vec3 wv = normalize(cameraPosition - vWaterPos);
  vec3 wn = normalize(vec3(-wslope.x, 1.0, -wslope.y));
  float fresnel = 0.02 + 0.98 * pow(1.0 - max(dot(wn, wv), 0.0), 5.0);
  outgoingLight = mix(outgoingLight, waterSky * 0.9, fresnel * water);
  float glint = pow(max(dot(reflect(-wv, wn), normalize(waterSun)), 0.0), 180.0) * step(0.02, waterSun.y);
  outgoingLight += vec3(1.0, 0.93, 0.8) * glint * 4.0 * waterGlint * water;
}
#include <opaque_fragment>`,
      );
  };
  material.customProgramCacheKey = () => (data.imagery ? "regionWaterDetail" : "regionWater");
  return data.imagery ? material : addGroundDetail(material); // imagery: its own detail (groundTextures.js)
}

// Fences: a chain-link mesh on posts every 3 m (the facade attribute: metres along, up),
// grey; between the wires discarded; far off the mesh thins to its average (posts and
// the top rail stay). Sizes project choices.
function fenceMaterial() {
  const material = new THREE.MeshLambertMaterial({ color: 0x8a8d8f, side: THREE.DoubleSide });
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec3 facade;\nvarying vec3 vFence;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFence = facade;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vFence;")
      .replace(
        "#include <clipping_planes_fragment>",
        `#include <clipping_planes_fragment>
{
  float post = step(fract(vFence.x / 3.0), 0.03);
  float rail = step(vFence.z - 0.08, vFence.y);
  vec2 d = vec2(vFence.x + vFence.y, vFence.x - vFence.y) / 0.12;
  float w = clamp(fwidth(d.x) * 0.5, 0.0, 1.0);
  float wire = max(step(fract(d.x), 0.18 + w), step(fract(d.y), 0.18 + w));
  float keep = max(max(post, rail), w > 0.6 ? 0.0 : wire);
  if (keep < 0.5 && w <= 0.6) discard;
  if (w > 0.6 && max(post, rail) < 0.5 && fract(dot(gl_FragCoord.xy, vec2(0.5, 0.25))) > 0.35) discard; // far: thinned to the mesh's average
}`,
      );
  };
  material.customProgramCacheKey = () => "fence";
  return material;
}

// Building facades (featureGeometry.js `facade`: metres along the wall, up from the base,
// the building's height): window bays every 3.3 m floor and 3.2 m across on ordinary
// buildings; from 40 m up, glass towers: continuous glass bands with thin floor slabs,
// reflecting the haze (Schlick's Fresnel) and the sun. The pattern fades out with
// distance (it would shimmer). At night windows light up (nightLights.js). Sizes and
// colours project choices, by eye.
function facadeMaterial() {
  const material = new THREE.MeshLambertMaterial({ vertexColors: true });
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, waterUniforms, { nightLevel });
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec3 facade;\nvarying vec3 vFacade;\nvarying vec3 vFacadePos;\nvarying vec3 vFacadeNormal;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFacade = facade;\nvFacadePos = (modelMatrix * vec4(position, 1.0)).xyz;\nvFacadeNormal = normalize(mat3(modelMatrix) * normal);");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vFacade;\nvarying vec3 vFacadePos;\nvarying vec3 vFacadeNormal;\nuniform vec3 waterSun;\nuniform vec3 waterSky;\nuniform float waterGlint;\nuniform float nightLevel;\n" +
        "float band(float x, float a, float b, float w) { return smoothstep(a - w, a + w, x) * (1.0 - smoothstep(b - w, b + w, x)); }")
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
float glassAmount = 0.0;
vec3 nightGlow = vec3(0.0);
if (vFacade.x >= 0.0) {
  // Darker toward the foot of the wall (the ground and neighbours hide part of the sky
  // there): a cheap ambient occlusion, strongest in the first metres (project choice).
  diffuseColor.rgb *= mix(0.6, 1.0, smoothstep(0.0, 5.0, vFacade.y));
  float fade = 1.0 - smoothstep(500.0, 2500.0, length(vViewPosition));
  float tower = step(40.0, vFacade.z);
  float fy = fract(vFacade.y / 3.3), fx = fract(vFacade.x / 3.2);
  float aa = clamp(fwidth(vFacade.y / 3.3) * 1.5, 0.002, 0.2);
  float window = band(fy, 0.32, 0.80, aa) * band(fx, 0.15, 0.85, aa) * step(0.8, vFacade.y) * step(vFacade.y, vFacade.z - 1.0);
  float glassBand = (1.0 - band(fy, 0.0, 0.12, aa)) * step(vFacade.y, vFacade.z - 0.5);
  glassAmount = mix(window * 0.55, glassBand, tower) * fade;
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.05, 0.07, 0.09), glassAmount * 0.8);
  // At night: windows lit at random (about 4 in 10), warm or cool, per window cell and
  // neighbourhood; beyond the pattern's fade their average glow, so the city still
  // shines from afar (project choices).
  if (nightLevel > 0.001) {
    vec2 cell = vec2(floor(vFacade.x / 3.2), floor(vFacade.y / 3.3)) + floor(vFacadePos.xz / 37.0) * 17.0;
    float h = fract(sin(dot(cell, vec2(12.9898, 78.233))) * 43758.5453);
    float lit = step(0.6, h) * step(0.8, vFacade.y) * step(vFacade.y, vFacade.z - 1.0);
    vec3 tint = mix(vec3(1.0, 0.72, 0.42), vec3(0.82, 0.88, 1.0), step(0.85, fract(h * 7.31)));
    float near = mix(window, glassBand * 0.9, tower) * lit;
    // Far: each neighbourhood's own share of lit windows (some dark), in its window area.
    float share = fract(sin(dot(floor(vFacadePos.xz / 37.0), vec2(39.3468, 11.1353))) * 24634.6345);
    float far = mix(0.33, 0.8, tower) * 0.4 * share * share;
    nightGlow = tint * mix(far, near, fade) * 1.6;
  }
}`,
      )
      .replace(
        "#include <opaque_fragment>",
        `if (glassAmount > 0.0) {
  vec3 v = normalize(cameraPosition - vFacadePos);
  float fresnel = 0.04 + 0.96 * pow(1.0 - max(dot(vFacadeNormal, v), 0.0), 5.0);
  outgoingLight = mix(outgoingLight, waterSky * 0.85, min(1.0, fresnel + 0.25) * glassAmount);
  float glint = pow(max(dot(reflect(-v, vFacadeNormal), normalize(waterSun)), 0.0), 120.0) * step(0.02, waterSun.y);
  outgoingLight += vec3(1.0, 0.93, 0.8) * glint * 3.0 * waterGlint * glassAmount;
}
outgoingLight += nightGlow * nightLevel;
#include <opaque_fragment>`,
      );
  };
  material.customProgramCacheKey = () => "facade";
  return material;
}

// A date palm around its trunk's middle: the trunk (9 m) and the crown of fronds drooping
// from its top, coloured per vertex.
function palmGeometry() {
  const parts = [[new THREE.CylinderGeometry(0.22, 0.32, 9, 5), 0x7a6248], [new THREE.ConeGeometry(3.6, 1.6, 7).rotateX(Math.PI).translate(0, 4.5, 0), 0x4d6b35]];
  const pos = [], nrm = [], col = [];
  for (const [g, hex] of parts) {
    const ni = g.toNonIndexed(), c = new THREE.Color(hex);
    pos.push(...ni.attributes.position.array);
    nrm.push(...ni.attributes.normal.array);
    for (let i = 0; i < ni.attributes.position.count; i++) col.push(c.r, c.g, c.b);
    g.dispose();
    ni.dispose();
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(nrm, 3));
  g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  return g;
}

const NEAR_TEXTURE = 256, FAR_TEXTURE = 64; // land cover texels per tile side (near: one per cell)

// A region's OpenStreetMap features by ring (project choices): roads, paving and every
// building on the near tiles; buildings of 30 m and up on the next ring, 60 m (the
// skyline) beyond.
const buildingsMinM = (w) => (w.objects ? 0 : w.farTrees ? 30 : 60);

// Meshes of a tile's features (featureGeometry.js arrays) with the shared materials.
function featureMeshes(f, mats) {
  const group = new THREE.Group();
  for (const [kind, data] of Object.entries(f)) {
    if (!data) continue;
    if (kind === "lights") {
      group.add(lightPoints(data.position, data.color, mats.lights)); // street and taxiway lights at night
      continue;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(data.position, 3));
    g.setAttribute("normal", new THREE.BufferAttribute(data.normal, 3));
    if (data.color) g.setAttribute("color", new THREE.BufferAttribute(data.color, 3));
    if (data.facade) g.setAttribute("facade", new THREE.BufferAttribute(data.facade, 3));
    g.setIndex(new THREE.BufferAttribute(data.index, 1));
    const m = new THREE.Mesh(g, mats[kind]);
    m.receiveShadow = true; // the sun's shadows (sunShadows.js)
    m.castShadow = kind === "buildings" || kind === "wall" || kind === "fence";
    group.add(m);
  }
  return group;
}

function tileObjects(data, shared, far) {
  const group = new THREE.Group();
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  const { trees, houses, landmarks, palms, bushes } = data;
  // A region's palms (trunk and crown) and bushes.
  if (palms?.length) {
    const n = palms.length / 4;
    const trees = new THREE.InstancedMesh(shared.palm, shared.palmMat, n);
    trees.castShadow = true;
    for (let i = 0; i < n; i++) {
      const [x, h, z, k] = palms.subarray(4 * i, 4 * i + 4);
      q.setFromAxisAngle(up, (x * 13 + z * 7) % 6.283);
      trees.setMatrixAt(i, m.compose(p.set(x, h + 4.5 * k, z), q, s.set(k, k, k)));
    }
    group.add(trees);
  }
  if (bushes?.length) {
    const n = bushes.length / 4;
    const mesh = new THREE.InstancedMesh(shared.bush, shared.bushMat, n);
    for (let i = 0; i < n; i++) {
      const [x, h, z, k] = bushes.subarray(4 * i, 4 * i + 4);
      mesh.setMatrixAt(i, m.compose(p.set(x, h + 1.2 * k, z), q.identity(), s.set(k, k, k)));
    }
    group.add(mesh);
  }
  if (trees.length) {
    const crowns = new THREE.InstancedMesh(far ? shared.farCrown : shared.crown, shared.crownMat, trees.length / 4);
    for (let i = 0; i < trees.length / 4; i++) {
      const [x, h, z, k] = trees.subarray(4 * i, 4 * i + 4);
      crowns.setMatrixAt(i, m.compose(p.set(x, h + 7 * k, z), q.identity(), s.set(k, k, k)));
    }
    group.add(crowns);
  }
  if (houses.length) {
    const n = houses.length / 6;
    const walls = new THREE.InstancedMesh(shared.box, shared.wallMat, n);
    const roofs = new THREE.InstancedMesh(shared.roof, shared.roofMat, n);
    for (let i = 0; i < n; i++) {
      const [x, h, z, yaw, w, d] = houses.subarray(6 * i, 6 * i + 6);
      q.setFromAxisAngle(up, yaw);
      walls.setMatrixAt(i, m.compose(p.set(x, h + 2.5, z), q, s.set(w, 5, d)));
      roofs.setMatrixAt(i, m.compose(p.set(x, h + 5 + 1.5, z), q, s.set(w * 1.05, 3, d * 1.05)));
    }
    group.add(walls, roofs);
  }
  for (let i = 0; i < landmarks.length / 4; i++) {
    const [x, h, z, kind] = landmarks.subarray(4 * i, 4 * i + 4);
    if (kind < 0.6) {
      // Church: nave, tower and spire, ~35 m tall.
      const nave = new THREE.Mesh(shared.box, shared.wallMat);
      nave.scale.set(10, 9, 22);
      nave.position.set(x, h + 4.5, z);
      const tower = new THREE.Mesh(shared.box, shared.wallMat);
      tower.scale.set(5, 22, 5);
      tower.position.set(x, h + 11, z - 13);
      const spire = new THREE.Mesh(shared.spire, shared.roofMat);
      spire.position.set(x, h + 22 + 7, z - 13);
      group.add(nave, tower, spire);
    } else {
      // Water tower: a tank on a column, ~30 m tall.
      const column = new THREE.Mesh(shared.column, shared.towerMat);
      column.position.set(x, h + 12, z);
      const tank = new THREE.Mesh(shared.tank, shared.towerMat);
      tank.position.set(x, h + 27, z);
      group.add(column, tank);
    }
  }
  return group;
}

// --- Streaming manager ----------------------------------------------------------------

// Quality presets: terrain rings (tile mesh resolution and how far tiles reach), trees per
// near tile, haze distances, pixel ratio and the close-up ground detail. "high" is the
// original setting.
// ?notrees in the viewer's URL: no measured trees (performance comparisons).
const NO_TREES = typeof location !== "undefined" && new URLSearchParams(location.search).has("notrees");

export const QUALITY = {
  low: {
    rings: [{ maxRing: 1, segments: 48, objects: true }, { maxRing: 3, segments: 12, objects: false }],
    maxTrees: 250, fog: [5000, 12000], pixelRatio: 1, groundDetail: 0, shadowMap: 0,
  },
  medium: {
    rings: [{ maxRing: 1, segments: 64, objects: true }, { maxRing: 2, segments: 32, objects: false, farTrees: true }, { maxRing: 4, segments: 12, objects: false }],
    maxTrees: 550, fog: [7000, 17000], pixelRatio: 1.5, groundDetail: 1, shadowMap: 2048,
  },
  high: {
    rings: [
      { maxRing: 1, segments: 96, objects: true }, // the 3 x 3 tiles around the aircraft
      { maxRing: 2, segments: 48, objects: false, farTrees: true },
      { maxRing: 5, segments: 16, objects: false }, // out to ~22 km, hidden in haze beyond
    ],
    maxTrees: 900, fog: [9000, 21000], pixelRatio: 2, groundDetail: 1, shadowMap: 4096,
  },
}; // fmt: skip

// Tiles are built in a Web Worker (terrainWorker.js) when the browser has one, so the
// drawing never waits for them; otherwise (Node tests, a failed worker) here, a few per
// frame. The worker returns tile data; the meshes are made here (fast).
const MAX_IN_FLIGHT = 3; // tiles asked of the worker at a time (nearest first; stays responsive when the wanted set changes)
const specKey = (w) => `${w.segments}|${w.objects ? 1 : 0}|${w.farTrees ? 1 : 0}`;

export class Terrain {
  constructor(scene, quality = "high", { worker = true } = {}) {
    this.scene = scene;
    this.wanted = new Map();
    this.inFlight = new Map(); // key -> spec key asked of the worker
    this.worker = null;
    if (worker && typeof Worker !== "undefined") {
      try {
        this.worker = new Worker(new URL("./terrainWorker.js", import.meta.url), { type: "module" });
        this.worker.onmessage = (e) => this._built(e.data);
        this.worker.onerror = () => this._noWorker();
      } catch {
        this.worker = null;
      }
    }
    this.rings = QUALITY[quality].rings;
    this.tiles = new Map(); // key -> { mesh, objects, segments }
    this.queue = [];
    this.rivers = { value: 1.0 };
    this.material = fieldMaterial(this.rivers);
    this.scenery = null; // a real-world region (world.js), or null: procedural
    const pulled = { polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 };
    this.featureMats = {
      // Roads and railways give way to a region's 1 m imagery where it covers (imageryClip.js).
      // At night they light up (street lamps) over the imagery too (imageryClip.js).
      // Their colour: the imagery's (vertex colours, featureGeometry.js featureGroundData).
      roads: glowAtNight(hideUnderImagery(addGroundDetail(new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide, ...pulled }), { strength: 0.25, tint: 0, fadeEndM: 250 })), [1.0, 0.72, 0.45], 0.05),
      lights: lightMaterial(3.5),
      rail: hideUnderImagery(new THREE.MeshLambertMaterial({ color: 0x5b4a3e, side: THREE.DoubleSide, ...pulled })),
      paved: addGroundDetail(new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide, ...pulled }), { strength: 0.25, tint: 0, fadeEndM: 250 }),
      // Walls (render) and fences (see-through mesh: discarded between the wires).
      wall: new THREE.MeshLambertMaterial({ color: 0xd2c7b0, side: THREE.DoubleSide }),
      fence: fenceMaterial(),
      buildings: facadeMaterial(),
      taxilines: new THREE.MeshBasicMaterial({ color: 0xd9a92b, side: THREE.DoubleSide, ...pulled, polygonOffsetFactor: -4, polygonOffsetUnits: -4 }),
    };
    this.shared = {
      crown: new THREE.ConeGeometry(4, 14, 6),
      farCrown: new THREE.ConeGeometry(4.5, 14, 4), // far ring: fewer faces
      spire: new THREE.ConeGeometry(3.4, 14, 4).rotateY(Math.PI / 4),
      column: new THREE.CylinderGeometry(1.5, 2, 24, 8),
      tank: new THREE.CylinderGeometry(6, 5, 7, 12),
      towerMat: new THREE.MeshLambertMaterial({ color: 0xbfc4c7 }),
      crownMat: new THREE.MeshLambertMaterial({ color: 0x2f4a2a }),
      // Real-world regions: date palms (9 m: trunk and a flat crown of fronds, one geometry
      // with vertex colours: one draw call per tile) and low bushes.
      palm: palmGeometry(),
      bush: new THREE.IcosahedronGeometry(2.2, 0).scale(1, 0.55, 1),
      palmMat: new THREE.MeshLambertMaterial({ vertexColors: true }),
      bushMat: new THREE.MeshLambertMaterial({ color: 0x3c5532 }),
      box: new THREE.BoxGeometry(1, 1, 1),
      wallMat: new THREE.MeshLambertMaterial({ color: 0xd9d4c5 }),
      roof: (() => {
        const g = new THREE.CylinderGeometry(0, 0.75, 1, 4, 1);
        g.rotateY(Math.PI / 4);
        return g;
      })(),
      roofMat: new THREE.MeshLambertMaterial({ color: 0x8f3b2f }),
      maxTrees: QUALITY[quality].maxTrees,
    };
    this.centre = null;
  }

  // Rebuild every tile for a quality preset (QUALITY).
  setQuality(quality) {
    this.rings = QUALITY[quality].rings;
    this.shared.maxTrees = QUALITY[quality].maxTrees;
    for (const key of [...this.tiles.keys()]) this._drop(key);
    this.queue = [];
    this.inFlight.clear(); // late answers no longer match the wanted spec and are dropped
    this.centre = null;
  }

  // The world the tiles show (world.js): procedural, or a real-world region. Rebuilds them.
  setWorld(w) {
    const scenery = w.real && w.manifest ? { scenery: w.scenery, tiles: w.manifest.tiles } : null;
    if ((scenery?.scenery.hash ?? null) === (this.scenery?.scenery.hash ?? null)) return;
    this.scenery = scenery;
    this.rivers.value = scenery ? 0 : 1;
    for (const key of [...this.tiles.keys()]) this._drop(key);
    this.queue = [];
    this.inFlight.clear();
    this.centre = null;
  }

  // The worker failed (e.g. no module workers): build here from now on.
  _noWorker() {
    this.worker?.terminate();
    this.worker = null;
    this.inFlight.clear();
    this.centre = null; // recompute the queue on the next update
  }

  _wanted(cx, cz) {
    const wanted = new Map();
    const R = this.rings[this.rings.length - 1].maxRing;
    for (let dz = -R; dz <= R; dz++) {
      for (let dx = -R; dx <= R; dx++) {
        const ring = Math.max(Math.abs(dx), Math.abs(dz));
        const spec = this.rings.find((r) => ring <= r.maxRing);
        wanted.set(`${cx + dx},${cz + dz}`, { tx: cx + dx, tz: cz + dz, ring, ...spec });
      }
    }
    return wanted;
  }

  // Call every frame with the camera position. Asks the worker for the nearest missing tiles;
  // without one, builds tiles here until about `budgetMs` of this frame is used, at least
  // one (a near tile takes ~15 ms, a far one < 1 ms).
  update(x, z, budgetMs = 8, view = null) {
    // Trees: detail by distance, only those ahead of the view (`view`: its horizontal heading).
    for (const t of this.tiles.values()) if (t.trees) updateTreeBatch(t.trees, x, z, view?.x ?? 0, view?.z ?? -1);
    const cx = Math.floor(x / TILE_SIZE_M), cz = Math.floor(z / TILE_SIZE_M);
    if (!this.centre || this.centre[0] !== cx || this.centre[1] !== cz) {
      this.centre = [cx, cz];
      this.wanted = this._wanted(cx, cz);
      for (const [key, tile] of this.tiles) {
        const want = this.wanted.get(key);
        if (!want || want.segments !== tile.segments || want.objects !== tile.near || Boolean(want.farTrees) !== tile.far) this._drop(key);
      }
      this.queue = [...this.wanted.values()].filter((w) => !this.tiles.has(`${w.tx},${w.tz}`)).sort((a, b) => a.ring - b.ring);
    }
    if (this.worker) {
      this._pump();
      return;
    }
    const start = performance.now();
    while (this.queue.length && (performance.now() - start < budgetMs || budgetMs <= 0)) {
      const w = this.queue.shift();
      const wantsObjects = Boolean(w.objects || w.farTrees);
      if (this.scenery) {
        // Without a worker: from the tiles the page has (world.js loads them on demand;
        // a tile still loading waits at the back of the queue).
        if (!world.tileReady(w.tx, w.tz)) {
          this.queue.push(w);
          break;
        }
        const g = demTileGeometryData(w.tx, w.tz, w.segments, world.tiles, w.objects ? NEAR_TEXTURE : FAR_TEXTURE);
        this._add(w, g, g && wantsObjects ? demTileObjectsData(w.tx, w.tz, this.shared.maxTrees, !w.objects, world.tiles) : null);
      } else {
        this._add(w, tileGeometryData(w.tx, w.tz, w.segments), wantsObjects ? tileObjectsData(w.tx, w.tz, this.shared.maxTrees, !w.objects) : null);
      }
      if (budgetMs <= 0) break; // budget 0: exactly one tile (tests)
    }
  }

  // Ask the worker for the next tiles (also on each answer, so it stays busy whatever the
  // frame rate).
  _pump() {
    while (this.worker && this.queue.length && this.inFlight.size < MAX_IN_FLIGHT) {
      const w = this.queue.shift(), key = `${w.tx},${w.tz}`;
      if (this.inFlight.get(key) === this._spec(w) || this.tiles.has(key)) continue;
      this.inFlight.set(key, this._spec(w));
      this.worker.postMessage({
        key, spec: this._spec(w), tx: w.tx, tz: w.tz, segments: w.segments, objects: Boolean(w.objects || w.farTrees), far: !w.objects,
        maxTrees: this.shared.maxTrees, scenery: this.scenery?.scenery ?? null, tiles: this.scenery?.tiles ?? null,
        textureSize: w.objects ? NEAR_TEXTURE : FAR_TEXTURE, buildingsMinM: this.scenery ? buildingsMinM(w) : null, ground: Boolean(w.objects),
      });  // fmt: skip
    }
  }

  // A tile from the worker: kept only if it is still wanted with the same detail.
  _built({ key, spec, geometry, objects, features }) {
    if (this.inFlight.get(key) === spec) this.inFlight.delete(key);
    const w = this.wanted.get(key);
    if (w && this._spec(w) === spec && !this.tiles.has(key)) this._add(w, geometry, objects, features);
    this._pump();
  }

  // A tile's detail and world: answers built for another world are dropped.
  _spec(w) {
    return `${specKey(w)}|${this.scenery?.scenery.hash ?? "procedural"}`;
  }

  _add(w, geometryData, objectsData, featuresData = null) {
    if (!geometryData) {
      // No terrain here (outside a real-world region): the ground fallback shows.
      this.tiles.set(`${w.tx},${w.tz}`, { mesh: null, objects: null, segments: w.segments, near: Boolean(w.objects), far: Boolean(w.farTrees) });
      return;
    }
    const geometry = tileGeometry(geometryData);
    const mesh = new THREE.Mesh(geometry, geometryData.texture ? regionMaterial(geometryData) : this.material);
    if (geometry.userData.hasWater) {
      // Water only where this tile has lakes (or a region's sea); elsewhere the land
      // fallback shows through gaps.
      const water = new THREE.Mesh(WATER_QUAD, this.scenery ? SEA : WATER);
      water.position.set((w.tx + 0.5) * TILE_SIZE_M, this.scenery ? SEA_SURFACE_M : WATER_LEVEL_M, (w.tz + 0.5) * TILE_SIZE_M);
      mesh.add(water);
    }
    mesh.receiveShadow = Boolean(this.scenery); // a region's ground takes the sun's shadows
    this.scene.add(mesh);
    this.onChange?.(); // new casters or receivers (the sun's shadow map is drawn again)
    // Measured trees (the canopy height map) replace the land cover's scattered palms.
    const measured = NO_TREES ? null : featuresData?.trees?.palm ? featuresData.trees : null;
    const objects = objectsData ? tileObjects(measured ? { ...objectsData, palms: null, bushes: null } : objectsData, this.shared, !w.objects) : null;
    let trees = null;
    if (measured?.palm.length) {
      trees = treeBatch(measured);
      this.scene.add(trees);
      this.onChange?.(); // casters for the sun's shadow map
    }
    if (objects) this.scene.add(objects);
    if (featuresData) mesh.add(featureMeshes({ ...featuresData, trees: null }, this.featureMats)); // the tile's own geometries (disposed with it)
    this.tiles.set(`${w.tx},${w.tz}`, { mesh, objects, trees, segments: w.segments, near: Boolean(w.objects), far: Boolean(w.farTrees) });
  }

  get pending() {
    return this.queue.length + this.inFlight.size;
  }

  _drop(key) {
    const t = this.tiles.get(key);
    if (t.mesh) {
      this.scene.remove(t.mesh); // its water quad (a child) shares geometry and material
      t.mesh.geometry.dispose();
      if (t.mesh.material !== this.material) {
        t.mesh.material.map?.image?.close?.(); // an imagery ImageBitmap
        t.mesh.material.map?.dispose(); // a region tile's own textures and material
        if (t.mesh.material.userData.shore !== NO_SHORE) t.mesh.material.userData.shore?.dispose();
        t.mesh.material.dispose();
      }
    }
    if (t.trees) {
      this.scene.remove(t.trees);
      t.trees.dispose(); // its own buffers (the source geometries and material are shared)
    }
    if (t.objects) {
      this.scene.remove(t.objects);
      t.objects.traverse((o) => o.isInstancedMesh && o.dispose());
    }
    t.mesh?.traverse((o) => o !== t.mesh && (o.isMesh || o.isPoints) && o.geometry !== WATER_QUAD && o.geometry.dispose());
    this.tiles.delete(key);
  }
}
