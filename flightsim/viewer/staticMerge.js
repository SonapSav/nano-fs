// Static scenery from many small meshes drawn as a few: every opaque, visible mesh of the
// given objects is baked into world space (each object placed by its own matrix) and the
// pieces are joined per material, so a parked aircraft of forty parts, or eight of them,
// cost one draw call per material instead of one per part. For things that never move
// (parked aircraft, ground markings). Visual only.

import * as THREE from "three";

// Is the mesh shown (it and every parent up to the root visible)?
function shown(o) {
  for (let p = o; p; p = p.parent) if (!p.visible) return false;
  return true;
}

// Join non-indexed geometries: the attributes all of them have (same item size), in
// order; mirrored pieces (negative determinant) get their triangles turned back so their
// fronts still face out.
function concat(parts) {
  const names = Object.keys(parts[0].geometry.attributes).filter((n) =>
    parts.every((p) => p.geometry.attributes[n]?.itemSize === parts[0].geometry.attributes[n].itemSize),
  );
  const count = parts.reduce((s, p) => s + p.geometry.attributes.position.count, 0);
  const out = new THREE.BufferGeometry();
  for (const n of names) {
    const size = parts[0].geometry.attributes[n].itemSize, arr = new Float32Array(count * size);
    let o = 0;
    for (const { geometry: g, mirrored } of parts) {
      const a = g.attributes[n], src = a.array;
      if (!mirrored) arr.set(src, o * size);
      else {
        for (let t = 0; t < a.count; t += 3) {
          for (const [to, from] of [[0, 0], [1, 2], [2, 1]]) arr.set(src.subarray((t + from) * size, (t + from + 1) * size), (o + t + to) * size);
        }
      }
      o += a.count;
    }
    out.setAttribute(n, new THREE.BufferAttribute(arr, size));
  }
  return out;
}

// Meshes, one per material, of the objects' opaque visible meshes (placements: [{object,
// matrix}], the matrix applied on top of the object's own). Sprites, points, lines,
// instanced and transparent meshes are left out (lights, glows, propeller discs).
export function mergeByMaterial(placements) {
  const groups = new Map();
  const m = new THREE.Matrix4();
  for (const { object, matrix } of placements) {
    object.updateMatrixWorld(true);
    object.traverse((o) => {
      if (!o.isMesh || o.isInstancedMesh || Array.isArray(o.material) || o.material.transparent || !shown(o)) return;
      m.multiplyMatrices(matrix, o.matrixWorld);
      const g = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry.clone();
      g.applyMatrix4(m);
      if (!groups.has(o.material)) groups.set(o.material, []);
      groups.get(o.material).push({ geometry: g, mirrored: m.determinant() < 0 });
    });
  }
  const meshes = [];
  for (const [material, parts] of groups) {
    const g = concat(parts);
    for (const p of parts) p.geometry.dispose();
    g.computeBoundingSphere();
    meshes.push(new THREE.Mesh(g, material));
  }
  return meshes;
}
