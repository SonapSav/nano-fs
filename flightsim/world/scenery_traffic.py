"""Moving traffic of a scenery region, for the build (scenery_build.py; needs osmium from
the `scenery` dependency group to read the routes). Visual only: the viewer flies these
models (viewer/traffic.js), nothing reaches the physics.

A region file's `traffic` entry flies a model of the viewer along an OpenStreetMap road:
the carriageway of `route.osm_way`, chained through the ways joined end to end with the
same name or ref (in the way's direction: a one-way carriageway gives the direction of
flight), `before_m` back and `after_m` on. The chain is smoothed (a moving average
`smooth_m` long: a helicopter following a road does not take its kinks) and resampled
every STEP_M. `meet_osm_way`: the way whose middle the model passes when the flown aircraft
passes closest to it (the viewer times its start for that).
"""

import json
import math
import re
from pathlib import Path

from flightsim.world import geo

STEP_M = 10.0  # route points' spacing
KT = 1852.0 / 3600.0
FT = 0.3048
FORMAT = 1


def _read_ways(pbf_path, geodesy: geo.Geodesy) -> dict[int, dict]:
    """Every highway way: {id: {"nodes": [...], "pts": [(north, east)], "name", "ref"}}."""
    import osmium

    ways = {}

    class _H(osmium.SimpleHandler):
        def way(self, w):
            if "highway" not in w.tags:
                return
            try:
                pts = [geodesy.to_map(math.radians(n.lat), math.radians(n.lon)) for n in w.nodes]
            except osmium.InvalidLocationError:
                return
            ways[w.id] = {"nodes": [n.ref for n in w.nodes], "pts": pts,
                          "name": w.tags.get("name:en") or w.tags.get("name") or "", "ref": w.tags.get("ref") or ""}  # fmt: skip

    _H().apply_file(str(pbf_path), locations=True)
    return ways


def _length(pts) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def chain(ways: dict[int, dict], start: int, before_m: float, after_m: float) -> tuple[list, float, float]:
    """The road through way `start`, in its direction: the ways joined end to end with the
    same name or ref, `before_m` back from its first node and `after_m` on from its last
    (or as far as the road goes). Returns (points, start of `start` along them, its end)."""
    w0 = ways[start]
    same = lambda w: (w0["name"] and w["name"] == w0["name"]) or (w0["ref"] and w["ref"] == w0["ref"])  # noqa: E731
    by_first, by_last = {}, {}
    for wid, w in ways.items():
        if same(w) or wid == start:
            by_first.setdefault(w["nodes"][0], []).append(wid)
            by_last.setdefault(w["nodes"][-1], []).append(wid)

    def follow(node, index, used, limit):
        out, got = [], 0.0
        while got < limit:
            nxt = sorted(set(index.get(node, [])) - used)
            if not nxt:
                break
            wid = nxt[0]
            used.add(wid)
            pts = ways[wid]["pts"]
            out.append(pts)
            got += _length(pts)
            node = ways[wid]["nodes"][0] if index is by_last else ways[wid]["nodes"][-1]
        return out

    used = {start}
    back = follow(w0["nodes"][0], by_last, used, before_m)
    on = follow(w0["nodes"][-1], by_first, used, after_m)
    pts = []
    for seg in reversed(back):
        pts += seg[:-1]
    s0 = _length(pts + [w0["pts"][0]]) if pts else 0.0
    pts += w0["pts"]
    s1 = _length(pts)
    for seg in on:
        pts += seg[1:]
    return pts, s0, s1


def resample(pts, step: float = STEP_M) -> list[tuple[float, float]]:
    """Points every `step` along the polyline (its last point kept)."""
    out, carry = [pts[0]], 0.0
    for a, b in zip(pts, pts[1:]):
        d = math.dist(a, b)
        t = step - carry
        while t <= d:
            out.append((a[0] + (b[0] - a[0]) * t / d, a[1] + (b[1] - a[1]) * t / d))
            t += step
        carry = d - (t - step)
    if math.dist(out[-1], pts[-1]) > 1e-6:
        out.append(pts[-1])
    return out


def smooth(pts, window_m: float, step: float = STEP_M) -> list[tuple[float, float]]:
    """Moving average over window_m of points `step` apart (shorter windows at the ends, so
    the ends stay put)."""
    k = max(0, int(round(window_m / step / 2)))
    n = len(pts)
    out = []
    for i in range(n):
        h = min(k, i, n - 1 - i)
        sl = pts[i - h : i + h + 1]
        out.append((sum(p[0] for p in sl) / len(sl), sum(p[1] for p in sl) / len(sl)))
    return out


def route(ways: dict[int, dict], r: dict, meet_way: int) -> dict:
    """One traffic route: points every STEP_M (north, east, rounded to cm) and the distance
    along them of the meeting point (the middle of `meet_way`, projected onto the route)."""
    pts, _, _ = chain(ways, int(r["osm_way"]), float(r["before_m"]), float(r["after_m"]))
    pts = smooth(resample(pts), float(r.get("smooth_m", 300.0)))
    m = ways[meet_way]["pts"]
    half = _length(m) / 2
    for a, b in zip(m, m[1:]):  # the middle of the meeting way along its length
        d = math.dist(a, b)
        if half <= d:
            mid = (a[0] + (b[0] - a[0]) * half / d, a[1] + (b[1] - a[1]) * half / d)
            break
        half -= d
    else:
        mid = m[-1]
    i = min(range(len(pts)), key=lambda i: math.dist(pts[i], mid))
    return {"points": [[round(n, 2), round(e, 2)] for n, e in pts], "step_m": STEP_M, "meet_s": i * STEP_M,
            "meet": [round(mid[0], 2), round(mid[1], 2)]}  # fmt: skip


def build_traffic(spec, pbf: Path, out: Path, models_dir: Path, log=print) -> list[dict]:
    """traffic.json: the region file's `traffic` entries with their routes on the map."""
    entries = spec.traffic or []
    if not entries:
        (out / "traffic.json").write_text(json.dumps({"format": FORMAT, "traffic": []}) + "\n")
        return []
    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    ways = _read_ways(pbf, g)
    result = []
    for t in entries:
        name = str(t["model"])
        if not re.fullmatch(r"[a-z0-9_]+", name) or not (models_dir / f"{name}.glb").is_file():
            raise ValueError(f"traffic {t.get('name')!r}: model {name!r}: no {models_dir / name}.glb")
        for key in ("osm_way",):
            if int(t["route"][key]) not in ways:
                raise ValueError(f"traffic {t.get('name')!r}: OSM way {t['route'][key]} is not a highway in the extract")
        r = route(ways, t["route"], int(t["meet_osm_way"]))
        length = (len(r["points"]) - 1) * STEP_M
        log(f"traffic {t.get('name')}: {name} along {length / 1000:.1f} km of {ways[int(t['route']['osm_way'])]['name']}, "
            f"meeting point {r['meet_s'] / 1000:.1f} km along")  # fmt: skip
        result.append({"name": str(t.get("name", "")), "model": name, "speed_mps": float(t["speed_kt"]) * KT,
                       "alt_m": float(t["altitude_ft"]) * FT, "trigger_m": float(t["trigger_m"]),
                       "pitch_deg": float(t.get("pitch_deg", 0.0)), **r})  # fmt: skip
    (out / "traffic.json").write_text(json.dumps({"format": FORMAT, "traffic": result}, sort_keys=True) + "\n")
    return result
