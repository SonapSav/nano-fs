import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from flightsim import provenance
from flightsim.batch import make_manifest
from flightsim.config import load_raw
from flightsim.datalog import read_log, write_log
from flightsim.datalog import schema as S
from flightsim.runner import run

from conftest import shortened

ROOT = Path(__file__).parent.parent
WINDY = ROOT / "configs" / "envs" / "altitude_heading_hold_wind.yaml"
AUTOPILOT = ROOT / "configs" / "autopilot.yaml"


def test_code_version_fields():
    v = provenance.code_version()
    assert set(v) == {"source_sha256", "git_commit", "git_dirty", "git_diff_sha256"}
    assert len(v["source_sha256"]) == 64


def test_source_hash_tracks_any_python_change(tmp_path):
    copy = tmp_path / "flightsim"
    shutil.copytree(provenance.PACKAGE_DIR, copy, ignore=shutil.ignore_patterns("__pycache__", "vendor"))
    before = provenance.source_sha256(copy)
    assert before == provenance.source_sha256(provenance.PACKAGE_DIR)
    (copy / "core" / "jsbsim_core.py").write_text((copy / "core" / "jsbsim_core.py").read_text() + "\n# change\n")
    assert provenance.source_sha256(copy) != before
    (copy / "new_module.py").write_text("x = 1\n")  # a new file counts too
    assert provenance.source_sha256(copy) != before


def test_git_fields_are_none_without_a_repository(monkeypatch):
    monkeypatch.setattr(provenance, "_git", lambda *a: None)
    provenance.code_version.cache_clear()
    try:
        v = provenance.code_version()
        assert v["git_commit"] is None and v["git_dirty"] is None and v["git_diff_sha256"] is None
        assert v["source_sha256"] == provenance.source_sha256()
    finally:
        provenance.code_version.cache_clear()


def test_logs_record_the_code_version(tmp_path):
    cfg = shortened(1.0)
    _, meta = read_log(write_log(tmp_path / "run.parquet", run(cfg), cfg.provenance))
    assert json.loads(meta[S.META_CODE_VERSION]) == provenance.code_version()


def _batch_id(monkeypatch, version):
    monkeypatch.setattr("flightsim.batch.code_version", lambda: version)
    return make_manifest(load_raw(WINDY), "pid", load_raw(AUTOPILOT), [0], False)


def test_batch_id_follows_source_not_git_state(monkeypatch):
    base = {"source_sha256": "a" * 64, "git_commit": "c1", "git_dirty": True, "git_diff_sha256": "d1"}
    m1 = _batch_id(monkeypatch, base)
    m2 = _batch_id(monkeypatch, {**base, "git_commit": "c2", "git_dirty": False, "git_diff_sha256": None})
    m3 = _batch_id(monkeypatch, {**base, "source_sha256": "b" * 64})
    assert m1["batch_id"] == m2["batch_id"]  # same code, committed or not
    assert m1["batch_id"] != m3["batch_id"]  # code changed
    assert m1["code_version"]["git_commit"] == "c1"  # git state is still recorded


def test_lqr_cache_key_includes_source(monkeypatch, tmp_path):
    from flightsim.control import lqr

    keys = []
    real_cls = lqr.GainSchedule

    def fake_init(self, aircraft, loading, cfg, dt_s, points=None):
        self.cfg, self.points = cfg, [[lqr.DesignPoint(*([np.zeros(1)] * 4))]]

    monkeypatch.setattr(real_cls, "__init__", fake_init)
    from flightsim.envs import load_env_config

    env = load_env_config(WINDY)
    raw = load_raw(ROOT / "configs" / "lqr.yaml")
    for src in ("x" * 64, "y" * 64):
        monkeypatch.setattr(lqr, "source_sha256", lambda src=src: src)
        real_cls.cached(env.aircraft, env.loading, raw, 0.05, tmp_path)
    assert len(list(tmp_path.glob("*.npz"))) == 2
