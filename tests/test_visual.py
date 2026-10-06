"""Viewer conditions (time of day, visibility, clouds): config, resolution of "auto",
the stream hello for live flights and replays, and the viewer's cloud and sky modules."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from flightsim.envs import load_env_config, make_env

ROOT = Path(__file__).parent.parent
ENVS = ROOT / "configs" / "envs"
VIEWER = ROOT / "flightsim" / "viewer"
NODE = shutil.which("node")


def test_defaults_and_auto_clouds_follow_the_wind():
    calm = make_env(load_env_config(ENVS / "altitude_heading_hold.yaml"))
    calm.reset(seed=3)
    assert calm.visual_conditions() == {"time_of_day": "afternoon", "visibility": "normal", "clouds": "few", "cloud_seed": 3}
    windy = make_env(load_env_config(ENVS / "manual_wind.yaml"))
    levels = set()
    for seed in range(30):
        windy.reset(seed=seed)
        levels.add((windy.wind["turbulence"], windy.visual_conditions()["clouds"]))
    assert {c for t, c in levels if t == "light"} <= {"scattered"} and {c for t, c in levels if t in ("moderate", "severe")} <= {"broken"}
    xw = make_env(load_env_config(ENVS / "approach_landing_crosswind.yaml"))
    xw.reset(seed=1119)  # ~19 kt at 20 ft
    assert xw.visual_conditions()["clouds"] == "broken"


def test_config_sets_and_validates_conditions():
    cfg = load_env_config(ENVS / "takeoff.yaml", {"visual": {"time_of_day": "evening", "visibility": "hazy", "clouds": "clear"}})
    env = make_env(cfg)
    env.reset(seed=0)
    assert env.visual_conditions() == {"time_of_day": "evening", "visibility": "hazy", "clouds": "clear", "cloud_seed": 0}
    with pytest.raises(ValueError, match="visual.time_of_day"):
        load_env_config(ENVS / "takeoff.yaml", {"visual": {"time_of_day": "night"}})
    plain = load_env_config(ENVS / "takeoff.yaml")
    assert "visual" not in json.loads(plain.config_json)  # absent: the config hash is unchanged


def test_live_and_replay_send_the_same_conditions(tmp_path):
    from flightsim.control.takeoff import load_takeoff_gains
    from flightsim.datalog import write_log
    from flightsim.envs.policies import TakeoffPolicy
    from flightsim.stream.sources import LiveSource, ReplaySource

    cfg = load_env_config(ENVS / "takeoff_crosswind.yaml", {"episode_s": 3.0, "visual": {"time_of_day": "morning"}})
    live = LiveSource(cfg, None, 5, policy=TakeoffPolicy(load_takeoff_gains(ROOT / "configs" / "takeoff_autopilot.yaml"), cfg.control_rate_hz))
    list(live.frames())
    path = write_log(tmp_path / "v.parquet", live._env.episode_result(), live._env.provenance())
    assert live.visual["time_of_day"] == "morning" and live.visual["cloud_seed"] == 5
    assert ReplaySource(path).visual == live.visual


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_cloud_fields_are_seeded_and_scale_with_the_amount(tmp_path):
    three = tmp_path / "node_modules" / "three"
    three.mkdir(parents=True)
    for f in ("three.module.js", "three.core.js"):
        shutil.copy(VIEWER / "vendor" / f, three / f)
    shutil.copytree(VIEWER / "vendor" / "addons", three / "addons")
    (three / "package.json").write_text('{"name":"three","type":"module","exports":{".":"./three.module.js","./addons/*":"./addons/*"}}')
    for f in ("clouds.js", "sky.js"):
        shutil.copy(VIEWER / f, tmp_path / f)
    script = """
const c = await import("./clouds.js");
const s = await import("./sky.js");
const count = (amount, seed) => { let n = 0; for (let tx = -2; tx <= 2; tx++) for (let tz = -2; tz <= 2; tz++) n += c.tilePuffs(seed, tx, tz, amount).length; return n; };
const a = JSON.stringify(c.tilePuffs(7, 0, 0, "scattered")), b = JSON.stringify(c.tilePuffs(7, 0, 0, "scattered"));
const other = JSON.stringify(c.tilePuffs(8, 0, 0, "scattered"));
const ys = c.tilePuffs(7, 1, -1, "broken").map((p) => p[1]);
const sun = s.sunDirection(90, 0), east = s.sunDirection(0, 90);
console.log(JSON.stringify({ same: a === b, differs: a !== other, counts: ["clear", "few", "scattered", "broken"].map((x) => count(x, 3)),
  ymin: Math.min(...ys), ymax: Math.max(...ys), sun: sun.toArray(), east: east.toArray(), times: Object.keys(s.TIMES) }));
"""
    out = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["same"] and r["differs"]
    clear, few, scattered, broken = r["counts"]
    assert clear == 0 < few < scattered < broken
    assert 900 < r["ymin"] and r["ymax"] < 2000  # bases ~3000 ft, tops below ~6500 ft
    assert r["sun"] == pytest.approx([0, 1, 0], abs=1e-9) and r["east"] == pytest.approx([1, 0, 0], abs=1e-9)
    assert r["times"] == ["morning", "midday", "afternoon", "evening"]
