// Traffic pattern drawing for the circuit task: the legs the circuit autopilot flies
// (flightsim/control/circuit.py), as a magenta ribbon with markers where the descent and
// the base turn begin. Display only.
//
// Geometry in the runway frame: along = metres past the threshold, cross = metres right of
// the centreline, height = metres above the runway. Left traffic: the pattern is left of
// the runway (cross < 0). The descent follows the glide path angle measured along the
// remaining pattern path to the aim point, as the autopilot's profile does; the crosswind
// turn point depends on the climb, so the upwind leg is drawn to an indicative point.

import * as THREE from "three";

export const UPWIND_PAST_END_M = 600; // indicative: where the crosswind turn usually happens
const COLOR = 0xd23cc8;

// Pattern path as [along, cross, height] points, and the marker points.
export function patternPath(a, p) {
  const d = p.downwind_offset_m, top = p.height_m, turn = p.height_m - p.crosswind_below_m;
  const tan = Math.tan((a.glide_path_deg * Math.PI) / 180), aim = a.aim_point_m;
  const corner = a.length_m + UPWIND_PAST_END_M;
  const descent = (remaining) => Math.min(top, remaining * tan);
  const points = [
    [a.length_m, 0, turn / 2], // past the departure end, climbing (indicative)
    [corner, 0, turn], // turn crosswind within 300 ft of pattern altitude
    [corner, -d, top], // turn downwind at pattern altitude
    [0, -d, top], // abeam the threshold: begin the descent
    [-d, -d, descent(2 * d + aim)], // base turn, 45 degrees from the threshold
    [-d, 0, descent(d + aim)], // turn final
    [aim, 0, 0], // aim point
  ];
  return { points, markers: { abeam: points[3], base: points[4] } };
}

// A group holding the ribbon and markers, in world coordinates (x east, y up, z south).
export function buildPattern(a, p) {
  const h = (a.heading_deg * Math.PI) / 180;
  const world = ([along, cross, height]) => {
    const n = a.threshold_north_m + along * Math.cos(h) - cross * Math.sin(h);
    const e = a.threshold_east_m + along * Math.sin(h) + cross * Math.cos(h);
    return new THREE.Vector3(e, a.elevation_m + height, -n);
  };
  const { points, markers } = patternPath(a, p);
  const group = new THREE.Group();
  const material = new THREE.MeshBasicMaterial({ color: COLOR, transparent: true, opacity: 0.55 });
  // Crosswind, downwind and base only: upwind runs straight ahead from the runway and final
  // has the glide-path gates; drawn, both would sit in the line of sight close to the ground.
  const path = new THREE.CurvePath();
  for (let i = 2; i < points.length - 1; i++) path.add(new THREE.LineCurve3(world(points[i - 1]), world(points[i])));
  group.add(new THREE.Mesh(new THREE.TubeGeometry(path, 400, 2.0, 6, false), material));
  const markerMaterial = new THREE.MeshBasicMaterial({ color: COLOR, transparent: true, opacity: 0.8 });
  for (const point of Object.values(markers)) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(25, 2, 6, 24), markerMaterial);
    ring.position.copy(world(point));
    ring.rotation.x = Math.PI / 2; // horizontal: seen from above and from the pattern
    group.add(ring);
  }
  return group;
}
