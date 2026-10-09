"""OpenStreetMap features of a scenery region, for the build (scenery_build.py; needs osmium
from the `scenery` dependency group). Positions go onto the region's map through
world/geo.py.

Airfields: every aeroway=runway way in the region, grouped into runways by their `ref`
(the main way and its runway=displaced_threshold sections, which OSM maps as separate
ways sharing end nodes). Per runway: the two ends (pavement end and landing threshold,
each named by the runway number for landing in that direction), width, surface, and
the aerodrome it belongs to (aeroway=aerodrome within 5 km of its midpoint). Helipads
mapped as runways (refs starting with "H") are left out.
"""

import math
import re

from flightsim.world import geo

AERODROME_RADIUS_M = 5000.0


def _map(geodesy: geo.Geodesy, lat: float, lon: float) -> tuple[float, float]:
    return geodesy.to_map(math.radians(lat), math.radians(lon))


def _idents(ref: str) -> tuple[str, str] | None:
    """('13', '31') from refs such as '13/31', '13 / 31', '17-35', '13L/31R'."""
    parts = [p.strip() for p in re.split(r"[/-]", ref or "") if p.strip()]
    if len(parts) != 2 or not all(re.fullmatch(r"\d{1,2}[LRC]?", p) for p in parts):
        return None
    return parts[0], parts[1]


def _bearing(a, b) -> float:
    """Map bearing (deg) from a to b, both (north, east)."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def extract_airfields(pbf_path, geodesy: geo.Geodesy, half_size_m: float) -> dict:
    """{"aerodromes": [...], "runways": [...]} inside the square of half_size_m around the
    origin, in map metres (north, east)."""
    import osmium

    inside = lambda p: abs(p[0]) <= half_size_m and abs(p[1]) <= half_size_m  # noqa: E731
    aerodromes, ways = [], []
    fp = osmium.FileProcessor(str(pbf_path)).with_locations().with_filter(osmium.filter.KeyFilter("aeroway"))
    for o in fp:
        tags = dict(o.tags)
        kind = tags.get("aeroway")
        if o.is_node() and kind == "aerodrome":
            pts = [_map(geodesy, o.location.lat, o.location.lon)]
        elif o.is_way() and kind in ("aerodrome", "runway"):
            try:
                pts = [_map(geodesy, n.lat, n.lon) for n in o.nodes]
                ids = [n.ref for n in o.nodes]
            except osmium.InvalidLocationError:
                continue
        else:
            continue
        centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        if not inside(centre):
            continue
        if kind == "aerodrome":
            aerodromes.append({
                "osm": f"{'node' if o.is_node() else 'way'}/{o.id}", "icao": tags.get("icao"),
                "name": tags.get("name:en") or tags.get("name"), "centre": centre,
                "military": tags.get("military") == "airfield" or tags.get("landuse") == "military",
            })  # fmt: skip
        else:
            ways.append({"id": o.id, "tags": tags, "pts": pts, "ids": ids})
    aerodromes.sort(key=lambda a: a["osm"])
    return {"aerodromes": aerodromes, "runways": _runways(ways, aerodromes)}


def _join(mains: list[dict]) -> list[dict]:
    """Main runway ways with the same ref that share an end node, as one way (OSM sometimes
    splits a runway): its ends are the two farthest end points."""
    groups: list[list[dict]] = []
    for w in sorted(mains, key=lambda w: w["id"]):
        ends = {w["ids"][0], w["ids"][-1]}
        touching = [g for g in groups if g[0]["tags"].get("ref") == w["tags"].get("ref")
                    and any(ends & {x["ids"][0], x["ids"][-1]} for x in g)]  # fmt: skip
        merged = [w]
        for g in touching:
            groups.remove(g)
            merged += g
        groups.append(merged)
    out = []
    for g in groups:
        points = [(x["pts"][k], x["ids"][k]) for x in g for k in (0, -1)]
        (pa, ia), (pb, ib) = max(((p, q) for p in points for q in points), key=lambda pq: math.dist(pq[0][0], pq[1][0]))
        out.append({"id": min(x["id"] for x in g), "ids_all": sorted(x["id"] for x in g), "tags": g[0]["tags"], "pts": [pa, pb], "ids": [ia, ib]})
    return out


def _runways(ways: list[dict], aerodromes: list[dict]) -> list[dict]:
    mains = _join([w for w in ways if w["tags"].get("runway") != "displaced_threshold"])
    displaced = [w for w in ways if w["tags"].get("runway") == "displaced_threshold"]
    named = [ad for ad in aerodromes if ad["icao"]] + [ad for ad in aerodromes if not ad["icao"]]
    out = []
    for w in sorted(mains, key=lambda w: w["id"]):
        ref = w["tags"].get("ref", "")
        idents = _idents(ref)
        if ref.strip().upper().startswith("H") or idents is None:
            continue  # helipads, unnumbered strips
        a, b = w["pts"][0], w["pts"][-1]
        ends = {"a": {"threshold": a, "pavement": a}, "b": {"threshold": b, "pavement": b}}
        used = list(w["ids_all"])
        # Displaced threshold sections continue the pavement past an end node.
        for d in displaced:
            for key, node in (("a", w["ids"][0]), ("b", w["ids"][-1])):
                if node in (d["ids"][0], d["ids"][-1]):
                    far = d["pts"][-1] if d["ids"][0] == node else d["pts"][0]
                    ends[key]["pavement"] = far
                    used.append(d["id"])
        # Name each end by the runway number for landing from it: the end where the
        # landing direction (towards the other end) matches the number x 10 deg.
        heading_ab = _bearing(a, b)
        n0 = int(re.match(r"\d+", idents[0]).group()) * 10.0
        diff = abs((heading_ab - n0 + 180.0) % 360.0 - 180.0)
        first, second = ("a", "b") if diff < 90.0 else ("b", "a")
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        # The nearest aerodrome within reach, preferring those with an ICAO code (OSM can
        # map one airport twice).
        near = next((ad for ad in sorted(named, key=lambda ad: (ad["icao"] is None, math.dist(ad["centre"], mid)))
                     if math.dist(ad["centre"], mid) <= AERODROME_RADIUS_M), None)  # fmt: skip
        p0, p1 = ends["a"]["pavement"], ends["b"]["pavement"]
        width = w["tags"].get("width", "")
        width_m = float(re.match(r"[\d.]+", width).group()) if re.match(r"[\d.]+", width) else None
        out.append({
            "ref": f"{idents[0]}/{idents[1]}", "osm_ways": sorted(used),
            "airport": None if near is None else {"icao": near["icao"], "name": near["name"]},
            "surface": w["tags"].get("surface"), "width_m": width_m,
            "length_m": math.dist(p0, p1),
            "ends": [{"ident": idents[0], **ends[first]}, {"ident": idents[1], **ends[second]}],
        })  # fmt: skip
    return out


# --- Features: roads, railways, taxiways, aprons, buildings -------------------------------------

# Road classes drawn, OSM highway=* (links as their road); widths in metres (project
# choices, typical carriageways: dual motorways 2 x 3 lanes and verge, urban streets).
ROAD_WIDTH_M = {
    "motorway": 24.0, "trunk": 20.0, "primary": 14.0, "secondary": 11.0, "tertiary": 9.0,
    "unclassified": 7.0, "residential": 7.0,
}  # fmt: skip
TAXIWAY_WIDTH_M = 23.0  # ICAO code C taxiway (project choice where OSM has no width)
LEVEL_M = 3.3  # storey height for building:levels (project choice)


def _metres(value) -> float | None:
    """'12', '12 m', '12.5m' -> metres; feet or anything else -> None."""
    m = re.fullmatch(r"\s*([\d.]+)\s*(m)?\s*", value or "")
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def building_height(tags: dict, area_m2: float) -> tuple[float, str]:
    """(height m, source): OSM height, else floors x LEVEL_M (+ a roof metre), else an
    estimate from type and footprint (project choices: houses and villas 7 m, big footprints
    of offices, malls and warehouses 12-15 m, others 9 m)."""
    h = _metres(tags.get("height"))
    if h and 2.0 <= h <= 900.0:
        return h, "height"
    levels = _metres(tags.get("building:levels"))
    if levels and 1 <= levels <= 200:
        return levels * LEVEL_M + 1.0, "levels"
    kind = tags.get("building", "yes")
    if kind in ("house", "villa", "detached", "residential", "terrace", "hut", "shed", "garage", "garages") or area_m2 < 250:
        return 7.0, "estimate"
    if area_m2 > 4000:
        return 15.0 if kind not in ("warehouse", "industrial", "hangar") else 12.0, "estimate"
    return 9.0, "estimate"


def _clip(x0, z0, x1, z1, bx0, bz0, bx1, bz1):
    """Liang-Barsky: the part of segment (x0, z0)-(x1, z1) inside the box, or None."""
    dx, dz = x1 - x0, z1 - z0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - bx0), (dx, bx1 - x0), (-dz, z0 - bz0), (dz, bz1 - z0)):
        if p == 0:
            if q < 0:
                return None
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return None
    return x0 + dx * t0, z0 + dz * t0, x0 + dx * t1, z0 + dz * t1


def _polylines_by_tile(pts: list[tuple[float, float]], tile_m: float) -> dict[tuple[int, int], list[list[float]]]:
    """A polyline (x, z) cut at tile edges: {(ix, iz): [[x0, z0, x1, z1, ...], ...]}."""
    out: dict[tuple[int, int], list[list[float]]] = {}
    for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
        for ix in range(math.floor(min(x0, x1) / tile_m), math.floor(max(x0, x1) / tile_m) + 1):
            for iz in range(math.floor(min(z0, z1) / tile_m), math.floor(max(z0, z1) / tile_m) + 1):
                c = _clip(x0, z0, x1, z1, ix * tile_m, iz * tile_m, (ix + 1) * tile_m, (iz + 1) * tile_m)
                if c is None or (c[0] == c[2] and c[1] == c[3]):
                    continue
                lines = out.setdefault((ix, iz), [])
                a, b = [round(c[0], 1), round(c[1], 1)], [round(c[2], 1), round(c[3], 1)]
                if lines and lines[-1][-2:] == a:
                    lines[-1] += b  # continues the previous piece
                else:
                    lines.append(a + b)
    return out


def _ring_area(ring: list[tuple[float, float]]) -> float:
    return 0.5 * abs(sum(x0 * z1 - x1 * z0 for (x0, z0), (x1, z1) in zip(ring, ring[1:] + ring[:1])))


def extract_features(pbf_path, geodesy: geo.Geodesy, bounds_deg, half_size_m: float, tile_m: float) -> dict:
    """{(ix, iz): {"roads": {class: [polyline, ...]}, "rail": [...], "taxiway": [...],
    "apron": [ring, ...], "buildings": [[height_m, source, ring], ...]}} in world x (east),
    z (south) metres rounded to 0.1, for the tiles of the region. Lines are cut at tile
    edges; areas go to the tile of their centroid (outer rings only)."""
    import osmium

    s, w, n, e = bounds_deg
    inside_deg = lambda lat, lon: s <= lat <= n and w <= lon <= e  # noqa: E731
    tiles: dict[tuple[int, int], dict] = {}

    def tile(key):
        return tiles.setdefault(key, {"roads": {}, "rail": [], "taxiway": [], "apron": [], "buildings": []})

    def xz(lat, lon):
        north, east = geodesy.to_map(math.radians(lat), math.radians(lon))
        return round(east, 1), round(-north, 1)

    def add_lines(kind, pts, cls=None):
        for key, lines in _polylines_by_tile(pts, tile_m).items():
            t = tile(key)
            (t["roads"].setdefault(cls, []) if kind == "roads" else t[kind]).extend(lines)

    def add_area(kind, ring, extra=None):
        if len(ring) < 3:
            return
        cx, cz = sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
        if abs(cx) > half_size_m or abs(cz) > half_size_m:
            return
        flat = [c for p in ring for c in p]
        tile((math.floor(cx / tile_m), math.floor(cz / tile_m)))[kind].append(flat if extra is None else [*extra, flat])

    fp = (osmium.FileProcessor(str(pbf_path)).with_areas(osmium.filter.KeyFilter("building", "aeroway")).with_locations()
          .with_filter(osmium.filter.KeyFilter("highway", "railway", "aeroway", "building")))  # fmt: skip
    for o in fp:
        tags = o.tags
        if o.is_area():
            kind = "building" if "building" in tags else "apron" if tags.get("aeroway") == "apron" else None
            if kind is None:
                continue
            for outer in o.outer_rings():
                try:
                    lat0, lon0 = outer[0].lat, outer[0].lon
                    if not inside_deg(lat0, lon0):
                        break
                    ring = [xz(nd.lat, nd.lon) for nd in outer][:-1]  # closed: drop the repeat
                except osmium.InvalidLocationError:
                    break
                if kind == "apron":
                    add_area("apron", ring)
                else:
                    h, src = building_height(dict(tags), _ring_area(ring))
                    add_area("buildings", ring, (round(h, 1), src))
            continue
        if not o.is_way():
            continue
        hw, rw, aw = tags.get("highway"), tags.get("railway"), tags.get("aeroway")
        cls = (hw or "").removesuffix("_link") if hw else None
        kind = "roads" if cls in ROAD_WIDTH_M else "rail" if rw == "rail" else "taxiway" if aw == "taxiway" else None
        if kind is None:
            continue
        try:
            first = o.nodes[0]
            if not inside_deg(first.lat, first.lon) and not inside_deg(o.nodes[-1].lat, o.nodes[-1].lon):
                continue
            pts = [xz(nd.lat, nd.lon) for nd in o.nodes]
        except osmium.InvalidLocationError:
            continue
        add_lines(kind, pts, cls)
    return tiles
