"""Runway lighting (flightsim/viewer/runwayLighting.js), run with Node when available:
the standards' patterns (CAP 168 ed. 10 chapter 6 and appendix 6B, which follow ICAO
Annex 14) laid out from a runway descriptor and its ends' published lighting (the OMAD
and OMAA charts of the UAE AIP)."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

# A runway along +x (east): pavement x 0..3200, landing thresholds displaced 100 m in at
# both ends, 45 m wide, level at 5 m.
SCRIPT = """
const L = await import("./runwayLighting.js");
const rw = (lighting, centrelineM = null) => ({
  widthM: 45, centrelineM, pavement: [[0, 0], [3200, 0]],
  ends: [{ ident: "09", x: 100, z: 0, y: 5, dx: 1, dz: 0, lighting: lighting[0] },
         { ident: "27", x: 3100, z: 0, y: 5, dx: -1, dz: 0, lighting: lighting[1] }],
});
const omad = rw([{ threshold_flash: true, papi: { angle_deg: 3.5, meht_ft: 46.83, sides: "both" } },
                 { approach: { type: "simple", length_m: 660 }, threshold_flash: true, papi: { angle_deg: 3.5, meht_ft: 46.5, sides: "both", ils: true } }]);
const cat3 = rw([{ approach: { type: "cat3" }, papi: { angle_deg: 3, meht_ft: 68.44, sides: "both", ils: true } }, { approach: { type: "cat1" } }], 15);
const name = (c) => Object.entries(L.COLOURS).find(([, v]) => v === c)[0];
const flat = (ls) => ls.map((l) => ({ x: +l.p[0].toFixed(2), z: +l.p[2].toFixed(2), c: name(l.c), d: l.d, f: l.f }));
const out = {
  omad: flat(L.runwayLights(omad)), cat3: flat(L.runwayLights(cat3)), plain: flat(L.runwayLights(rw([null, null]))),
  papi: L.papiUnits(omad).map((u) => ({ x: u.p[0], z: u.p[2], y: u.p[1], angle: u.angle, ident: u.ident })),
  papiIls: L.papiUnits(cat3).map((u) => ({ x: u.p[0], z: u.p[2], angle: u.angle })),
  fallback: L.papiUnits(rw([null, null]), { angle_deg: 3, distance_m: 250, sides: "left" }).length,
  noFallbackWhenPublished: L.papiUnits(rw([{ approach: { type: "cat1" } }, null]), { angle_deg: 3, distance_m: 250, sides: "left" }).length,
};
// PAPI colours seen from points on the approach to runway 09 (units of its left side).
const units09 = L.papiUnits(omad).filter((u) => u.ident === "09" && u.p[2] < 0);
const seen = (deg, dist = 3000, off = 0) => units09.map((u) => {
  const eye = [u.p[0] - dist, u.p[1] + dist * Math.tan(deg * Math.PI / 180), off];
  const c = L.papiColour(u, eye);
  return c === null ? null : c[1] > 1 ? "white" : c[1] < 0.3 ? "red" : "pink";
});
out.seen = { on: seen(3.5), high: seen(4.5), low: seen(2.5), behind: units09.map((u) => L.papiColour(u, [u.p[0] + 500, 50, u.p[2]])), wide: seen(3.5, 3000, 1500) };
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out():
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=VIEWER, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def facing(lights, sign):
    """The faces shining toward -x (seen landing eastbound, runway 09: sign +1) or +x."""
    return [l for l in lights if l["d"] is not None and l["d"][0] == -sign]


def test_edge_lights_every_60_m_with_red_before_a_displaced_threshold_and_a_yellow_caution_zone(out):
    edges = [l for l in facing(out["omad"], 1) if abs(abs(l["z"]) - 23.5) < 0.01]  # 1 m beyond the 22.5 m half width
    xs = sorted({l["x"] for l in edges})
    assert max(b - a for a, b in zip(xs, xs[1:])) <= 60.0 and xs[0] == 0 and xs[-1] == 3200
    colour = {l["x"]: l["c"] for l in edges}
    assert all(c == "red" for x, c in colour.items() if x < 99)  # before 09's displaced threshold
    # Yellow over the upwind 600 m of the runway (a third of its 3200 m is more).
    assert all(c == "yellow" for x, c in colour.items() if x > 3200 - 600 + 1)
    assert all(c == "white" for x, c in colour.items() if 101 < x < 3200 - 600 - 1)
    # With centreline lights there is no caution zone.
    assert {l["c"] for l in facing(out["cat3"], 1) if abs(abs(l["z"]) - 23.5) < 0.01 and l["x"] > 101} == {"white"}


def test_threshold_green_toward_the_approach_end_lights_red_toward_the_runway(out):
    lights = out["omad"]
    green = [l for l in lights if l["c"] == "green" and l["x"] == 100]
    assert green and all(l["d"] == [-1, 0] for l in green)  # seen from the approach to 09 only
    assert any(abs(l["z"]) > 30 for l in green)  # wing bars (displaced threshold)
    ends = [l for l in lights if l["c"] == "red" and l["x"] == 3200 and l["d"] == [-1, 0]]
    assert len(ends) >= 6  # the far end seen from a 09 landing roll


def test_centreline_colour_coding(out):
    cl = [l for l in facing(out["cat3"], 1) if l["z"] == 0 and 100 <= l["x"] < 3200 and l["f"] == 0]  # (the end lights at 3200)
    toGo = {round(3200 - l["x"]): l["c"] for l in cl}
    assert all(c == "red" for t, c in toGo.items() if t <= 300)
    assert {c for t, c in toGo.items() if 300 < t <= 900} == {"red", "white"}
    assert all(c == "white" for t, c in toGo.items() if t > 900)
    assert min(b - a for a, b in zip(sorted(l["x"] for l in cl), sorted(l["x"] for l in cl)[1:])) == pytest.approx(15)


def test_category_iii_approach_touchdown_zone_side_rows_and_flashers(out):
    app = [l for l in facing(out["cat3"], 1) if l["x"] < 100]  # before 09's threshold (the approach)
    centre = [l for l in app if l["z"] == 0 and l["f"] == 0]
    assert 100 - min(l["x"] for l in centre) == pytest.approx(900)
    reds = [l for l in app if l["c"] == "red" and 8 < abs(l["z"]) < 15]  # the side row barrettes
    assert reds and max(100 - l["x"] for l in reds) == pytest.approx(270) and min(abs(l["z"]) for l in reds) == pytest.approx(9)
    bars = sorted({round(100 - l["x"]) for l in app if abs(l["z"]) > 3 and l["c"] == "white" and l["x"] < 100 - 30})  # beyond the centre barrette
    assert [150, 300, 450, 600, 750] == [b for b in bars if b in (150, 300, 450, 600, 750)]
    half300 = max(abs(l["z"]) for l in app if round(100 - l["x"]) == 300 and l["c"] == "white")
    assert half300 == pytest.approx(15, abs=2.7)  # the 300 m crossbar about 30 m wide
    flashers = sorted((l for l in app if l["f"] > 0), key=lambda l: l["f"])
    assert flashers[0]["x"] < flashers[-1]["x"]  # the sequence runs toward the threshold
    tdz = [l for l in facing(out["cat3"], 1) if 100 < l["x"] < 1100 and 9 <= abs(l["z"]) <= 13.5]
    assert tdz and max(l["x"] for l in tdz) - 100 <= 900
    # The CAT I end (27) has no touchdown zone or red side rows.
    assert not [l for l in facing(out["cat3"], -1) if l["c"] == "red" and 3100 < l["x"] < 3200 and 8 < abs(l["z"]) < 15]


def test_omad_simple_approach_and_flashing_threshold_lights(out):
    app27 = [l for l in facing(out["omad"], -1) if l["x"] > 3100 and l["f"] == 0]
    assert max(l["x"] for l in app27) - 3100 == pytest.approx(660)
    assert any(round(l["x"] - 3100) == 300 and abs(l["z"]) > 10 for l in app27)  # its 300 m crossbar
    flash = [l for l in out["omad"] if l["f"] == 1]
    assert len(flash) == 4  # a pair at each threshold
    assert not [l for l in out["omad"] if l["f"] > 1]  # no sequenced flashers without a precision system


def test_runway_without_published_lighting_has_edge_threshold_and_end_lights_only(out):
    assert {l["c"] for l in out["plain"]} == {"white", "green", "red"}
    assert not [l for l in out["plain"] if l["x"] < 0 or l["x"] > 3200]  # no approach lights
    assert out["fallback"] == 8  # a fallback PAPI on each end where asked
    assert out["noFallbackWhenPublished"] == 4  # only the end without published lighting


def test_papi_siting_from_the_published_meht(out):
    u09 = [u for u in out["papi"] if u["ident"] == "09"]
    assert len(u09) == 8  # both sides
    # MEHT 46.83 ft over the angle of the second unit from outside less 2' (3.5 deg - 10' - 2').
    d = 46.83 * 0.3048 / math.tan(math.radians(3.5 - 12 / 60))
    assert {round(u["x"] - 100, 1) for u in u09} == {round(d, 1)}
    assert sorted(round(abs(u["z"]), 1) for u in u09 if u["z"] < 0) == [37.5, 46.5, 55.5, 64.5]  # 15 m beyond the edge, 9 m apart
    nearest = min((u for u in u09 if u["z"] < 0), key=lambda u: abs(u["z"]))
    assert math.degrees(nearest["angle"]) == pytest.approx(3.5 + 30 / 60)
    ils = sorted(round(math.degrees(u["angle"]) * 60) for u in out["papiIls"] if u["x"] < 1600 and u["z"] < 0)
    assert ils == [180 - 25, 180 - 15, 180 + 15, 180 + 35]


def test_papi_colours_from_the_eye(out):
    s = out["seen"]
    by_dist = lambda colours: colours  # units ordered from the runway outward (papiUnits)
    assert by_dist(s["on"]) == ["red", "red", "white", "white"] or by_dist(s["on"]).count("white") == 2
    assert s["high"] == ["white"] * 4
    assert s["low"] == ["red"] * 4
    assert s["behind"] == [None] * 4
    assert s["wide"] == [None] * 4  # outside the beam's 15 deg
