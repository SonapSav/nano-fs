// Chase camera defaults and re-centring the view (pure, no three.js: tested with Node).

export const CHASE_DISTANCE_M = 22; // default chase camera distance (the aircraft is 8.2 m long, 10.9 m span)
export const DEFAULT_ORBIT = { azimuth: 0, elevation: 0.18, distance: CHASE_DISTANCE_M };
// Re-centring is a short eased move rather than a cut, so the eye can follow where the
// view went, and short enough not to become a long sweeping pan.
export const RECENTRE_S = 0.5;

// The view a fraction k (0..1) of the way from `from` ({orbit, head}) back to the default:
// smoothstep easing (gentle start and stop), azimuth the shortest way round, distance
// evenly in log scale (zooming feels uniform).
export function recentreView(from, k) {
  const e = k * k * (3 - 2 * k);
  const lerp = (a, b) => a + (b - a) * e;
  const az = from.orbit.azimuth - 2 * Math.PI * Math.round(from.orbit.azimuth / (2 * Math.PI));
  return {
    orbit: {
      azimuth: lerp(az, DEFAULT_ORBIT.azimuth),
      elevation: lerp(from.orbit.elevation, DEFAULT_ORBIT.elevation),
      distance: Math.exp(lerp(Math.log(from.orbit.distance), Math.log(DEFAULT_ORBIT.distance))),
    },
    head: { yaw: lerp(from.head.yaw, 0), pitch: lerp(from.head.pitch, 0) },
  };
}
