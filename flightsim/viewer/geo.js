// Geodetic latitude/longitude <-> the flat map (metres north and east of the world's
// origin): an exact port of flightsim/world/geo.py (tests compare them). Models: "wgs84"
// (WGS84 ellipsoid, transverse Mercator around the origin's meridian, Krueger series) and
// "sphere" (the original mapping, for flights recorded before 2026-10-08).
// map bearing = true bearing - convergence.

const R_SPHERE_M = 6371000;
const WGS84_A_M = 6378137;
const WGS84_F = 1 / 298.257223563;

const N = WGS84_F / (2 - WGS84_F);
const A = (WGS84_A_M / (1 + N)) * (1 + N ** 2 / 4 + N ** 4 / 64);
const ALPHA = [N / 2 - (2 * N ** 2) / 3 + (5 * N ** 3) / 16, (13 * N ** 2) / 48 - (3 * N ** 3) / 5, (61 * N ** 3) / 240];
const BETA = [N / 2 - (2 * N ** 2) / 3 + (37 * N ** 3) / 96, N ** 2 / 48 + N ** 3 / 15, (17 * N ** 3) / 480];
const DELTA = [2 * N - (2 * N ** 2) / 3 - 2 * N ** 3, (7 * N ** 2) / 3 - (8 * N ** 3) / 5, (56 * N ** 3) / 15];
const C = (2 * Math.sqrt(N)) / (1 + N);
const D2R = Math.PI / 180;

const wrapPi = (a) => {
  const t = (a + Math.PI) % (2 * Math.PI);
  return (t < 0 ? t + 2 * Math.PI : t) - Math.PI;
};

function tmForward(lat, dlon) {
  const s = Math.sin(lat);
  const t = Math.sinh(Math.atanh(s) - C * Math.atanh(C * s));
  const xiP = Math.atan2(t, Math.cos(dlon));
  const etaP = Math.atanh(Math.sin(dlon) / Math.sqrt(1 + t * t));
  let north = xiP, east = etaP, sigma = 1, tau = 0;
  ALPHA.forEach((a, k) => {
    const j = k + 1;
    const c2 = Math.cos(2 * j * xiP), s2 = Math.sin(2 * j * xiP), ch = Math.cosh(2 * j * etaP), sh = Math.sinh(2 * j * etaP);
    north += a * s2 * ch;
    east += a * c2 * sh;
    sigma += 2 * j * a * c2 * ch;
    tau += 2 * j * a * s2 * sh;
  });
  const tl = Math.tan(dlon), r = Math.sqrt(1 + t * t);
  return [A * north, A * east, Math.atan2(tau * r + sigma * t * tl, sigma * r - tau * t * tl)];
}

function tmInverse(north, east) {
  const xi = north / A, eta = east / A;
  let xiP = xi, etaP = eta;
  BETA.forEach((b, k) => {
    const j = k + 1;
    xiP -= b * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta);
    etaP -= b * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta);
  });
  const chi = Math.asin(Math.sin(xiP) / Math.cosh(etaP));
  let lat = chi;
  DELTA.forEach((d, k) => (lat += d * Math.sin(2 * (k + 1) * chi)));
  return [lat, Math.atan2(Math.sinh(etaP), Math.cos(xiP))];
}

export class Geodesy {
  // `world`: the stream's {model, origin_lat_deg, origin_lon_deg}; null: the sphere.
  constructor(world = null) {
    this.model = world?.model ?? "sphere";
    this.lat0 = (world?.origin_lat_deg ?? 0) * D2R;
    this.lon0 = (world?.origin_lon_deg ?? 0) * D2R;
    this.north0 = this.model === "wgs84" ? tmForward(this.lat0, 0)[0] : 0;
  }

  // [north, east] in metres from the origin.
  toMap(latRad, lonRad) {
    if (this.model === "sphere") return [latRad * R_SPHERE_M, lonRad * R_SPHERE_M];
    const [n, e] = tmForward(latRad, wrapPi(lonRad - this.lon0));
    return [n - this.north0, e];
  }

  // [lat, lon] in radians.
  toGeodetic(northM, eastM) {
    if (this.model === "sphere") return [northM / R_SPHERE_M, eastM / R_SPHERE_M];
    const [lat, dlon] = tmInverse(northM + this.north0, eastM);
    return [lat, wrapPi(dlon + this.lon0)];
  }

  // Angle from true north to map north, clockwise (radians).
  convergence(latRad, lonRad) {
    return this.model === "sphere" ? 0 : tmForward(latRad, wrapPi(lonRad - this.lon0))[2];
  }
}
