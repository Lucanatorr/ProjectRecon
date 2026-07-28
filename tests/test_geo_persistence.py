"""Geo-1 — per-project feature_type → code crosswalk persistence, the ui.db
wrappers, and the GeoJSON → quantities pipeline (incl. the shipped sample file)."""
from __future__ import annotations

import json

import pytest

from config import ROOT
from recon.geo.pipeline import derive_geojson
from recon.persistence import Database

SAMPLE = ROOT / "samples" / "AsBuilt_PhaseB_map.geojson"


# --- persistence ---------------------------------------------------------- #
def test_feature_code_map_upserts_and_deletes():
    db = Database(":memory:")
    try:
        pid = db.get_or_create_project("Job")
        assert db.feature_code_map(pid) == {}
        db.set_feature_code(pid, "handhole", "HH-100")
        db.set_feature_code(pid, "pole", "PL-9")
        assert db.feature_code_map(pid) == {"handhole": "HH-100", "pole": "PL-9"}
        db.set_feature_code(pid, "handhole", "HH-200")          # upsert
        assert db.feature_code_map(pid)["handhole"] == "HH-200"
        db.delete_feature_code(pid, "pole")
        assert "pole" not in db.feature_code_map(pid)
    finally:
        db.close()


def test_feature_code_map_is_scoped_per_project():
    db = Database(":memory:")
    try:
        a = db.get_or_create_project("A")
        b = db.get_or_create_project("B")
        db.set_feature_code(a, "handhole", "A-1")
        assert db.feature_code_map(b) == {}                    # not shared
    finally:
        db.close()


# --- ui.db wrappers ------------------------------------------------------- #
def test_wrappers_persist_only_for_saved_projects(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    monkeypatch.setattr("recon.persistence.DB_PATH", path)
    from ui.db import feature_code_overrides, set_feature_code

    assert feature_code_overrides("Ghost") == {}
    assert set_feature_code("Ghost", "handhole", "X") is False  # unsaved → no-op

    Database(path).get_or_create_project("Real")
    assert set_feature_code("Real", "handhole", "HH-1") is True
    assert feature_code_overrides("Real") == {"handhole": "HH-1"}


# --- pipeline ------------------------------------------------------------- #
def test_derive_geojson_accepts_dict_and_string():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"feature_type": "handhole"},
         "geometry": {"type": "Point", "coordinates": [-79.0, 34.61]}}]}
    lines_d, _ = derive_geojson(fc)
    lines_s, _ = derive_geojson(json.dumps(fc))
    assert lines_d[0].code == lines_s[0].code == "5.1"
    assert lines_d[0].qty == 1


def test_sample_geojson_derives_expected_quantities():
    lines, unmapped = derive_geojson(SAMPLE.read_text(encoding="utf-8"))
    assert unmapped == []                                      # all canonical types
    by_code = {ln.code: ln for ln in lines}
    assert by_code["5.1"].qty == 6                             # handholes
    assert by_code["8.1"].qty == 5                             # poles
    assert by_code["6.2"].qty == 2                             # splice closures
    assert by_code["5.2"].qty == 1                             # pedestal
    assert by_code["9.1"].qty == 3                             # drops (count, not length)
    # linear runs derive positive footage
    assert by_code["3.1"].qty > 0 and by_code["3.1"].uom.value == "FT"
    assert by_code["4.2"].qty > 0                              # buried
    assert by_code["4.1"].qty > 0                              # bore


def test_sample_respects_a_project_override():
    lines, _ = derive_geojson(SAMPLE.read_text(encoding="utf-8"),
                              {"handhole": "STRUCT-01"})
    by_code = {ln.code: ln for ln in lines}
    assert "STRUCT-01" in by_code and "5.1" not in by_code
    assert by_code["STRUCT-01"].qty == 6
