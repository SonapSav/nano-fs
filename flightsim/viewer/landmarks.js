// Landmarks of a real-world region (its landmarks.json: OSM footprint, centre, long axis,
// ground height and the dimensions from the region file; flightsim/world/scenery_build.py),
// modelled simply so the city is recognisable: a grand mosque (white base, minarets, a
// main dome toward the qibla and smaller domes), a leaning tower, a flat dome on four
// piers, a central dome on a building. World frame: x east, y up, z south. Visual only.

import * as THREE from "three";

const WHITE = new THREE.MeshLambertMaterial({ color: 0xf2f0ea });
const SAND = new THREE.MeshLambertMaterial({ color: 0xd8bb8e });
const GOLD = new THREE.MeshLambertMaterial({ color: 0xc8a24a });
const GLASS = new THREE.MeshLambertMaterial({ color: 0x7f97a8 });
const STEEL = new THREE.MeshLambertMaterial({ color: 0xc4c8cc, side: THREE.DoubleSide });

// A footprint ring [x0, z0, ...] extruded from y0 to y1, the top shifted by (sx, sz).
function prism(ring, y0, y1, material, sx = 0, sz = 0) {
  const pts = [];
  for (let k = 0; k < ring.length; k += 2) pts.push(new THREE.Vector2(ring[k], -ring[k + 1])); // shape in (x, -z)
  const geo = new THREE.ExtrudeGeometry(new THREE.Shape(pts), { depth: y1 - y0, bevelEnabled: false });
  geo.rotateX(-Math.PI / 2); // shape plane to the ground, extrusion up
  geo.translate(0, y0, 0);
  if (sx || sz) {
    const p = geo.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const k = (p.getY(i) - y0) / (y1 - y0);
      p.setX(i, p.getX(i) + sx * k);
      p.setZ(i, p.getZ(i) + sz * k);
    }
    geo.computeVertexNormals();
  }
  return new THREE.Mesh(geo, material);
}

// A dome: a drum up to `springY`, then a half-ellipsoid to `topY`, radius r, at (x, z).
function dome(x, z, r, springY, topY, baseY, material, finial = true) {
  const g = new THREE.Group();
  const drum = new THREE.Mesh(new THREE.CylinderGeometry(r, r, springY - baseY, 24), material);
  drum.position.set(x, (springY + baseY) / 2, z);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(r, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), material);
  cap.scale.set(1, (topY - springY) / r, 1);
  cap.position.set(x, springY, z);
  g.add(drum, cap);
  if (finial) {
    const f = new THREE.Mesh(new THREE.ConeGeometry(r * 0.06, r * 0.4, 8), GOLD);
    f.position.set(x, topY + r * 0.2, z);
    g.add(f);
  }
  return g;
}

// Unit vectors of a bearing (degrees from north, clockwise) in the world frame.
const along = (deg) => [Math.sin((deg * Math.PI) / 180), -Math.cos((deg * Math.PI) / 180)];

function grandMosque(m) {
  const g = new THREE.Group(), y0 = m.ground_m;
  g.add(prism(m.ring, y0, y0 + m.base_m, WHITE));
  const [cx, cz] = m.centre;
  // Corners of the footprint's oriented box: the minarets.
  const [ux, uz] = along(m.axis_deg), [vx, vz] = [-uz, ux];
  let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
  for (let k = 0; k < m.ring.length; k += 2) {
    const dx = m.ring[k] - cx, dz = m.ring[k + 1] - cz, a = dx * ux + dz * uz, b = dx * vx + dz * vz;
    [a0, a1, b0, b1] = [Math.min(a0, a), Math.max(a1, a), Math.min(b0, b), Math.max(b1, b)];
  }
  for (const [a, b] of [[a0, b0], [a0, b1], [a1, b0], [a1, b1]]) {
    const x = cx + ux * (a - Math.sign(a) * 6) + vx * (b - Math.sign(b) * 6), z = cz + uz * (a - Math.sign(a) * 6) + vz * (b - Math.sign(b) * 6);
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(3, 4, m.minaret_m - 8, 12), WHITE);
    shaft.position.set(x, y0 + (m.minaret_m - 8) / 2, z);
    const top = new THREE.Mesh(new THREE.ConeGeometry(3, 8, 12), GOLD);
    top.position.set(x, y0 + m.minaret_m - 4, z);
    g.add(shaft, top);
  }
  // The prayer hall toward the qibla: the main dome there, smaller domes beside it.
  const [qx, qz] = along(m.qibla_deg ?? 258);
  let reach = 0;
  for (let k = 0; k < m.ring.length; k += 2) reach = Math.max(reach, (m.ring[k] - cx) * qx + (m.ring[k + 1] - cz) * qz);
  const hx = cx + qx * reach * 0.55, hz = cz + qz * reach * 0.55, r = m.dome_diameter_m / 2;
  g.add(dome(hx, hz, r, y0 + m.dome_top_m - r * 1.25, y0 + m.dome_top_m, y0 + m.base_m, WHITE));
  const [px, pz] = [-qz, qx]; // along the hall
  for (const s of [-2, -1, 1, 2]) {
    const x = hx + px * s * 28, z = hz + pz * s * 28, rs = Math.abs(s) === 1 ? 9 : 7;
    g.add(dome(x, z, rs, y0 + m.base_m + 14, y0 + m.base_m + 14 + rs * 1.1, y0 + m.base_m, WHITE)); // project choice sizes
  }
  return g;
}

function leaningTower(m) {
  const [dx, dz] = along(m.lean_toward_deg);
  const shift = Math.tan((m.lean_deg * Math.PI) / 180) * m.height_m;
  return prism(m.ring, m.ground_m, m.ground_m + m.height_m, GLASS, dx * shift, dz * shift);
}

function flatDome(m) {
  const g = new THREE.Group(), [cx, cz] = m.centre, r = m.dome_diameter_m / 2;
  const [ux, uz] = along(m.axis_deg), [vx, vz] = [-uz, ux], h = m.pier_spacing_m / 2, top = m.ground_m + m.dome_top_m;
  // A shallow spherical cap (the sphere of radius R through the rim r: rise = 0.12 r,
  // project choice) on a 4 m deep rim, on four piers.
  const rise = 0.12 * r, R = (r * r + rise * rise) / (2 * rise), theta = Math.asin(r / R);
  const edge = top - rise;
  const shell = new THREE.Mesh(new THREE.SphereGeometry(R, 64, 8, 0, Math.PI * 2, 0, theta), STEEL);
  shell.position.set(cx, top - R, cz);
  const rim = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 4, 64, 1, true), STEEL);
  rim.position.set(cx, edge - 2, cz);
  for (const [a, b] of [[-h, -h], [-h, h], [h, -h], [h, h]]) {
    const pier = new THREE.Mesh(new THREE.BoxGeometry(5, edge - 4 - m.ground_m, 5), WHITE);
    pier.position.set(cx + ux * a + vx * b, (m.ground_m + edge - 4) / 2, cz + uz * a + vz * b);
    g.add(pier);
  }
  g.add(shell, rim);
  return g;
}

function centralDome(m) {
  const [cx, cz] = m.centre, r = m.dome_diameter_m / 2, base = m.ground_m + m.roof_m;
  return dome(cx, cz, r, m.ground_m + m.dome_top_m - r * 1.1, m.ground_m + m.dome_top_m, base, SAND);
}

const BUILDERS = { grand_mosque: grandMosque, leaning_tower: leaningTower, flat_dome: flatDome, central_dome: centralDome };

export class Landmarks {
  constructor(scene) {
    this.group = new THREE.Group();
    scene.add(this.group);
  }

  build(list) {
    this.clear();
    for (const m of list ?? []) {
      const make = BUILDERS[m.kind];
      if (make) this.group.add(make(m));
    }
  }

  clear() {
    this.group.traverse((o) => o.geometry?.dispose());
    this.group.clear();
  }
}
