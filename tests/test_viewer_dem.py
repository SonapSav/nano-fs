"""Real-world terrain tiles in the viewer (flightsim/viewer/demTiles.js), run with Node: at
full detail the vertices are the physics' height posts (the sea too: the ground at 0 m);
the land cover texture has one texel per WorldCover cell with the sea painted in; trees
grow only where WorldCover has trees (or shrubs); no tile outside the region. The server serves a
built region's files (not its sources, nothing outside data/scenery)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEWER = Path(__file__).parent.parent / "flightsim" / "viewer"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_dem_tile_data():
    script = f"""
    const d = await import({json.dumps((VIEWER / "demCore.js").as_uri())});
    const t = await import({json.dumps((VIEWER / "demTiles.js").as_uri())});
    const n = d.HEIGHT_CELLS + 1, L = d.LANDCOVER_CELLS;
    // One tile (0, 0): heights rising east, 2..130 m; land cover: water in the west quarter,
    // trees in the north-east quarter, desert elsewhere.
    const h = new Float32Array(n * n);
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) h[r * n + c] = 2 + c;
    const lc = new Uint8Array(L * L).fill(60);
    for (let r = 0; r < L; r++) for (let c = 0; c < L; c++) {{
      if (c < L / 4) lc[r * L + c] = 80;
      else if (c >= L / 2 && r < L / 2) lc[r * L + c] = 10;
    }}
    const tiles = {{ heights: (ix, iz) => (ix === 0 && iz === 0 ? h : null), landcover: (ix, iz) => (ix === 0 && iz === 0 ? lc : null) }};
    const g = t.demTileGeometryData(0, 0, d.HEIGHT_CELLS, tiles);
    const at = (i, j) => [g.position[3 * (i * n + j)], g.position[3 * (i * n + j) + 1], g.position[3 * (i * n + j) + 2]];
    const objects = t.demTileObjectsData(0, 0, 500, false, tiles);
    const trees = [];
    for (let k = 0; k < objects.trees.length; k += 4) trees.push([objects.trees[k], objects.trees[k + 2]]);
    const texel = (r, c) => [...g.texture.slice(4 * (r * g.textureSize + c), 4 * (r * g.textureSize + c) + 3)];
    console.log(JSON.stringify({{
      landPost: at(10, 100), physics: d.heightAt(tiles, at(10, 100)[0], at(10, 100)[2]),
      waterPost: at(10, 5), size: g.textureSize, uvLast: [...g.uv.slice(2 * (n * n - 1), 2 * n * n)],
      texWater: texel(5, 5), texDesert: texel(200, 100), texTree: texel(10, 200),
      trees: trees.length, treesOk: trees.every(([x, z]) => d.landcoverAt(tiles, x, z) === 10),
      outside: t.demTileGeometryData(3, 3, 32, tiles),
    }}));
    """
    out = json.loads(subprocess.run([NODE, "--input-type=module"], input=script, capture_output=True, text=True, check=True).stdout)
    assert out["landPost"][1] == out["physics"] == pytest.approx(102.0)  # the post's height, as the physics
    assert out["waterPost"][1] == pytest.approx(7.0)  # water is drawn by colour, on the physics' ground
    assert out["size"] == 256 and out["uvLast"] == [1, 1]
    assert out["texWater"] == [0x3B, 0x7D, 0x93] and out["texTree"] == [0x4B, 0x6A, 0x3C]
    assert out["texDesert"][0] > out["texDesert"][2] > 100  # sand (warmer on this 50 m "dune")
    assert out["trees"] > 100 and out["treesOk"]
    assert out["outside"] is None


def test_server_serves_scenery_files_only(tmp_path, monkeypatch):
    from flightsim.stream import server

    region = tmp_path / "r"
    (region / "tiles").mkdir(parents=True)
    (region / "sources").mkdir()
    (region / "manifest.json").write_text("{}")
    (region / "tiles" / "h_0_0.f32").write_bytes(b"\0" * 8)
    (region / "sources" / "x.osm.pbf").write_bytes(b"secret")
    (tmp_path / "other.json").write_text("{}")
    monkeypatch.setattr(server, "SCENERY_DIR", tmp_path)
    assert server._scenery_response("/scenery/r/manifest.json?h=abc").status_code == 200
    assert server._scenery_response("/scenery/r/tiles/h_0_0.f32").body == b"\0" * 8
    assert server._scenery_response("/scenery/r/sources/x.osm.pbf").status_code == 404
    assert server._scenery_response("/scenery/r/../../etc/passwd").status_code == 404
