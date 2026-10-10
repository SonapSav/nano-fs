// A real-world region's runway (its airfields.json entry, flightsim/world/scenery_osm.py) as
// the runway descriptor the scenery uses (scenery.js: lights, PAPI, windsock; realAirfields.js;
// the moving map). No three.js.

// A runway of airfields.json as the scenery's runway descriptor (scenery.js): world x =
// east, z = south; each end its landing threshold, elevation and landing direction.
export function runwayDescriptor(rw) {
  const [a, b] = rw.ends;
  const xz = (p) => [p[1], -p[0]];
  const [ax, az] = xz(a.threshold), [bx, bz] = xz(b.threshold);
  const len = Math.hypot(bx - ax, bz - az), ux = (bx - ax) / len, uz = (bz - az) / len;
  return {
    ref: rw.ref, icao: rw.airport?.icao ?? null, widthM: rw.width_m, lengthM: rw.length_m,
    colour: rw.colour ?? null, // its asphalt in the imagery (0xRRGGBB; scenery_colours.py), or null
    pavement: [xz(a.pavement), xz(b.pavement)],
    ends: [
      { ident: a.ident, x: ax, z: az, y: a.elevation_m, dx: ux, dz: uz },
      { ident: b.ident, x: bx, z: bz, y: b.elevation_m, dx: -ux, dz: -uz },
    ],
    // Elevation along the pavement from its first end: a straight slope.
    base: rw.elevation_m - (rw.slope_pct / 100) * (rw.length_m / 2),
    slope: rw.slope_pct / 100,
  };  // fmt: skip
}

