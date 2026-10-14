// A runway's lights laid out to the standards (no three.js, tested with Node): edge,
// threshold, wing bar, runway end, centreline and touchdown zone lights, approach lighting
// systems, flashing threshold lights and the PAPI, from the runway descriptor
// (runwayGeometry.js) and each landing end's published lighting (the region file's
// `airports`, e.g. from the national AIP's aerodrome ground lights chart). Visual only.
//
// Sources (retrieved 2026-10-14): UK CAA CAP 168 "Licensing of Aerodromes" ed. 10 (2014),
// chapter 6 and appendix 6B, which follows ICAO Annex 14; FAA Aeronautical Information
// Manual 2-1 (sequenced flashers twice a second). Figures they do not give are marked
// "project choice".
//
// Each light is one face: {p: [x, y, z], c: [r, g, b], d: [dx, dz] | null, f: 0 | 1 + phase}.
// `d` is the horizontal direction it shines toward (null: all round): a bidirectional
// fitting is two faces, e.g. an edge light that is white for one landing direction and
// red (before a displaced threshold) for the other. `f` 0 is steady, else the light flashes
// (nightLights.js) at `phase` in its cycle.

export const COLOURS = {
  white: [1.6, 1.5, 1.25],
  green: [0.35, 1.7, 0.6],
  red: [1.7, 0.22, 0.12],
  yellow: [1.7, 1.15, 0.25],
};

export const EDGE_SPACING_M = 60; // CAP 168 6.57: 60 m +/- 6 m
export const CAUTION_M = 600; // 6.58: yellow over the upwind 600 m or a third, the less
export const TDZ = { spacingM: 60, lengthM: 900, innerM: 9, lights: 4, gapM: 1.5 }; // 6.68
export const APPROACH = {
  lengthM: 900, // 6.24: a 900 m line ...
  spacingM: 30, // ... of barrettes every 30 m (CAP 168 figure 6.2)
  barretteGapM: 1.2, // 6.28: centreline barrettes, lights 1.2 m apart (inner 300 m; used throughout, project choice)
  crossbarsM: [150, 300, 450, 600, 750], // 6.24: five crossbars at 150 m intervals ...
  convergeM: 300, // ... their ends on lines meeting the centreline 300 m past the threshold
  crossbar300HalfM: 15, // the 300 m crossbar 30 m wide (6.30)
  crossbarGapM: 2.7, // figure 6.3: 300 m crossbar lights 2.7 m apart
  sideRowM: 270, // 6.28: red side row barrettes over the inner 270 m ...
  sideRowLights: 4, // ... of four lights 1.5 m apart, at the touchdown zone's gauge
  sideRowGapM: 1.5,
  flasherFromM: 330, // sequenced flashers on the centreline beyond the inner 300 m (project choice)
};
export const SIMPLE = { spacingM: 60, crossbarM: 300, crossbarHalfM: 15, crossbarGapM: 2.7 }; // 6.30
export const PAPI = {
  inboardM: 15, // appendix 6B figure 6B.1: the inner unit 15 m from the runway edge ...
  spacingM: 9, // ... units 9 m apart
  // Setting angles from the unit nearest the runway, minutes of arc from the approach
  // angle (6B para 1-2 and its example): 3.5 / 3 deg 10' / 2 deg 50' / 2.5 deg for a 3 deg
  // approach; with ILS 3 deg 35' / 3 deg 15' / 2 deg 45' / 2 deg 35'.
  offsets: [30, 10, -10, -30],
  offsetsIls: [35, 15, -15, -25],
  mehtBelowMin: 2, // 6B para 5: the angle setting the MEHT is 2' below the second unit from outside
  transitionMin: 3, // 6.40: a perceived red-white transition of about three minutes of arc
  beamHalfDeg: 15, // figure 6B.1: the signal's azimuth coverage, 15 deg either side
};
export const FLASH_HZ = { sequence: 2, threshold: 1 }; // AIM 2-1-1: twice a second; threshold flashers 1 a second (project choice)

const FT = 0.3048;
const DEG = Math.PI / 180;

// Everything about one landing end: its threshold, direction, left, along-runway frame.
function endFrame(d, i) {
  const e = d.ends[i];
  const [px, pz] = d.pavement[i]; // the pavement's end behind this threshold
  const [fx, fz] = d.pavement[1 - i]; // the far end
  const ux = e.dx, uz = e.dz;
  const lx = uz, lz = -ux; // left of the landing direction (world x east, z south)
  const back = (px - e.x) * ux + (pz - e.z) * uz; // pavement start, along (negative: behind)
  const far = (fx - e.x) * ux + (fz - e.z) * uz; // pavement end, along
  return { e, ux, uz, lx, lz, back, far, lighting: e.lighting ?? null };
}

// The runway's elevation at a point along its pavement (a straight slope).
function heightAt(d, x, z) {
  if (d.base === undefined) return d.ends[0].y;
  const [ax, az] = d.pavement[0], [bx, bz] = d.pavement[1];
  const L = Math.hypot(bx - ax, bz - az);
  const s = ((x - ax) * (bx - ax) + (z - az) * (bz - az)) / L;
  return d.base + d.slope * s;
}

const LIGHT_Y = 0.6; // fittings above the surface (m)

export function runwayLights(d) {
  const out = [];
  const ends = [endFrame(d, 0), endFrame(d, 1)];
  const lit = ends.some((f) => f.lighting);
  const hw = d.widthM / 2;
  const at = (f, s, c, extraY = 0) => {
    const x = f.e.x + f.ux * s + f.lx * c, z = f.e.z + f.uz * s + f.lz * c;
    return [x, heightAt(d, x, z) + LIGHT_Y + extraY, z];
  };
  const face = (p, colour, f, flash = 0) => out.push({ p, c: COLOURS[colour], d: [-f.ux, -f.uz], f: flash });

  // Edge lights along the whole pavement, a face for each landing direction: red before
  // that direction's displaced threshold, yellow in its caution zone (published lighting
  // and no centreline lights), else white.
  const f0 = ends[0];
  const total = f0.far - f0.back;
  const n = Math.max(1, Math.ceil(total / EDGE_SPACING_M));
  const caution = lit && !d.centrelineM;
  const zone = Math.min(CAUTION_M, total / 3);
  for (let k = 0; k <= n; k++) {
    for (const side of [-1, 1]) {
      const p = at(f0, f0.back + (total * k) / n, side * (hw + 1));
      for (const f of ends) {
        const s = (p[0] - f.e.x) * f.ux + (p[2] - f.e.z) * f.uz; // along from this end's threshold
        face(p, s < -1 ? "red" : caution && f.far - s < zone ? "yellow" : "white", f);
      }
    }
  }
  for (const f of ends) {
    // Threshold lights (green toward the approach) across the runway, every 3 m (project
    // choice); wing bars where the threshold is displaced or the runway has an approach
    // lighting system (6.62): 5 lights 2.5 m apart beyond each edge (project choice).
    for (let c = -hw; c <= hw + 0.01; c += 3) face(at(f, 0, c), "green", f);
    if (f.back < -1 || f.lighting?.approach) {
      for (const side of [-1, 1]) for (let k = 1; k <= 5; k++) face(at(f, 0, side * (hw + 1 + 2.5 * k)), "green", f);
    }
    // Runway end lights (red toward the runway) across the far end of the pavement, as
    // seen by an aircraft rolling this way: 6 lights or more, every 3 m at most (project choice).
    const m = Math.max(6, Math.ceil(d.widthM / 3));
    for (let k = 0; k < m; k++) out.push({ p: at(f, f.far, -hw + (d.widthM * k) / (m - 1)), c: COLOURS.red, d: [-f.ux, -f.uz], f: 0 }); // toward the aircraft rolling up to it
  }
  // Runway centreline lights (6.66), both directions: white, then alternating red and white
  // over the last 900 m, red over the last 300 m.
  if (d.centrelineM) {
    for (const f of ends) {
      const count = Math.floor(f.far / d.centrelineM);
      for (let k = 0; k <= count; k++) {
        const s = k * d.centrelineM, toGo = f.far - s;
        const colour = toGo <= 300 ? "red" : toGo <= 900 && k % 2 ? "red" : "white";
        face(at(f, s, 0, -LIGHT_Y + 0.05), colour, f); // inset
      }
    }
  }
  for (const f of ends) {
    const L = f.lighting;
    if (!L) continue;
    const type = L.approach?.type ?? null;
    const precisionII = type === "cat2" || type === "cat3";
    // Touchdown zone barrettes (6.68) on Category II and III runways.
    if (precisionII) {
      const last = Math.min(TDZ.lengthM, f.far / 2);
      for (let s = TDZ.spacingM / 2; s <= last; s += TDZ.spacingM) {
        for (const side of [-1, 1]) for (let k = 0; k < TDZ.lights; k++) face(at(f, s, side * (TDZ.innerM + k * TDZ.gapM), -LIGHT_Y + 0.05), "white", f);
      }
    }
    if (type === "cat1" || precisionII) {
      const A = APPROACH, length = L.approach.length_m ?? A.lengthM;
      for (let s = A.spacingM; s <= length + 0.01; s += A.spacingM) {
        for (let k = -2; k <= 2; k++) face(at(f, -s, k * A.barretteGapM, 0.5), "white", f);
        if (precisionII && s <= A.sideRowM) {
          for (const side of [-1, 1]) for (let k = 0; k < A.sideRowLights; k++) face(at(f, -s, side * (TDZ.innerM + k * A.sideRowGapM), 0.3), "red", f);
        }
      }
      for (const s of A.crossbarsM.filter((s) => s <= length)) {
        const half = (A.crossbar300HalfM * (s + A.convergeM)) / (300 + A.convergeM);
        for (let c = 4; c <= half + 0.01; c += A.crossbarGapM) for (const side of [-1, 1]) face(at(f, -s, side * c, 0.5), "white", f);
      }
      // Sequenced flashers, from the outer end toward the threshold, twice a second.
      const firsts = [];
      for (let s = length; s >= A.flasherFromM - 0.01; s -= A.spacingM) firsts.push(s);
      firsts.forEach((s, i) => face(at(f, -s, 0, 1.2), "white", f, 1 + i / firsts.length));
    } else if (type === "simple") {
      const S = SIMPLE, length = L.approach.length_m ?? 420;
      for (let s = S.spacingM; s <= length + 0.01; s += S.spacingM) face(at(f, -s, 0, 0.5), "white", f);
      if (length >= S.crossbarM) for (let c = -S.crossbarHalfM; c <= S.crossbarHalfM + 0.01; c += S.crossbarGapM) face(at(f, -S.crossbarM, c, 0.5), "white", f);
    }
    // Flashing threshold identification lights: a synchronized pair beside the threshold
    // (AIM 2-1-3), 10 m beyond the edges (project choice).
    if (L.threshold_flash) for (const side of [-1, 1]) face(at(f, 0, side * (hw + 10), 0.6), "white", f, 1);
  }
  return out;
}

// The PAPI units of a runway: [{p, dx, dz, angle (rad)}], each landing end with a
// published PAPI; `fallback` (e.g. {angle_deg: 3, distance_m: 250, sides: "left"}) for an end
// without one (the procedural airfield; a real runway is lit as published only).
export function papiUnits(d, fallback = null) {
  const units = [];
  for (let i = 0; i < 2; i++) {
    const f = endFrame(d, i);
    const P = f.lighting?.papi ?? (f.lighting ? null : fallback);
    if (!P) continue;
    const offsets = P.ils ? PAPI.offsetsIls : PAPI.offsets;
    const angleDeg = P.angle_deg;
    // Distance from the threshold: from the published MEHT (the height of the eye at the
    // threshold on the lowest on-slope signal: the second unit from outside, less 2'), or given.
    const mehtAngle = (angleDeg + (offsets[2] - PAPI.mehtBelowMin) / 60) * DEG;
    const dist = P.meht_ft ? (P.meht_ft * FT) / Math.tan(mehtAngle) : P.distance_m;
    const sides = P.sides === "both" ? [1, -1] : P.sides === "right" ? [-1] : [1]; // +1 = left
    for (const side of sides) {
      offsets.forEach((o, k) => {
        const c = side * (d.widthM / 2 + PAPI.inboardM + k * PAPI.spacingM);
        const x = f.e.x + f.ux * dist + f.lx * c, z = f.e.z + f.uz * dist + f.lz * c;
        units.push({ p: [x, heightAt(d, x, z) + 0.9, z], dx: f.ux, dz: f.uz, angle: (angleDeg + o / 60) * DEG, ident: f.e.ident, distM: dist });
      });
    }
  }
  return units;
}

// A PAPI unit's colour seen from `eye` (world x, y, z): white above its setting angle, red
// below, blended over the transition; null outside its beam (behind it, or more than
// beamHalfDeg off the approach).
export function papiColour(u, eye) {
  const rx = eye[0] - u.p[0], rz = eye[2] - u.p[2];
  const ahead = -(rx * u.dx + rz * u.dz); // out along the approach
  const side = Math.abs(rx * u.dz - rz * u.dx);
  if (ahead <= 0 || side > ahead * Math.tan(PAPI.beamHalfDeg * DEG)) return null;
  const elev = Math.atan2(eye[1] - u.p[1], ahead);
  const t = Math.min(1, Math.max(0, (elev - u.angle) / ((PAPI.transitionMin / 60) * DEG) + 0.5));
  return COLOURS.red.map((r, k) => r + (COLOURS.white[k] - r) * t);
}
