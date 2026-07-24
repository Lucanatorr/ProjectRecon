"""Sprint 4.2 — reviewer resolution & sign-off on flagged rows (FR-14)."""
from __future__ import annotations

import sqlite3

import pytest

from recon.models import AsBuiltLine, ContractItem, InvoiceLine, UoM
from recon.persistence import Database
from recon.reconcile import reconcile
from ui.state import (
    WizardState,
    clear_resolution,
    row_key,
    set_resolution,
    unresolved_criticals,
)


def _state_with_results():
    contract = [ContractItem("A", "Unit A", UoM.EA, 10.0, 100)]
    ab = [AsBuiltLine("A", 10, UoM.EA, code="A")]
    iv = [InvoiceLine("INV", "A", 50, 10.0, 500.0, code="A"),          # qty over
          InvoiceLine("INV", "Traffic control", 5, 100.0, 500.0, code=None)]  # no contract
    s = WizardState()
    s.contract = contract
    s.results = reconcile(ab, iv, contract)
    return s


def test_row_key_falls_back_to_description():
    s = _state_with_results()
    keys = {row_key(r) for r in s.results}
    assert "A" in keys                       # coded row keyed by code
    assert "Traffic control" in keys         # unmatched row keyed by description


def test_set_and_clear_resolution():
    s = _state_with_results()
    set_resolution(s, "A", "hold", note="await field verification", by="LC")
    res = s.resolutions["A"]
    assert res["status"] == "hold"
    assert res["note"] == "await field verification"
    assert res["by"] == "LC"
    assert res["at"]                         # timestamped

    clear_resolution(s, "A")
    assert "A" not in s.resolutions


def test_note_none_keeps_existing_but_empty_clears():
    s = _state_with_results()
    set_resolution(s, "A", "hold", note="first reason", by="LC")
    set_resolution(s, "A", "approve")                     # note=None -> keep
    assert s.resolutions["A"]["note"] == "first reason"
    assert s.resolutions["A"]["status"] == "approve"
    set_resolution(s, "A", "approve", note="")            # explicit clear
    assert s.resolutions["A"]["note"] == ""


def test_reviewer_defaults_from_state():
    s = _state_with_results()
    s.reviewer = "Lucas"
    set_resolution(s, "A", "hold")
    assert s.resolutions["A"]["by"] == "Lucas"


def test_unresolved_criticals_gate():
    s = _state_with_results()
    criticals = [r for r in s.results if r.severity.value == "critical"]
    assert criticals                                        # fixture has some
    assert len(unresolved_criticals(s)) == len(criticals)

    for r in criticals:
        set_resolution(s, row_key(r), "hold", by="LC")
    assert unresolved_criticals(s) == []                    # all decided


# --- persistence ---
def test_resolutions_persist_with_the_cycle():
    db = Database(":memory:")
    try:
        s = _state_with_results()
        set_resolution(s, "A", "hold", note="await field verification", by="LC")
        db.save_cycle_snapshot(
            project_name="Job", contractor=None, area=None, cycle_no=1,
            period_label="Jan", billing_mode="cumulative", retainage_pct=10.0,
            prior_billed=0.0, contract_items=s.contract, rows=s.results,
            resolutions=s.resolutions)
        pid = db.project_by_name("Job")["id"]
        cid = db.list_cycles(pid)[0]["id"]
        stored = {r["code"]: r for r in db.load_results(cid)}
        assert stored["A"]["resolution"] == "hold"
        assert stored["A"]["resolution_note"] == "await field verification"
        assert stored["A"]["resolved_by"] == "LC"
        assert stored["A"]["resolved_at"]
        # an untouched row stays unresolved
        unmatched = [r for r in db.load_results(cid) if r["code"] is None][0]
        assert unmatched["resolution"] is None
    finally:
        db.close()


def test_migration_adds_resolution_note_to_older_db(tmp_path):
    """A database created before the note column gains it on next open."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute("""CREATE TABLE recon_result (
        id INTEGER PRIMARY KEY, cycle_id INTEGER, code TEXT, description TEXT,
        uom TEXT, built_qty REAL, billed_qty REAL, contract_price REAL,
        billed_price REAL, est_qty REAL, billed_amount REAL, expected_amount REAL,
        variance REAL, severity TEXT, flags_json TEXT, resolution TEXT,
        resolved_by TEXT, resolved_at TEXT)""")
    con.commit()
    con.close()

    db = Database(path)                       # opening runs the migration
    try:
        cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(recon_result)")}
        assert "resolution_note" in cols
    finally:
        db.close()
