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
    """{"aerodromes", "runways", "stands", "holds", "windsocks"} inside the square of
    half_size_m around the origin, in map metres (north, east). Stands: aeroway=
    parking_position (nodes, or lead-in lines whose last node is the stand, the nose along
    the line), heading the direction a parked aircraft points (map degrees; nodes: toward
    the nearest taxiway); holds: aeroway=holding_position with the direction of the nearest
    taxiway; windsocks: aeroway=windsock."""
    import osmium

    inside = lambda p: abs(p[0]) <= half_size_m and abs(p[1]) <= half_size_m  # noqa: E731
    aerodromes, ways = [], []
    points = {"parking_position": [], "holding_position": [], "windsock": []}
    stand_lines, taxiways = [], []
    fp = osmium.FileProcessor(str(pbf_path)).with_locations().with_filter(osmium.filter.KeyFilter("aeroway"))
    for o in fp:
        tags = dict(o.tags)
        kind = tags.get("aeroway")
        if o.is_node() and kind in points:
            p = _map(geodesy, o.location.lat, o.location.lon)
            if inside(p):
                points[kind].append(p)
            continue
        if o.is_way() and kind in ("parking_position", "taxiway"):
            try:
                pts = [_map(geodesy, n.lat, n.lon) for n in o.nodes]
            except osmium.InvalidLocationError:
                continue
            if inside(pts[0]) and len(pts) >= 2:
                (stand_lines if kind == "parking_position" else taxiways).append(pts)
            continue
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
    segments = [(a, b) for line in taxiways for a, b in zip(line, line[1:])]
    stands = [{"north": b[0], "east": b[1], "heading_deg": _bearing(a, b)} for a, b in ((line[-2], line[-1]) for line in stand_lines)]
    for p in points["parking_position"]:
        near = _nearest_point(p, segments)
        stands.append({"north": p[0], "east": p[1], "heading_deg": _bearing(p, near) if near else None})
    holds = []
    for p in points["holding_position"]:
        seg = _nearest_segment(p, segments)
        holds.append({"north": p[0], "east": p[1], "taxiway_deg": _bearing(*seg) if seg else None})
    windsocks = [{"north": p[0], "east": p[1]} for p in points["windsock"]]
    key = lambda d: (round(d["north"], 1), round(d["east"], 1))  # noqa: E731
    return {"aerodromes": aerodromes, "runways": _runways(ways, aerodromes), "stands": sorted(stands, key=key),
            "holds": sorted(holds, key=key), "windsocks": sorted(windsocks, key=key)}  # fmt: skip


def _closest_on(p, a, b):
    dn, de = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dn + (p[1] - a[1]) * de) / (dn * dn + de * de or 1.0)))
    return (a[0] + t * dn, a[1] + t * de)


def _nearest_segment(p, segments, reach_m: float = 200.0):
    best = min(segments, key=lambda s: math.dist(p, _closest_on(p, *s)), default=None)
    return best if best is not None and math.dist(p, _closest_on(p, *best)) <= reach_m else None


def _nearest_point(p, segments, reach_m: float = 200.0):
    s = _nearest_segment(p, segments, reach_m)
    return _closest_on(p, *s) if s else None


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
    estimate from type and footprint (project choices: houses, villas and small footprints
    7 m, "estimate_small"; big footprints of offices, malls and warehouses 12-15 m, others
    9 m, "estimate"). The build replaces estimates with measured cell averages where it has
    them (scenery_build._measured_heights)."""
    h = _metres(tags.get("height"))
    if h and 2.0 <= h <= 900.0:
        return h, "height"
    levels = _metres(tags.get("building:levels"))
    if levels and 1 <= levels <= 200:
        return levels * LEVEL_M + 1.0, "levels"
    kind = tags.get("building", "yes")
    if kind in ("house", "villa", "detached", "residential", "terrace", "hut", "shed", "garage", "garages") or area_m2 < 250:
        return 7.0, "estimate_small"
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


def extract_parts(pbf_path, geodesy: geo.Geodesy, sites: dict[str, tuple[float, float, float]]) -> dict:
    """OSM's 3D parts near landmarks (sites: {key: (x, z, radius_m)} in world metres):
    {key: {"domes": [...], "building_parts": [...], "pools": [ring, ...]}}. Domes are
    `building:part=dome` or parts with `roof:shape=dome`: {"x", "z", "d" (mean of the
    footprint's extents), "top", "min", "roof_h", "levels", "colour"} (Simple 3D Buildings
    tags: height, min_height, roof:height, building:levels, roof:colour or
    building:colour; None where untagged). Other building parts: {"ring", "top", "min",
    "levels", "colour"}. Pools are `natural=water` areas. Sorted by position so builds
    are reproducible."""
    import osmium

    out = {k: {"domes": [], "building_parts": [], "pools": []} for k in sites}
    fp = osmium.FileProcessor(str(pbf_path)).with_areas(osmium.filter.KeyFilter("building:part", "natural")).with_locations()
    fp = fp.with_filter(osmium.filter.KeyFilter("building:part", "natural"))
    for o in fp:
        if not o.is_area():
            continue
        tags = o.tags
        part, pool = "building:part" in tags, tags.get("natural") == "water"
        if not (part or pool):
            continue
        for outer in o.outer_rings():
            try:
                pts = [_map(geodesy, nd.lat, nd.lon) for nd in outer][:-1]
            except osmium.InvalidLocationError:
                break
            xs, zs = [e for _, e in pts], [-n for n, _ in pts]
            cx, cz = sum(xs) / len(xs), sum(zs) / len(zs)
            for key, (sx, sz, r) in sites.items():
                if math.hypot(cx - sx, cz - sz) > r:
                    continue
                if pool:
                    out[key]["pools"].append([round(c, 1) for p in zip(xs, zs) for c in p])
                    continue
                levels = _metres(tags.get("building:levels"))
                common = {"top": _metres(tags.get("height")), "min": _metres(tags.get("min_height")), "levels": levels,
                          "colour": tags.get("roof:colour") or tags.get("building:colour")}  # fmt: skip
                if tags.get("building:part") == "dome" or tags.get("roof:shape") == "dome":
                    d = ((max(xs) - min(xs)) + (max(zs) - min(zs))) / 2
                    out[key]["domes"].append({"x": round(cx, 1), "z": round(cz, 1), "d": round(d, 1), "roof_h": _metres(tags.get("roof:height")), **common})
                else:
                    out[key]["building_parts"].append({"ring": [round(c, 1) for p in zip(xs, zs) for c in p], **common})
            break
    for v in out.values():
        v["domes"].sort(key=lambda d: (d["x"], d["z"]))
        v["building_parts"].sort(key=lambda p: p["ring"])
        v["pools"].sort()
    return out


def extract_features(pbf_path, geodesy: geo.Geodesy, bounds_deg, half_size_m: float, tile_m: float, capture: set | None = None) -> dict:
    """{(ix, iz): {"roads": {class: [polyline, ...]}, "rail": [...], "taxiway": [...],
    "apron": [ring, ...], "buildings": [[height_m, source, ring], ...], "wall": [polyline, ...],
    "fence": [polyline, ...]}} (barrier=wall / fence: walls and fences) in world x (east),
    z (south) metres rounded to 0.1, for the tiles of the region. Lines are cut at tile
    edges; areas go to the tile of their centroid (outer rings only). `capture`: OSM
    objects ("way/<id>", "relation/<id>") whose first outer ring and its inner rings are
    also returned under the key "captured" ({object: {"outer": ring, "inner": [ring, ...]}}).
    Roads and railways on bridges are left out of the tiles and returned whole under the
    key "bridges" ([{"id", "nodes" (OSM node ids), "pts" ([[x, z], ...]), "kind" ("roads"
    or "rail"), "cls", "lanes", "layer", "name"}]; scenery_bridges.py)."""
    import osmium

    s, w, n, e = bounds_deg
    inside_deg = lambda lat, lon: s <= lat <= n and w <= lon <= e  # noqa: E731
    tiles: dict = {}
    captured: dict[str, dict] = {}
    bridges: list[dict] = []

    def tile(key):
        return tiles.setdefault(key, {"roads": {}, "rail": [], "taxiway": [], "apron": [], "buildings": [], "wall": [], "fence": []})

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
          .with_filter(osmium.filter.KeyFilter("highway", "railway", "aeroway", "building", "barrier")))  # fmt: skip
    for o in fp:
        tags = o.tags
        if o.is_area():
            aeroway = tags.get("aeroway")
            kind = "building" if "building" in tags or aeroway in ("hangar", "terminal") else "apron" if aeroway == "apron" else None
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
                obj = f"{'way' if o.from_way() else 'relation'}/{o.orig_id()}"
                if capture and obj in capture and obj not in captured:
                    inner = [[c for nd in list(r)[:-1] for c in xz(nd.lat, nd.lon)] for r in o.inner_rings(outer)]
                    captured[obj] = {"outer": [c for p in ring for c in p], "inner": inner}
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
        bar = tags.get("barrier")
        kind = ("roads" if cls in ROAD_WIDTH_M else "rail" if rw == "rail" else "taxiway" if aw == "taxiway"
                else bar if bar in ("wall", "fence") else None)  # fmt: skip
        if kind is None:
            continue
        try:
            first = o.nodes[0]
            if not inside_deg(first.lat, first.lon) and not inside_deg(o.nodes[-1].lat, o.nodes[-1].lon):
                continue
            pts = [xz(nd.lat, nd.lon) for nd in o.nodes]
        except osmium.InvalidLocationError:
            continue
        if tags.get("bridge", "no") != "no" and kind in ("roads", "rail"):
            bridges.append({"id": o.id, "nodes": [nd.ref for nd in o.nodes], "pts": [list(p) for p in pts], "kind": kind, "cls": cls or "rail",
                            "lanes": _metres(tags.get("lanes")), "layer": _metres(tags.get("layer")) or 1,
                            "name": tags.get("bridge:name") or tags.get("name:en") or tags.get("name")})  # fmt: skip
            continue
        add_lines(kind, pts, cls)
    if capture:
        tiles["captured"] = captured
    tiles["bridges"] = sorted(bridges, key=lambda b: b["id"])
    return tiles
