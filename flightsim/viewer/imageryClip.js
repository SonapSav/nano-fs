// A real-world region's high-resolution imagery (flightsim/world/scenery_hires.py: 1 m
// and 4 m chunks, `hires.json`) around the camera, over the tiles' 10 m imagery.
//
// Each level is one fixed texture of SLOTS x SLOTS chunks (a clipmap): the chunks of the
// window around the camera, each in the slot (cx mod SLOTS, cz mod SLOTS), so the window
// moves by replacing a row or column of chunks and the texture wraps (REPEAT); level 0
// covers 4 km at 1 m, level 1 16 km at 4 m. A small index texture says which chunk each
// slot holds; the ground's shader (terrain.js regionMaterial) uses a slot's pixels only
// where it holds the chunk under the fragment, fades each level out before its window's
// edge, and falls back to the tile's imagery elsewhere (and where no chunk exists:
// outside the covered area). Mipmaps are made per chunk (no level below a chunk's 1 px),
// so slots never blend into one another. Graphics memory is fixed: 2 x 4096^2 RGBA plus
// mipmaps, about 180 MB.

import * as THREE from "three";
import { nightLevel } from "./nightLights.js";

export const SLOTS = 8;
const MIP_LEVELS = 10; // 512 px chunks down to 1 px
export const FADE_M = [[1300, 1700], [5500, 7000]]; // per level: fade between (Chebyshev distance from the camera; window half-width >= 3.5 chunks)
const MAX_FETCHES = 6;
const MAX_UPLOADS_PER_FRAME = 2;

function placeholder() {
  const t = new THREE.DataTexture(new Uint8Array([0, 0, 0, 0]), 1, 1);
  t.needsUpdate = true;
  return t;
}

const index = new THREE.DataTexture(new Float32Array(SLOTS * SLOTS * 2 * 4), SLOTS, SLOTS * 2, THREE.RGBAFormat, THREE.FloatType);
index.magFilter = index.minFilter = THREE.NearestFilter;
index.needsUpdate = true;

// Shared by every region tile's material (terrain.js).
export const clipUniforms = {
  clip0: { value: placeholder() },
  clip1: { value: placeholder() },
  clipIndex: { value: index },
  clipOn: { value: 0 },
  clipChunk: { value: new THREE.Vector2(512, 2048) }, // chunk size per level (m)
  clipCover: { value: placeholder() }, // where the imagery covers (hires/cover.png; 0: nowhere yet)
  clipCoverRect: { value: new THREE.Vector3(0, 0, 1) }, // its west and north edges and size (m)
};

export const CLIP_GLSL = `
uniform sampler2D clip0;
uniform sampler2D clip1;
uniform sampler2D clipIndex;
uniform float clipOn;
uniform vec2 clipChunk;
uniform sampler2D clipCover;
uniform vec3 clipCoverRect;
// Whether the level's slot for world (x, z) holds that chunk.
float clipHas(int level, float chunkM, vec2 p) {
  vec2 c = floor(p / chunkM);
  vec2 slot = mod(c, ${SLOTS}.0);
  vec4 idx = texelFetch(clipIndex, ivec2(slot) + ivec2(0, level * ${SLOTS}), 0);
  return idx.b * (1.0 - step(0.5, abs(idx.r - c.x) + abs(idx.g - c.y)));
}
// The level's colour at world (x, z) and whether its slot holds that chunk (a).
vec4 clipSample(sampler2D tex, int level, float chunkM, vec2 p) {
  return vec4(texture2D(tex, p / (chunkM * ${SLOTS}.0)).rgb, clipHas(level, chunkM, p));
}
// How much the high-resolution imagery shows at world (x, z) from the camera: its levels
// loaded and not faded out, times the covered area's weight.
float clipShown(vec2 p) {
  if (clipOn < 0.5) return 0.0;
  vec2 uv = (p - clipCoverRect.xy) / clipCoverRect.z;
  if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0)))) return 0.0;
  float d = max(abs(p.x - cameraPosition.x), abs(p.y - cameraPosition.z));
  float w1 = clipHas(1, clipChunk.y, p) * (1.0 - smoothstep(${FADE_M[1][0]}.0, ${FADE_M[1][1]}.0, d));
  float w0 = clipHas(0, clipChunk.x, p) * (1.0 - smoothstep(${FADE_M[0][0]}.0, ${FADE_M[0][1]}.0, d));
  return max(w0, w1) * texture2D(clipCover, uv).r;
}`;

// OpenStreetMap's drawn roads and railways give way to the imagery, which shows the real
// ones: a fragment is dropped where the imagery shows (dithered where it fades, so they
// fade in and out without sorting transparent surfaces).
export function hideUnderImagery(material) {
  const previous = material.onBeforeCompile;
  material.onBeforeCompile = (shader, renderer) => {
    previous?.call(material, shader, renderer);
    Object.assign(shader.uniforms, clipUniforms, { nightLevel });
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec2 vClipXZ;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvClipXZ = (modelMatrix * vec4(position, 1.0)).xz;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>\nvarying vec2 vClipXZ;\nuniform float nightLevel;\n${CLIP_GLSL}`)
      .replace(
        "#include <clipping_planes_fragment>",
        `#include <clipping_planes_fragment>
if (clipShown(vClipXZ) * (1.0 - nightLevel) > fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))))) discard; // at night the lit roads show`,
      );
  };
  const previousKey = material.hasOwnProperty("customProgramCacheKey") ? material.customProgramCacheKey.bind(material) : () => "";
  material.customProgramCacheKey = () => `${previousKey()}|underImagery`;
  return material;
}

// After three's map_fragment: diffuseColor holds the tile imagery's colour (linear).
export const CLIP_FRAGMENT = `
if (clipOn > 0.5) {
  vec2 clipP = vWaterPos.xz;
  float clipD = max(abs(clipP.x - cameraPosition.x), abs(clipP.y - cameraPosition.z));
  vec4 c1 = clipSample(clip1, 1, clipChunk.y, clipP);
  diffuseColor.rgb = mix(diffuseColor.rgb, c1.rgb, c1.a * (1.0 - smoothstep(${FADE_M[1][0]}.0, ${FADE_M[1][1]}.0, clipD)));
  vec4 c0 = clipSample(clip0, 0, clipChunk.x, clipP);
  diffuseColor.rgb = mix(diffuseColor.rgb, c0.rgb, c0.a * (1.0 - smoothstep(${FADE_M[0][0]}.0, ${FADE_M[0][1]}.0, clipD)));
}`;

export class ImageryClip {
  constructor(renderer) {
    this.renderer = renderer;
    this.levels = null; // per level: {chunkM, chunks: Map "cx,cz" -> hash, slots: [{key, state}], texture}
    this.scenery = null;
    this.fetches = 0;
    this.ready = []; // decoded chunks waiting for upload: {level, slot, key, mips}
  }

  // The region's hires.json (null: none, or a procedural world).
  set(scenery, hires) {
    this._dispose();
    this.scenery = hires ? scenery : null;
    clipUniforms.clipOn.value = 0;
    if (!hires) return;
    this.chunkPx = hires.chunk_px;
    this.levels = hires.levels.map((lv, level) => ({
      level,
      chunkM: lv.chunk_m,
      dir: `hires/l${level}`,
      chunks: new Map(Object.entries(lv.chunks)),
      slots: Array.from({ length: SLOTS * SLOTS }, () => ({ key: null, want: null })),
      texture: null,
    }));
    clipUniforms.clipChunk.value.set(this.levels[0].chunkM, this.levels[1].chunkM);
    this._createTextures();
    this.cover = hires.cover ?? null;
    if (this.cover) this._loadCover(this.levels);
  }

  async _loadCover(levels) {
    const c = this.cover;
    try {
      const r = await fetch(`scenery/${encodeURIComponent(this.scenery.name)}/${c.file}?h=${c.sha}`);
      if (!r.ok) throw new Error(`${r.status}`);
      const bitmap = await createImageBitmap(await r.blob(), { premultiplyAlpha: "none", colorSpaceConversion: "none" });
      if (levels !== this.levels) return bitmap.close();
      const t = new THREE.Texture(bitmap);
      t.flipY = false; // rows go south, as the chunks
      t.generateMipmaps = false;
      t.minFilter = t.magFilter = THREE.LinearFilter;
      t.needsUpdate = true;
      clipUniforms.clipCover.value = t;
      clipUniforms.clipCoverRect.value.set(c.x0_m, c.z0_m, c.size_m);
    } catch (e) {
      console.warn(`hires imagery cover: ${e.message}`);
    }
  }

  // A new WebGL context (scene.js rebuildRenderer): the textures again, then the chunks.
  setRenderer(renderer) {
    for (const lv of this.levels ?? []) lv.gl = null; // gone with the old context
    this.renderer = renderer;
    if (!this.levels) return;
    const scenery = this.scenery;
    const hires = { chunk_px: this.chunkPx, cover: this.cover, levels: this.levels.map((l) => ({ chunk_m: l.chunkM, chunks: Object.fromEntries(l.chunks) })) };
    this.set(scenery, hires);
  }

  _createTextures() {
    const gl = this.renderer.getContext();
    const aniso = gl.getExtension("EXT_texture_filter_anisotropic");
    for (const lv of this.levels) {
      const size = SLOTS * this.chunkPx;
      const t = gl.createTexture();
      this.renderer.state.bindTexture(gl.TEXTURE_2D, t);
      gl.texStorage2D(gl.TEXTURE_2D, MIP_LEVELS, gl.SRGB8_ALPHA8, size, size);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAX_LEVEL, MIP_LEVELS - 1);
      if (aniso) gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(16, gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT))); // sharp at low angles
      lv.gl = t;
      lv.texture = new THREE.ExternalTexture(t);
    }
    clipUniforms.clip0.value = this.levels[0].texture;
    clipUniforms.clip1.value = this.levels[1].texture;
    index.image.data.fill(0);
    index.needsUpdate = true;
  }

  _dispose() {
    if (this.levels) {
      const gl = this.renderer.getContext();
      for (const lv of this.levels) {
        if (lv.gl && !gl.isContextLost()) gl.deleteTexture(lv.gl);
        lv.texture?.dispose();
      }
    }
    this.levels = null;
    this.ready = [];
    clipUniforms.clip0.value = placeholder();
    clipUniforms.clip1.value = placeholder();
    clipUniforms.clipCover.value.dispose();
    clipUniforms.clipCover.value = placeholder();
    clipUniforms.clipOn.value = 0;
  }

  // Each frame: the window around the camera; fetch what is missing (nearest first) and
  // upload a few decoded chunks.
  update(camX, camZ) {
    if (!this.levels) return;
    for (const lv of this.levels) {
      const want = [];
      for (const { cx, cz, slot: s } of clipWindow(camX, camZ, lv.chunkM)) {
        const key = `${cx},${cz}`;
        const slot = lv.slots[s];
        slot.want = lv.chunks.has(key) ? key : null;
        if (slot.want && slot.key !== key && slot.loading !== key) want.push({ slot, key, cx, cz, d: Math.hypot(cx + 0.5 - camX / lv.chunkM, cz + 0.5 - camZ / lv.chunkM) });
      }
      want.sort((a, b) => a.d - b.d);
      for (const w of want) {
        if (this.fetches >= MAX_FETCHES) break;
        this._fetch(lv, w);
      }
    }
    for (let n = 0; n < MAX_UPLOADS_PER_FRAME && this.ready.length; n++) this._upload(this.ready.shift());
  }

  async _fetch(lv, { slot, key, cx, cz }) {
    const levels = this.levels;
    slot.loading = key;
    this.fetches++;
    try {
      const url = `scenery/${encodeURIComponent(this.scenery.name)}/${lv.dir}/c_${cx}_${cz}.jpg?h=${lv.chunks.get(key)}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error(`${r.status}`);
      const opts = { premultiplyAlpha: "none", colorSpaceConversion: "none" };
      const mips = [await createImageBitmap(await r.blob(), opts)];
      for (let m = 1; m < MIP_LEVELS; m++) {
        const px = this.chunkPx >> m;
        mips.push(await createImageBitmap(mips[m - 1], { ...opts, resizeWidth: px, resizeHeight: px, resizeQuality: "medium" }));
      }
      if (levels !== this.levels || slot.want !== key) {
        mips.forEach((b) => b.close());
        return;
      }
      this.ready.push({ lv, slot, key, cx, cz, mips });
    } catch (e) {
      console.warn(`hires imagery ${key}: ${e.message}`);
    } finally {
      this.fetches--;
      if (slot.loading === key) slot.loading = null;
    }
  }

  _upload({ lv, slot, key, cx, cz, mips }) {
    if (lv !== this.levels?.[lv.level] || slot.want !== key) {
      mips.forEach((b) => b.close());
      return;
    }
    const gl = this.renderer.getContext();
    this.renderer.state.bindTexture(gl.TEXTURE_2D, lv.gl);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
    gl.pixelStorei(gl.UNPACK_ROW_LENGTH, 0);
    gl.pixelStorei(gl.UNPACK_SKIP_PIXELS, 0);
    gl.pixelStorei(gl.UNPACK_SKIP_ROWS, 0);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    const sx = mod(cx, SLOTS), sz = mod(cz, SLOTS);
    mips.forEach((b, m) => {
      const px = this.chunkPx >> m;
      gl.texSubImage2D(gl.TEXTURE_2D, m, sx * px, sz * px, gl.RGBA, gl.UNSIGNED_BYTE, b);
      b.close();
    });
    slot.key = key;
    const i = ((lv.level * SLOTS + sz) * SLOTS + sx) * 4;
    index.image.data.set([cx, cz, 1, 0], i);
    index.needsUpdate = true;
    clipUniforms.clipOn.value = 1;
  }
}

const mod = (a, n) => ((a % n) + n) % n;

// The window of SLOTS x SLOTS chunks around the camera (world x, z) and each one's slot:
// centred on the chunk corner nearest the camera, so the camera is at least SLOTS / 2 - 0.5
// chunks from its edges.
export function clipWindow(camX, camZ, chunkM) {
  const ccx = Math.round(camX / chunkM), ccz = Math.round(camZ / chunkM);
  const out = [];
  for (let cz = ccz - SLOTS / 2; cz < ccz + SLOTS / 2; cz++) {
    for (let cx = ccx - SLOTS / 2; cx < ccx + SLOTS / 2; cx++) out.push({ cx, cz, slot: mod(cz, SLOTS) * SLOTS + mod(cx, SLOTS) });
  }
  return out;
}
