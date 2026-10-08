// The circuit's traffic pattern geometry (no three.js: used by pattern.js for the 3D
// ribbon and by the moving map). Runway frame: along = metres past the threshold, cross =
// metres right of the centreline, height = metres above the runway; left traffic.

export const UPWIND_PAST_END_M = 600; // indicative: where the crosswind turn usually happens

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

