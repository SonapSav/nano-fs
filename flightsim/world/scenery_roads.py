"""The road network as drawn roads with markings (prototype, 2026-10-11; not yet part of the
build): OpenStreetMap's roads in a box with their lanes and one-way tags, moved onto the
roads of the 1 m imagery, with where their markings stop for junctions, and the marked
crossings. The viewer (viewer/roadNetwork.js, `?roadproto`) draws them with one asphalt
and its markings, the bridge decks with the same.

Alignment: OSM's centrelines lie a few metres from the imagery's roads, much of it a
slowly varying shift of the imagery. Across-road brightness profiles every 25 m find the
dark band of the road (a box of the road's width, shifted across); each finding says how
far the road lies along its normal. A shift per 200 m cell is fitted to the findings
within 300 m (least squares on the normal components, Gaussian weights), and every node
moves by the field there (bilinear), so roads stay joined at their junctions.

Run: uv run --group scenery python -m flightsim.world.scenery_roads configs/scenery/abu_dhabi.yaml x0 z0 x1 z1
(writes data/scenery/<region>/proto_roads.json).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
           "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link")  # fmt: skip
LANE_M = 3.65  # (project choice; UAE lanes are about 3.5-3.75 m)
SHOULDERS_M = 2.0  # both edges together, beyond the outer lane lines (project choice)
DEFAULT_LANES = {"motorway": 3, "trunk": 3, "primary": 2, "secondary": 2, "tertiary": 2, "unclassified": 2, "residential": 2, "living_street": 1}
CELL_M = 200.0
STEP_M = 25.0
DENSE_M = 5.0  # drawn roads: a point at least this often (they follow the ground)


def lanes_of(tags: dict, cls: str) -> tuple[int, bool]:
    """(lanes, one-way) of an OSM road (link roads: one lane each way unless tagged)."""
    oneway = tags.get("oneway") in ("yes", "1", "-1") or tags.get("junction") == "roundabout" or cls == "motorway"
    base = cls.removesuffix("_link")
    try:
        lanes = max(1, int(float(tags.get("lanes", "").split(";")[0])))
    except ValueError:
        lanes = 1 if cls.endswith("_link") else DEFAULT_LANES.get(base, 2) * (1 if oneway else 1)
    return lanes, oneway


def width_m(lanes: int) -> float:
    return lanes * LANE_M + SHOULDERS_M


def extract(pbf: Path, g, box: tuple[float, float, float, float], margin_m: float = 300.0):
    """Roads ({"id", "cls", "lanes", "oneway", "nodes", "pts" [[x, z]...], "bridge"}) and
    crossings / signals ({"kind", "node", "x", "z"}) with a node in the box (x0, z0, x1, z1)."""
    import osmium

    from flightsim.world.scenery_build import to_geodetic_arrays, to_map_arrays

    x0, z0, x1, z1 = box
    lat, lon = to_geodetic_arrays(g, -np.array([z0 - margin_m, z1 + margin_m]), np.array([x0 - margin_m, x1 + margin_m]))
    s, n, w, e = lat.min(), lat.max(), lon.min(), lon.max()
    roads, points = [], []
    for o in osmium.FileProcessor(str(pbf)).with_locations():
        if o.is_node():
            hw = o.tags.get("highway")
            if hw in ("crossing", "traffic_signals") and s <= o.location.lat <= n and w <= o.location.lon <= e:
                kind = "signals" if hw == "traffic_signals" else o.tags.get("crossing", "marked")
                points.append({"kind": kind, "node": o.id, "lat": o.location.lat, "lon": o.location.lon})
            continue
        if not o.is_way():
            continue
        cls = o.tags.get("highway")
        if cls not in CLASSES or o.tags.get("tunnel") in ("yes", "building_passage") or o.tags.get("area") == "yes":
            continue
        try:
            ll = [(nd.location.lat, nd.location.lon) for nd in o.nodes]
        except osmium.InvalidLocationError:
            continue
        la = np.array([p[0] for p in ll])
        lo = np.array([p[1] for p in ll])
        if not ((la >= s) & (la <= n) & (lo >= w) & (lo <= e)).any():
            continue
        north, east = to_map_arrays(g, la, lo)
        tags = dict(o.tags)
        lanes, oneway = lanes_of(tags, cls)
        pts = np.column_stack([east, -north])
        if tags.get("oneway") == "-1":
            pts = pts[::-1]
        roads.append({"id": o.id, "cls": cls, "lanes": lanes, "oneway": oneway, "nodes": [nd.ref for nd in o.nodes][:: -1 if tags.get("oneway") == "-1" else 1],
                      "pts": pts, "bridge": tags.get("bridge") not in (None, "no")})  # fmt: skip
    for p in points:
        north, east = to_map_arrays(g, np.array([p["lat"]]), np.array([p["lon"]]))
        p["x"], p["z"] = float(east[0]), float(-north[0])
    return roads, points


def find_offsets(roads: list[dict], picture) -> np.ndarray:
    """Findings [[x, z, nx, nz, d], ...]: at (x, z) the imagery's road lies d metres along
    the normal (nx, nz) from OSM's centreline. `picture(xs, zs)` -> (luminance, in 1 m
    imagery) at map points."""
    out = []
    k = np.arange(-30.0, 31.0)
    for r in roads:
        if r["bridge"]:
            continue
        w = width_m(r["lanes"])
        a = r["pts"]
        for p, q in zip(a[:-1], a[1:]):
            L = float(np.hypot(*(q - p)))
            if L < STEP_M:
                continue
            u = (q - p) / L
            nrm = np.array([-u[1], u[0]])
            for t in np.arange(STEP_M / 2, L - STEP_M / 2, STEP_M):
                c = p + u * t
                lum, ok = picture(c[0] + nrm[0] * k, c[1] + nrm[1] * k)
                if not ok.all():
                    continue
                best = None
                for sft in range(-12, 13):
                    ins = np.abs(k - sft) < w / 2
                    outs = (np.abs(k - sft) > w / 2 + 1) & (np.abs(k - sft) < w / 2 + 6)
                    sc = lum[outs].mean() - lum[ins].mean()
                    if best is None or sc > best[0]:
                        best = (sc, sft)
                if best[0] > 30 and abs(best[1]) < 12:
                    out.append((c[0], c[1], nrm[0], nrm[1], float(best[1])))
    return np.array(out).reshape(-1, 5)


def shift_field(found: np.ndarray, box, cell_m: float = CELL_M, reach_m: float = 300.0, min_n: int = 30):
    """The shift per cell over the box (+ one cell): (x0, z0, cell, grid [rows, cols, 2]);
    cells with fewer than `min_n` findings in reach take the box's own fit."""
    x0, z0, x1, z1 = box
    gx0, gz0 = np.floor(x0 / cell_m) * cell_m - cell_m, np.floor(z0 / cell_m) * cell_m - cell_m
    cols, rows = int(np.ceil((x1 - gx0) / cell_m)) + 2, int(np.ceil((z1 - gz0) / cell_m)) + 2
    N, d = found[:, 2:4], found[:, 4]
    every, *_ = np.linalg.lstsq(N, d, rcond=None)
    grid = np.tile(every, (rows, cols, 1))
    for r in range(rows):
        for c in range(cols):
            cx, cz = gx0 + c * cell_m, gz0 + r * cell_m
            dist = np.hypot(found[:, 0] - cx, found[:, 1] - cz)
            near = dist < reach_m
            if near.sum() < min_n:
                continue
            wgt = np.exp(-0.5 * (dist[near] / (reach_m / 2)) ** 2)
            A = N[near] * wgt[:, None]
            # Ridge toward the box's fit: few findings in one direction do not run away.
            lam = 5.0
            D = np.linalg.solve(A.T @ N[near] + lam * np.eye(2), A.T @ d[near] + lam * every)
            grid[r, c] = D
    return gx0, gz0, cell_m, grid


def apply_field(pts: np.ndarray, field) -> np.ndarray:
    gx0, gz0, cell, grid = field
    u, v = (pts[:, 0] - gx0) / cell, (pts[:, 1] - gz0) / cell
    i = np.clip(np.floor(u).astype(int), 0, grid.shape[1] - 2)
    j = np.clip(np.floor(v).astype(int), 0, grid.shape[0] - 2)
    fx, fz = np.clip(u - i, 0, 1)[:, None], np.clip(v - j, 0, 1)[:, None]
    top = grid[j, i] * (1 - fx) + grid[j, i + 1] * fx
    bot = grid[j + 1, i] * (1 - fx) + grid[j + 1, i + 1] * fx
    return pts + top * (1 - fz) + bot * fz


def _densify(pts: np.ndarray) -> np.ndarray:
    out = [pts[0]]
    for p, q in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.ceil(np.hypot(*(q - p)) / DENSE_M)))
        for k in range(1, n + 1):
            out.append(p + (q - p) * k / n)
    return np.array(out)


def _chains(roads: list[dict]) -> list[list[tuple[dict, bool]]]:
    """Roads joined where one continues another: at a node where exactly two road ends
    meet and nothing else, of the same lanes and one-way (one-way roads end to start).
    Each chain: [(road, reversed), ...] in order."""
    ends: dict[int, list[tuple[int, int]]] = {}
    inner: dict[int, int] = {}
    for i, r in enumerate(roads):
        ends.setdefault(r["nodes"][0], []).append((i, 0))
        ends.setdefault(r["nodes"][-1], []).append((i, 1))
        for nd in r["nodes"][1:-1]:
            inner[nd] = inner.get(nd, 0) + 1

    def partner(i: int, which: int) -> tuple[int, int] | None:
        nd = roads[i]["nodes"][-1 if which else 0]
        here = ends.get(nd, [])
        if len(here) != 2 or inner.get(nd):
            return None
        (j, w) = here[0] if here[1] == (i, which) else here[1]
        a, b = roads[i], roads[j]
        if j == i or (a["lanes"], a["oneway"]) != (b["lanes"], b["oneway"]):
            return None
        if a["oneway"] and w == which:  # one-way: an end meets a start
            return None
        return j, w

    used, chains = set(), []
    for start in range(len(roads)):
        if start in used:
            continue
        # Back to the chain's first road (its free end), then forward.
        i, rev, seen = start, False, {start}
        while (pj := partner(i, 1 if rev else 0)) is not None and pj[0] not in seen:
            j, w = pj
            seen.add(j)
            i, rev = j, w == 0  # entered j at its start: walking back means j runs reversed
        chain, at = [], (i, rev)
        while at is not None and at[0] not in used:
            j, rv = at
            used.add(j)
            chain.append((roads[j], rv))
            nxt = partner(j, 0 if rv else 1)
            at = (nxt[0], nxt[1] == 1) if nxt else None
        chains.append(chain)
    return chains


MAX_REACH_M = 150.0  # markings stop at most this far either side of a junction (long ramp splits)


def network(roads: list[dict], points: list[dict]) -> dict:
    """The drawn network: road chains (continuing pieces joined, so lines and dashes run
    on) as ribbons of their non-bridge runs, each with its points, arc length from the
    chain's start (`s0`: dashes continue over bridges) and a marking weight: 0 where other
    roads overlap it at a junction (how far: from the angle between the roads, a ramp
    splitting off at a shallow angle overlaps for long), easing back over 4 m; run ends at a
    node shared with other roads extend half their width (no notches). The crossings with
    their road's direction and width; network arc lengths at the bridges' ends."""
    incid: dict[int, list[tuple[np.ndarray, float, int]]] = {}  # node -> [(direction leaving, width, chain)]
    chains = _chains(roads)
    lines = []
    for ci, chain in enumerate(chains):
        pts, nodes, bridge = [], [], []
        for r, rv in chain:
            p = r["pts"][::-1] if rv else r["pts"]
            nd = r["nodes"][::-1] if rv else r["nodes"]
            k = 1 if pts else 0
            pts.extend(p[k:])
            nodes.extend(nd[k:])
            bridge.extend([r["bridge"]] * (len(nd) - k))
        pts = np.array(pts)
        r0 = chain[0][0]
        w = width_m(r0["lanes"])
        lines.append({"pts": pts, "nodes": nodes, "bridge": bridge, "w": w, "r": r0})
        for k, nd in enumerate(nodes):
            for nb in (k - 1, k + 1):
                if 0 <= nb < len(pts):
                    d = pts[nb] - pts[k]
                    L = float(np.hypot(*d))
                    if L > 0:
                        incid.setdefault(nd, []).append((d / L, w, ci))

    def reach(nd: int, ci: int, d: np.ndarray, w: float) -> float:
        out = 0.0
        for d2, w2, c2 in incid.get(nd, []):
            if c2 == ci and float(d @ d2) < -0.5:
                continue  # this road's own other half
            if c2 == ci and float(d @ d2) > 0.999:
                continue  # itself
            cosang = float(np.clip(d @ d2, -1.0, 1.0))
            if cosang < -0.87:
                continue  # another road continuing straight on (150 deg or more apart)
            sin = max(np.sqrt(1.0 - cosang * cosang), 0.05)
            need = ((w + w2) / 2 if cosang > 0 else w2 / 2) / sin + 2.0
            out = max(out, min(need, MAX_REACH_M))
        return out

    out, at_node, bridge_s = [], {}, []
    for ci, ln in enumerate(lines):
        pts, nodes = ln["pts"], ln["nodes"]
        s_nodes = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        r0 = ln["r"]
        for k, nd in enumerate(nodes):
            at_node.setdefault(nd, (ln, k))
        # Bridge ends: the network's arc length there (deck dashes continue).
        for k in range(len(nodes)):
            b = ln["bridge"][k]
            if k and b != ln["bridge"][k - 1] or (k == 0 and b) or (k == len(nodes) - 1 and b):
                bridge_s.append([round(float(pts[k][0]), 1), round(float(pts[k][1]), 1), round(float(s_nodes[k]), 2)])
        # Junction gaps in the markings, by arc length.
        gaps = []
        for k, nd in enumerate(nodes):
            others = [c for c in incid.get(nd, []) if c[2] != ci]
            if not others:
                continue
            fwd = reach(nd, ci, (pts[k + 1] - pts[k]) / (np.hypot(*(pts[k + 1] - pts[k])) or 1), ln["w"]) if k + 1 < len(pts) else 0.0
            back = reach(nd, ci, (pts[k - 1] - pts[k]) / (np.hypot(*(pts[k - 1] - pts[k])) or 1), ln["w"]) if k > 0 else 0.0
            gaps.append((s_nodes[k] - back, s_nodes[k] + fwd))
        # Runs of non-bridge pieces.
        k = 0
        while k < len(nodes) - 1:
            if ln["bridge"][k + 1] and (k == 0 or ln["bridge"][k]):
                k += 1
                continue
            j = k
            while j + 1 < len(nodes) and not ln["bridge"][j + 1]:
                j += 1
            if j == k:
                k += 1
                continue
            run = pts[k : j + 1].copy()
            s0 = float(s_nodes[k])
            # Extend the run's ends at a shared node (no notches); the bridge's own deck meets it otherwise.
            ext = [0.0, 0.0]
            for e, (idx, nb) in enumerate(((k, k + 1), (j, j - 1))):
                nd = nodes[idx]
                if any(c[2] != ci for c in incid.get(nd, [])) and not (0 < idx < len(nodes) - 1 and ln["bridge"][idx if e == 0 else idx + 1]):
                    d = pts[idx] - pts[nb]
                    d = d / (np.hypot(*d) or 1)
                    ext[e] = min(ln["w"] / 2, 8.0)
                    if e == 0:
                        run = np.vstack([pts[idx] + d * ext[0], run])
                    else:
                        run = np.vstack([run, pts[idx] + d * ext[1]])
            s0 -= ext[0]
            dense = _densify(run)
            s = s0 + np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(dense, axis=0).T))])
            mark = np.ones(len(s))
            for a, b in gaps:
                mark = np.minimum(mark, np.clip(np.maximum(a - s, s - b) / 4.0, 0.0, 1.0))
            out.append({"id": r0["id"], "cls": r0["cls"], "lanes": r0["lanes"], "oneway": r0["oneway"], "width": round(ln["w"], 2), "s0": round(s0, 2),
                        "pts": [[round(float(x), 2), round(float(z), 2), round(float(m), 2)] for (x, z), m in zip(dense, mark)]})  # fmt: skip
            k = j
    marks = []
    for p in points:
        hit = at_node.get(p["node"])
        if not hit or p["kind"] in ("unmarked", "no"):
            continue
        ln, i = hit
        a = ln["pts"]
        q0, q1 = a[max(0, i - 1)], a[min(len(a) - 1, i + 1)]
        u = (q1 - q0) / (np.hypot(*(q1 - q0)) or 1.0)
        x, z = a[i]
        junction = len({c[2] for c in incid.get(p["node"], [])}) > 1
        marks.append({"kind": "stop" if p["kind"] == "signals" else "zebra", "x": round(float(x), 2), "z": round(float(z), 2),
                      "ux": round(float(u[0]), 4), "uz": round(float(u[1]), 4), "width": round(ln["w"], 2), "junction": junction})  # fmt: skip
    return {"roads": out, "marks": [m for m in marks if not (m["kind"] == "stop" and m["junction"])], "bridge_s": bridge_s}


def main(argv: list[str]) -> None:
    from flightsim.world import geo
    from flightsim.world.scenery import TILE_SIZE_M
    from flightsim.world.scenery_build import SCENERY_DIR, load_spec
    from flightsim.world.scenery_colours import tile_picture

    spec = load_spec(argv[0])
    box = tuple(float(v) for v in argv[1:5])
    out = SCENERY_DIR / spec.name
    g = geo.Geodesy("wgs84", spec.origin_lat_deg, spec.origin_lon_deg)
    pbf = next((out / "sources").glob("*.osm.pbf"))
    roads, points = extract(pbf, g, box)
    print(f"{len(roads)} roads, {len(points)} crossings and signals")
    hires = json.loads((out / "hires.json").read_text())
    tiles: dict = {}

    def picture(xs, zs):
        lum = np.zeros(len(xs), np.float32)
        ok = np.zeros(len(xs), bool)
        ix, iz = np.floor(xs / TILE_SIZE_M).astype(int), np.floor(zs / TILE_SIZE_M).astype(int)
        for key in set(zip(ix.tolist(), iz.tolist())):
            if key not in tiles:
                img, mask = tile_picture(out, key[0], key[1], hires, with_mask=True)
                tiles[key] = (img.astype(np.float32).mean(0), mask)
            lm, mk = tiles[key]
            sel = (ix == key[0]) & (iz == key[1])
            c = np.clip(np.round(xs[sel] - key[0] * TILE_SIZE_M).astype(int), 0, 3999)
            r = np.clip(np.round(zs[sel] - key[1] * TILE_SIZE_M).astype(int), 0, 3999)
            lum[sel], ok[sel] = lm[r, c], mk[r, c]
        return lum, ok

    found = find_offsets(roads, picture)
    field = shift_field(found, box)
    before = np.median(np.abs(found[:, 4]))
    moved = np.array([f[4] - np.dot(f[2:4], apply_field(f[None, :2], field)[0] - f[:2]) for f in found])
    print(f"{len(found)} findings; |offset| median {before:.1f} m before, {np.median(np.abs(moved)):.1f} m after the shift field; "
          f"field {field[3].reshape(-1, 2).mean(0).round(1)} m (mean east, south)")  # fmt: skip
    for r in roads:
        r["pts"] = apply_field(r["pts"], field)
    for p in points:
        p["x"], p["z"] = apply_field(np.array([[p["x"], p["z"]]]), field)[0]

    net = network(roads, points)
    net["box"] = list(box)
    gx0, gz0, cell, grid = field
    net["field"] = {"x0": gx0, "z0": gz0, "cell": cell, "rows": grid.shape[0], "cols": grid.shape[1], "d": np.round(grid, 2).ravel().tolist()}
    # Bridges' lanes, one-way and direction of travel (the deck may run the other way).
    net["bridge_lanes"] = {str(r["id"]): [r["lanes"], r["oneway"], *np.round(r["pts"][-1] - r["pts"][0], 1).tolist()] for r in roads if r["bridge"]}
    (out / "proto_roads.json").write_text(json.dumps(net, separators=(",", ":")))
    print(f"proto_roads.json: {len(net['roads'])} roads, {len(net['marks'])} crossings and stop lines")


if __name__ == "__main__":
    main(sys.argv[1:])
