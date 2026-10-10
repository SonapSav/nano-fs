// Trees' per-instance data, computed in the tile worker (no three.js here): for each tree
// ([x, groundY, z, height] x n) whether it is a palm, its matrix (column-major, as
// three.js) and its tint (RGBA). trees.js builds the tile's batched mesh from it without
// per-tree work on the page's main thread. Sizes and tints: project choices (trees.js).

export const PALM_FROM_M = 5;
export const BASE_H = 10; // the tree geometries' height (scaled per tree)

export function treeInstances(data) {
  const n = data.length / 4;
  const matrices = new Float32Array(n * 16), colors = new Float32Array(n * 4), palm = new Uint8Array(n), xz = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) {
    const x = data[4 * i], y = data[4 * i + 1], z = data[4 * i + 2], h = data[4 * i + 3];
    const isPalm = h >= PALM_FROM_M;
    palm[i] = isPalm ? 1 : 0;
    const hash = (Math.abs(Math.sin(x * 12.9898 + z * 78.233)) * 43758.5453) % 1;
    const a = hash * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
    const k = h / BASE_H, w = isPalm ? Math.min(1.25, Math.max(0.75, 0.8 + 0.04 * h)) : k;
    // Translation x rotation about y x scale (w, k, w).
    matrices.set([w * c, 0, -w * s, 0, 0, k, 0, 0, w * s, 0, w * c, 0, x, y - 0.2, z, 1], 16 * i);
    colors.set([0.85 + 0.3 * hash, 0.9 + 0.2 * ((hash * 7.3) % 1), 0.85 + 0.2 * ((hash * 3.1) % 1), 1], 4 * i);
    xz[2 * i] = x;
    xz[2 * i + 1] = z;
  }
  return { matrices, colors, palm, xz };
}
