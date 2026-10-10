"""Night lighting (flightsim/viewer/nightLights.js, featureGeometry.js lightData, sky.js),
run with Node when available: street lamps along roads (both sides of wide roads, beside
the carriageway, up on poles), blue taxiway edge lights; the dusk and night presets
switch the city's lights on, the day presets leave them off; the light material shows
only at night unless it is a light that shines by day too."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = r"""
const fg = await import("./featureGeometry.js");
const d = await import("./demCore.js");
const { TIMES } = await import("./sky.js");
const nl = await import("./nightLights.js");
const n = d.HEIGHT_CELLS + 1, h = new Float32Array(n * n).fill(5);
const tiles = { heights: (ix, iz) => (ix === 0 && iz === 0 ? h : null), landcover: () => null };
const out = {};
const road = (cls) => fg.lightData({ roads: { [cls]: [[100, 100, 460, 100]] } }, tiles);
const res = road("residential"), mot = road("motorway"), taxi = fg.lightData({ taxiway: [[100, 500, 400, 500]] }, tiles);
const zs = (l) => [...l.position].filter((_, i) => i % 3 === 2);
out.res = { n: res.position.length / 3, y: res.position[1], zs: [...new Set(zs(res))].sort((a, b) => a - b) };
out.mot = { n: mot.position.length / 3, zs: [...new Set(zs(mot).map((z) => Math.round(z)))].sort((a, b) => a - b), colour: [...mot.color.slice(0, 3)] };
out.taxi = { n: taxi.position.length / 3, colour: [...taxi.color.slice(0, 3)] };
out.none = fg.lightData({ roads: {} }, tiles);
out.night = Object.fromEntries(Object.entries(TIMES).map(([k, t]) => [k, t.night ?? 0]));
const m = nl.lightMaterial(5), day = nl.lightMaterial(5, { dayToo: 0.4 });
out.shared = m.uniforms.nightLevel === nl.nightLevel && day.uniforms.dayToo.value === 0.4 && m.uniforms.dayToo.value === 0;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("night")
    three = d / "node_modules" / "three"
    (three / "addons" / "objects").mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    shutil.copy(VIEWER / "vendor" / "addons" / "objects" / "Sky.js", three / "addons" / "objects" / "Sky.js")
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js","./addons/*":"./addons/*"}}')
    for f in ("featureGeometry.js", "demCore.js", "sky.js", "nightLights.js"):
        shutil.copy(VIEWER / f, d / f)
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT], cwd=d, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_street_lamps(result):
    r = result["res"]
    assert r["n"] == 10  # 360 m every 36 m
    assert r["y"] == pytest.approx(5 + 10)  # on 10 m poles over the ground
    assert r["zs"] == pytest.approx([100 - 4.5, 100 + 4.5])  # staggered side to side, 1 m beside a 7 m road
    m = result["mot"]
    assert m["n"] == 20 and m["zs"] == [87, 113]  # both sides of a 24 m motorway, every station
    assert m["colour"][2] > 0.9  # whiter on highways


def test_taxiway_lights_blue(result):
    t = result["taxi"]
    assert t["n"] == 20 and t["colour"][2] > t["colour"][0]
    assert result["none"] is None


def test_lights_follow_the_time_of_day(result):
    n = result["night"]
    assert n["night"] == 1 and n["dusk"] > 0.5
    assert n["morning"] == n["midday"] == n["afternoon"] == 0
    assert result["shared"]
