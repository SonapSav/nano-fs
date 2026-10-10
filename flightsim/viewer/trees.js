// Trees of a real-world region: placed and sized from the canopy height map (the features
// file's "trees": [x, z, height], world/scenery_trees.py). From 5 m up a date palm (the
// region's common tree), lower a round broadleaf tree. Drawn per 500 m cell as instanced
// meshes, in two levels of detail by the cell's distance from the camera: near, palms with
// a ringed trunk and drooping fronds (cast shadows); far, a few flat fronds. Visual only.
//
// The fronds are strips textured with a pinnate leaf drawn here (midrib and leaflets on a
// transparent ground, alpha-tested), the trunk with bark from the same texture's right
// part: one material. Shapes, colours and sizes: project choices, by eye (date palms
// 8-20 m, crowns 6-9 m across, 15-25 fronds; here fewer, wider).

import * as THREE from "three";

export const NEAR_M = 800; // trees nearer than this: detailed
export const FAR_M = 2500; // ...then the simple ones; beyond, none (a palm is a pixel or two)
const AROUND_M = 300; // always drawn (the view turns quickly close by)
const CONE_COS = Math.cos((80 * Math.PI) / 180); // drawn within 80 deg of the view's heading
const PALM_FROM_M = 5;
const BASE_H = 10; // the geometry's height (scaled per tree)

// --- The texture: frond (u 0..0.75) and bark (u 0.75..1) ---------------------------------------

let texture = null;
function frondTexture() {
  if (texture) return texture;
  const W = 512, H = 128, c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const g = c.getContext("2d");
  const fw = W * 0.75;
  // Leaflets: pairs along the midrib (u = along the frond, base at 0), angled toward the
  // tip, longest in the middle, a little random in angle and shade (seeded).
  let s = 7;
  const rnd = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  for (let i = 0; i < 70; i++) {
    const t = 0.06 + (i / 70) * 0.92, x = t * fw;
    const len = (H / 2) * 0.95 * Math.sin(Math.PI * Math.min(1, t * 1.1)) ** 0.7;
    for (const side of [-1, 1]) {
      const a = (side * (0.55 + 0.25 * rnd())) - 0.0;
      const ex = x + Math.cos(a) * len * 0.9 + 6, ey = H / 2 + side * Math.abs(Math.sin(a)) * len;
      const shade = 0.75 + 0.35 * rnd();
      g.strokeStyle = `rgb(${Math.round(92 * shade)}, ${Math.round(122 * shade)}, ${Math.round(62 * shade)})`;
      g.lineWidth = 2.4;
      g.beginPath();
      g.moveTo(x, H / 2);
      g.quadraticCurveTo((x + ex) / 2, (H / 2 + ey) / 2 + side * 3, ex, ey);
      g.stroke();
    }
  }
  g.strokeStyle = "rgb(150, 140, 90)"; // midrib
  g.lineWidth = 3;
  g.beginPath();
  g.moveTo(0, H / 2);
  g.lineTo(fw, H / 2);
  g.stroke();
  // Bark: brown with darker rings (leaf bases).
  for (let y = 0; y < H; y++) {
    const ring = 0.75 + 0.25 * Math.abs(Math.sin((y / H) * Math.PI * 6));
    g.fillStyle = `rgb(${Math.round(118 * ring)}, ${Math.round(96 * ring)}, ${Math.round(72 * ring)})`;
    g.fillRect(fw, y, W - fw, 1);
  }
  texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

// --- Geometry ------------------------------------------------------------------------------------

function builder() {
  const pos = [], uv = [], idx = [];
  return {
    pos, uv, idx,
    v(x, y, z, u, w) {
      pos.push(x, y, z);
      uv.push(u, w);
      return pos.length / 3 - 1;
    },
    geometry() {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
      g.setIndex(idx);
      g.computeVertexNormals();
      return g;
    },
  };
}

// A date palm BASE_H tall, its base at the origin: a slightly leaning, tapering trunk and
// `fronds` drooping fronds (each `along` segments, folded along the midrib).
function palmGeometry({ sides = 5, fronds = 9, along = 3, folded = true, rings = 3 } = {}) {
  const b = builder();
  const top = BASE_H * 0.82, lean = 0.25;
  for (let k = 0; k <= rings; k++) {
    const t = k / rings, y = t * top, r = 0.34 - 0.12 * t, ox = lean * t * t;
    for (let j = 0; j <= sides; j++) {
      const a = (j / sides) * Math.PI * 2;
      b.v(ox + Math.cos(a) * r, y, Math.sin(a) * r, 0.76 + 0.23 * (j / sides), t);
    }
  }
  for (let k = 0; k < rings; k++) for (let j = 0; j < sides; j++) {
    const a = k * (sides + 1) + j, c = a + sides + 1;
    b.idx.push(a, c, a + 1, a + 1, c, c + 1);
  }
  // Fronds from the crown: rising, then drooping; length 3.8-4.6 m.
  for (let f = 0; f < fronds; f++) {
    const az = (f / fronds) * Math.PI * 2 + (f % 2) * 0.3, up = f % 3 === 0 ? 0.55 : f % 3 === 1 ? 0.2 : -0.1;
    const L = 4.2 + 0.4 * Math.sin(f * 2.3), half = 0.9;
    const dx = Math.cos(az), dz = Math.sin(az), sx = -dz, sz = dx;
    const row = [];
    for (let k = 0; k <= along; k++) {
      const t = k / along;
      const hx = lean + dx * L * t, hz = dz * L * t;
      const hy = top + L * (up * t - 0.75 * t * t) + 0.1;
      const w = half * Math.sin(Math.PI * (0.12 + 0.88 * t)) ** 0.6;
      const keel = folded ? 0.18 * w : 0;
      const u = 0.005 + 0.74 * t;
      row.push([
        b.v(hx + sx * w, hy + keel, hz + sz * w, u, 0.02),
        b.v(hx, hy, hz, u, 0.5),
        b.v(hx - sx * w, hy + keel, hz - sz * w, u, 0.98),
      ]);
    }
    for (let k = 0; k < along; k++) {
      const [a0, a1, a2] = row[k], [c0, c1, c2] = row[k + 1];
      b.idx.push(a0, c0, a1, a1, c0, c1, a1, c1, a2, a2, c1, c2);
    }
  }
  return b.geometry();
}

// A round broadleaf tree BASE_H tall: a short trunk and a lumpy crown (bark and leaf
// colours from the texture's solid parts).
function roundTreeGeometry(detail) {
  const crown = new THREE.IcosahedronGeometry(1, detail);
  const p = crown.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i), k = 1 + 0.12 * Math.sin(x * 5.1 + z * 3.7) * Math.cos(y * 4.3);
    p.setXYZ(i, x * 0.45 * BASE_H * k, BASE_H * (0.62 + 0.36 * y * k), z * 0.45 * BASE_H * k);
  }
  const uv = new Float32Array(p.count * 2).fill(0.5);
  for (let i = 0; i < p.count; i++) uv[2 * i] = 0.3; // a leafy texel near the midrib
  crown.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  const trunk = new THREE.CylinderGeometry(0.18, 0.28, BASE_H * 0.35, 5, 1, true).translate(0, BASE_H * 0.175, 0);
  const tuv = trunk.attributes.uv;
  for (let i = 0; i < tuv.count; i++) tuv.setX(i, 0.76 + 0.23 * tuv.getX(i));
  const g = mergeTwo(crown.index ? crown.toNonIndexed() : crown, trunk.toNonIndexed());
  g.setIndex([...Array(g.attributes.position.count).keys()]); // indexed, as the palms (one batch)
  g.computeVertexNormals();
  return g;
}

function mergeTwo(a, b) {
  const g = new THREE.BufferGeometry();
  for (const name of ["position", "uv"]) {
    const x = a.attributes[name], y = b.attributes[name];
    const arr = new Float32Array(x.array.length + y.array.length);
    arr.set(x.array);
    arr.set(y.array, x.array.length);
    g.setAttribute(name, new THREE.BufferAttribute(arr, x.itemSize));
  }
  return g;
}

let shared = null;
function sharedParts() {
  if (shared) return shared;
  const material = new THREE.MeshLambertMaterial({ map: frondTexture(), alphaTest: 0.45, side: THREE.DoubleSide });
  shared = {
    material,
    near: { palm: palmGeometry(), round: roundTreeGeometry(1) },
    far: { palm: palmGeometry({ sides: 3, fronds: 3, along: 1, folded: false, rings: 1 }), round: roundTreeGeometry(0) },
  };
  return shared;
}

// --- Per tile -----------------------------------------------------------------------------------

// A tile's trees (Float32Array [x, groundY, z, height] x n, from the tile worker) as one
// batched mesh: one draw call; each tree an instance whose shape (near, far) and
// visibility update() sets by its distance from the camera.
export function treeBatch(data) {
  const parts = sharedParts();
  const n = data.length / 4;
  const geos = [parts.near.palm, parts.near.round, parts.far.palm, parts.far.round];
  const verts = geos.reduce((t, g) => t + g.attributes.position.count, 0), idx = geos.reduce((t, g) => t + g.index.count, 0);
  const batch = new THREE.BatchedMesh(n, verts, idx, parts.material);
  const [nearPalm, nearRound, farPalm, farRound] = geos.map((g) => batch.addGeometry(g));
  batch.perObjectFrustumCulled = false; // (thousands of small instances: culled with the tile)
  batch.sortObjects = false;
  // No sun shadows of their own: the 1 m imagery shows the real trees' shadows (and in the
  // shadow pass thousands of trees cost 10-45 ms whenever the map is drawn again).
  batch.castShadow = false;
  batch.receiveShadow = true;
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), p = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0);
  const tint = new THREE.Color();
  const trees = new Float32Array(n * 2), palm = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const x = data[4 * i], y = data[4 * i + 1], z = data[4 * i + 2], h = data[4 * i + 3];
    palm[i] = h >= PALM_FROM_M ? 1 : 0;
    const id = batch.addInstance(palm[i] ? farPalm : farRound);
    const hash = (Math.abs(Math.sin(x * 12.9898 + z * 78.233)) * 43758.5453) % 1;
    q.setFromAxisAngle(up, hash * Math.PI * 2);
    const k = h / BASE_H, w = palm[i] ? Math.min(1.25, Math.max(0.75, 0.8 + 0.04 * h)) : k;
    batch.setMatrixAt(id, m.compose(p.set(x, y - 0.2, z), q, sc.set(w, k, w)));
    batch.setColorAt(id, tint.setRGB(0.85 + 0.3 * hash, 0.9 + 0.2 * ((hash * 7.3) % 1), 0.85 + 0.2 * ((hash * 3.1) % 1)));
    batch.setVisibleAt(id, false);
    trees[2 * i] = x;
    trees[2 * i + 1] = z;
  }
  batch.userData.trees = { xz: trees, palm, ids: { nearPalm, nearRound, farPalm, farRound }, key: null };
  return batch;
}

// Each frame: when the camera has moved to another 250 m step or turned to another 15 deg
// step, each tree near it takes the detailed shape, farther ones the simple one; those
// behind the view (beyond AROUND_M) and the farthest are hidden.
export function updateTreeBatch(batch, camX, camZ, headX = 0, headZ = -1) {
  const t = batch.userData.trees;
  const step = Math.round(Math.atan2(headX, -headZ) / (Math.PI / 12));
  const key = `${Math.round(camX / 250)},${Math.round(camZ / 250)},${step}`;
  if (key === t.key) return;
  t.key = key;
  const a = (step * Math.PI) / 12, hx = Math.sin(a), hz = -Math.cos(a);
  const { nearPalm, nearRound, farPalm, farRound } = t.ids;
  for (let i = 0; i < t.palm.length; i++) {
    const dx = t.xz[2 * i] - camX, dz = t.xz[2 * i + 1] - camZ;
    const d = Math.max(Math.abs(dx), Math.abs(dz));
    const ahead = d < AROUND_M || (dx * hx + dz * hz) > CONE_COS * Math.hypot(dx, dz);
    const near = d < NEAR_M;
    batch.setVisibleAt(i, ahead && d < FAR_M);
    batch.setGeometryIdAt(i, t.palm[i] ? (near ? nearPalm : farPalm) : near ? nearRound : farRound);
  }
}
