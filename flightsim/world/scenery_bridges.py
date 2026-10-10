"""Bridges of a real-world region (the scenery build, visual only): OSM's road and railway
ways on bridges, joined where they continue one another, with a deck height profile.

FABDEM is bare earth: it has no bridges, so the ground under a bridge is the water or the
land it crosses. The deck runs from its two ends (where OSM's bridge way meets the
road on the ground: their ground heights) in a straight line, raised where it must
clear what is under it: over land 1 m above the ground (an overpass's embankments are in
the ground model; the deck spans between them), over water `clearance_m` above it, with
ramps no steeper than `grade`. A landmark bridge (the region file's `bridges`) sets its
own clearance, grade and structure; the viewer (viewer/bridges.js) builds the decks,
piers and the landmarks' superstructures. The physics flies through bridges as through
buildings.
"""

from __future__ import annotations

import math

import numpy as np

STEP_M = 5.0  # profile computed every STEP_M along the bridge
SAMPLE_M = 10.0  # and stored every SAMPLE_M (plus every original corner)
LAND_CLEARANCE_M = 1.0
WATER_CLEARANCE_M = 8.0  # unnamed bridges over water: under the deck (project choice)
DECK_DEPTH_M = 1.8  # road surface to the deck's underside (project choice)
SEA_BAND_M = 1.0
GRADE = 0.05  # steepest ramp (project choice; motorway ramps rarely exceed 5 %)
LANE_M = 3.6
SHOULDERS_M = 2.5
CLASS_WIDTH_M = {"motorway": 15, "trunk": 13, "primary": 11, "secondary": 9, "tertiary": 8, "unclassified": 7, "residential": 7, "rail": 6}


def join(ways: list[dict]) -> list[list[dict]]:
    """Chains of bridge ways that continue one another (a shared end node where exactly
    two bridge ways of the same kind end), each way oriented along its chain."""
    ends: dict[int, list[int]] = {}
    for i, w in enumerate(ways):
        for n in (w["nodes"][0], w["nodes"][-1]):
            ends.setdefault(n, []).append(i)

    def partner(i: int, node: int) -> int | None:
        others = [j for j in ends.get(node, []) if j != i]
        if len(ends.get(node, [])) == 2 and len(others) == 1 and ways[others[0]]["kind"] == ways[i]["kind"]:
            return others[0]
        return None

    used, chains = set(), []
    for start in range(len(ways)):
        if start in used:
            continue
        # Walk back to the chain's first way, then forward collecting.
        i, node, seen = start, ways[start]["nodes"][0], {start}
        while (j := partner(i, node)) is not None and j not in seen:
            seen.add(j)
            node = ways[j]["nodes"][-1] if ways[j]["nodes"][0] == node else ways[j]["nodes"][0]
            i = j
        first, chain = i, []
        # `node` is the free end of `first`: orient it to start there.
        i, at = first, node
        while i is not None and i not in used:
            used.add(i)
            w = ways[i]
            fwd = w["nodes"][0] == at
            chain.append({**w, "nodes": w["nodes"] if fwd else w["nodes"][::-1], "pts": w["pts"] if fwd else w["pts"][::-1]})
            at = chain[-1]["nodes"][-1]
            i = partner(i, at)
        chains.append(chain)
    return chains


def _polyline(chain: list[dict]) -> np.ndarray:
    pts = [tuple(p) for p in chain[0]["pts"]]
    for w in chain[1:]:
        pts += [tuple(p) for p in w["pts"][1:]]
    return np.array(pts, float)


def width_m(chain: list[dict]) -> float:
    w = chain[0]
    if w["lanes"]:
        return w["lanes"] * LANE_M + SHOULDERS_M
    return float(CLASS_WIDTH_M.get(w["cls"], 7))


def profile(line: np.ndarray, ground, water, clearance_m: float = WATER_CLEARANCE_M, grade: float = GRADE, depth_m: float = DECK_DEPTH_M) -> dict:
    """The deck along a polyline ([[x, z], ...]): {"pts": [[x, z, road_y, ground_y, water], ...],
    "length_m"}: the road surface's height, `clearance_m` + `depth_m` above water.
    `ground(x, z)` -> height; `water(x, z)` -> bool."""
    seg = np.diff(line, axis=0)
    lens = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(lens)])
    L = float(cum[-1])
    s = np.unique(np.concatenate([np.arange(0.0, L, STEP_M), [L], cum]))
    xs, zs = np.interp(s, cum, line[:, 0]), np.interp(s, cum, line[:, 1])
    g = np.array([ground(x, z) for x, z in zip(xs, zs)])
    # Water: WorldCover's (which maps a bridge's own deck as built-up) or bare earth within
    # SEA_BAND_M of sea level; its surface is sea level.
    wet = np.array([water(x, z) for x, z in zip(xs, zs)]) | (g <= SEA_BAND_M)
    need = np.where(wet, clearance_m + depth_m, g + LAND_CLEARANCE_M)
    need[0] = need[-1] = -np.inf  # the ends are the roads they join, not ground to clear
    # Raised where needed, ramps no steeper than `grade` (the upper envelope of cones).
    raise_ = np.max(need[None, :] - grade * np.abs(s[:, None] - s[None, :]), axis=1)
    deck = np.maximum(np.interp(s, [0, L], [g[0], g[-1]]), raise_)
    deck[0], deck[-1] = g[0], g[-1]  # the ends meet the roads on the ground
    keep = np.isin(s, cum) | (np.mod(s, SAMPLE_M) < 1e-6) | (s == L)
    pts = [[round(float(x), 1), round(float(z), 1), round(float(d), 2), round(float(h), 2), int(w)]
           for x, z, d, h, w, k in zip(xs, zs, deck, g, wet, keep) if k]  # fmt: skip
    return {"pts": pts, "length_m": round(L, 1)}


def build_bridges(ways: list[dict], ground, water, landmarks: list[dict] | None = None) -> list[dict]:
    """bridges.json: one entry per chain ({"ids", "kind", "cls", "name", "width_m",
    "length_m", "pts"}), and for chains of a landmark bridge (`landmarks`: the region
    file's entries, matched by OSM way id in `ways` or by `osm_name`) its name under
    "landmark" with its clearance and grade used for the profile."""
    out = []
    for chain in join(ways):
        ids = [w["id"] for w in chain]
        names = {w["name"] for w in chain if w["name"]}
        mark = next((m for m in landmarks or [] if set(m.get("ways", [])) & set(ids) or (m.get("osm_name") in names)), None)
        m = mark or {}
        prof = profile(_polyline(chain), ground, water, m.get("clearance_m", WATER_CLEARANCE_M), m.get("grade", GRADE), m.get("deck_depth_m", DECK_DEPTH_M))
        entry = {"ids": ids, "kind": chain[0]["kind"], "cls": chain[0]["cls"], "name": next(iter(sorted(names)), None),
                 "width_m": round(width_m(chain), 1), "depth_m": m.get("deck_depth_m", DECK_DEPTH_M), **prof}  # fmt: skip
        if mark:
            entry["landmark"] = mark["name"]
            entry["structure"] = {k: v for k, v in mark.items() if k not in ("name", "ways", "osm_name")}
        out.append(entry)
    out.sort(key=lambda b: b["ids"][0])
    return out


def landmark_summary(bridges: list[dict]) -> dict[str, dict]:
    """Per landmark bridge: its chains' total length and highest deck (for the build log)."""
    res: dict[str, dict] = {}
    for b in bridges:
        if "landmark" in b:
            r = res.setdefault(b["landmark"], {"chains": 0, "length_m": 0.0, "deck_max_m": -math.inf})
            r["chains"] += 1
            r["length_m"] += b["length_m"]
            r["deck_max_m"] = max(r["deck_max_m"], max(p[2] for p in b["pts"]))
    return res
