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
