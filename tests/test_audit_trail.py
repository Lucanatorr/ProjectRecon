"""Sprint 4.4 — audit trail (FR-17) and globally persisted crosswalk aliases (FR-7)."""
from __future__ import annotations

import json

import pytest

from recon.ingest.normalize import normalize
from recon.persistence import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _actions(db):
    return [r["action"] for r in db.audit_trail(50)]


def test_log_records_actor_action_and_detail(db):
    db.log("Lucas", "load_contract", "contract", None,
           {"source": "BidSchedule.xlsx", "units": 11})
    entry = db.audit_trail(1)[0]
    assert entry["actor"] == "Lucas"
    assert entry["action"] == "load_contract"
    assert entry["entity"] == "contract"
    assert json.loads(entry["detail_json"])["units"] == 11
    assert entry["ts"]                      # timestamped by the database


def test_audit_trail_is_newest_first(db):
    for i in range(3):
        db.log(None, f"action_{i}", "test")
    assert _actions(db) == ["action_2", "action_1", "action_0"]


def test_audit_trail_respects_limit(db):
    for i in range(10):
        db.log(None, f"a{i}", "test")
    assert len(db.audit_trail(4)) == 4


def test_alias_confirmation_is_logged(db):
    db.confirm_alias("144F ADSS Aerial Place", "3.1", actor="Lucas")
    assert "confirm_alias" in _actions(db)
    entry = db.audit_trail(1)[0]
    assert json.loads(entry["detail_json"])["code"] == "3.1"


def test_alias_deletion_is_logged(db):
    db.confirm_alias("Some text", "3.1")
    db.delete_alias("Some text", actor="Lucas")
    assert _actions(db)[0] == "delete_alias"


# --- aliases persist globally so the crosswalk gets smarter across jobs (FR-7) ---
def test_confirmed_alias_survives_and_reloads(db):
    db.confirm_alias("Directional Drilling 2 inch", "4.1", actor="Lucas")
    store = db.load_alias_store()
    assert store.get(normalize("Directional Drilling 2 inch")) == "4.1"
    # normalization means casing/punctuation variants hit the same alias
    assert store.get(normalize("directional drilling 2 inch")) == "4.1"


def test_alias_is_upserted_not_duplicated(db):
    db.confirm_alias("Widget", "1.1")
    db.confirm_alias("Widget", "2.2")           # reviewer changed their mind
    store = db.load_alias_store()
    assert store.get(normalize("Widget")) == "2.2"
    assert len(store) == 1


def test_deleted_alias_leaves_the_store(db):
    db.confirm_alias("Widget", "1.1")
    db.delete_alias("Widget")
    assert db.load_alias_store().get(normalize("Widget")) is None


def test_alias_store_reused_by_the_matcher(db):
    """A mapping confirmed on a past job auto-resolves on the next one."""
    from recon.crosswalk import resolve
    from recon.models import ContractItem, UoM

    db.confirm_alias("Contractor's odd wording", "9.1")
    contract = [ContractItem("9.1", "Drop placement", UoM.EA, 145.0, 400)]
    m = resolve("Contractor's odd wording", contract, db.load_alias_store())
    assert m.code == "9.1"
    assert m.kind == "alias"
