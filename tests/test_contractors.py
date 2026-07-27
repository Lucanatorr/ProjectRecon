"""Sprint 5.0 — multi-contractor model: contractor registry, project links,
contractor-scoped billing cycles, portfolio stats for the Home page, and the
forward migration that adds billing_cycle.contractor_id to older databases."""
from __future__ import annotations

import sqlite3

import pytest

from recon.models import AsBuiltLine, ContractItem, InvoiceLine, UoM
from recon.persistence import Database
from recon.reconcile import reconcile


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _save(db, project, cycle_no, *, contractor, built, billed, period,
          price=10.0, retainage=10.0):
    contract = [ContractItem("A", "Unit A", UoM.EA, price, 1000)]
    rows = reconcile([AsBuiltLine("A", built, UoM.EA, code="A")],
                     [InvoiceLine("INV", "A", billed, price, billed * price, code="A")],
                     contract)
    return db.save_cycle_snapshot(
        project_name=project, contractor=contractor, area="Zone 2",
        cycle_no=cycle_no, period_label=period, billing_mode="cumulative",
        retainage_pct=retainage, prior_billed=0.0, contract_items=contract, rows=rows)


# --- contractor registry -------------------------------------------------- #
def test_get_or_create_contractor_is_deduped(db):
    a = db.get_or_create_contractor("Rivr Tech")
    b = db.get_or_create_contractor("Rivr Tech")           # same name
    c = db.get_or_create_contractor("Ace Fiber Constructors")
    assert a == b
    assert c != a
    assert [r["name"] for r in db.list_contractors()] == [
        "Ace Fiber Constructors", "Rivr Tech"]              # ordered by name


def test_link_contractor_is_idempotent_and_ordered(db):
    pid = db.get_or_create_project("Bladen FTTH")
    ace = db.get_or_create_contractor("Ace Fiber Constructors")
    pine = db.get_or_create_contractor("PineBelt Utilities")
    db.link_contractor(pid, ace)
    db.link_contractor(pid, ace)                            # duplicate link
    db.link_contractor(pid, pine)
    names = [c["name"] for c in db.project_contractors(pid)]
    assert names == ["Ace Fiber Constructors", "PineBelt Utilities"]


# --- save_cycle_snapshot wires the contractor up -------------------------- #
def test_saving_a_cycle_registers_and_links_the_contractor(db):
    _save(db, "Robeson CAB", 1, contractor="Rivr Tech",
          built=100, billed=100, period="Jul")
    pid = db.project_by_name("Robeson CAB")["id"]
    assert [c["name"] for c in db.project_contractors(pid)] == ["Rivr Tech"]
    assert db.cycle_summary(db.list_cycles(pid)[0]["id"])["contractor"] == "Rivr Tech"


def test_no_contractor_leaves_cycle_unattached(db):
    _save(db, "Legacy Job", 1, contractor=None, built=50, billed=50, period="Jan")
    pid = db.project_by_name("Legacy Job")["id"]
    assert db.project_contractors(pid) == []
    cyc = db.list_cycles(pid)[0]
    assert cyc["contractor_id"] is None
    assert db.cycle_summary(cyc["id"])["contractor"] is None


# --- cycles are separable per contractor ---------------------------------- #
def test_same_cycle_no_for_two_contractors_are_distinct(db):
    _save(db, "Duplin Backbone", 1, contractor="Rivr Tech",
          built=100, billed=120, period="Aug")
    _save(db, "Duplin Backbone", 1, contractor="Sandhills Directional",
          built=80, billed=80, period="Aug")
    pid = db.project_by_name("Duplin Backbone")["id"]

    assert len(db.list_cycles(pid)) == 2                    # not overwritten
    rivr = db.get_or_create_contractor("Rivr Tech")
    sand = db.get_or_create_contractor("Sandhills Directional")
    assert len(db.list_cycles(pid, rivr)) == 1
    assert len(db.list_cycles(pid, sand)) == 1
    assert db.cycle_summaries(pid, rivr)[0]["billed"] == pytest.approx(1200)
    assert db.cycle_summaries(pid, sand)[0]["billed"] == pytest.approx(800)


def test_resaving_a_contractor_cycle_overwrites(db):
    _save(db, "Duplin Backbone", 1, contractor="Rivr Tech",
          built=100, billed=120, period="Aug")
    _save(db, "Duplin Backbone", 1, contractor="Rivr Tech",
          built=100, billed=130, period="Aug (revised)")   # same key
    pid = db.project_by_name("Duplin Backbone")["id"]
    rivr = db.get_or_create_contractor("Rivr Tech")
    cycles = db.list_cycles(pid, rivr)
    assert len(cycles) == 1
    s = db.cycle_summary(cycles[0]["id"])
    assert s["period_label"] == "Aug (revised)"
    assert s["billed"] == pytest.approx(1300)


def test_prior_billed_is_scoped_to_the_contractor(db):
    # two contractors, overlapping cycle numbers, different to-date quantities
    _save(db, "Duplin Backbone", 1, contractor="Rivr Tech",
          built=100, billed=100, period="Jul")
    _save(db, "Duplin Backbone", 1, contractor="Sandhills Directional",
          built=40, billed=40, period="Jul")
    pid = db.project_by_name("Duplin Backbone")["id"]
    rivr = db.get_or_create_contractor("Rivr Tech")
    sand = db.get_or_create_contractor("Sandhills Directional")

    assert db.prior_billed_by_code(pid, 2, rivr) == {"A": pytest.approx(100)}
    assert db.prior_billed_by_code(pid, 2, sand) == {"A": pytest.approx(40)}


def test_trend_is_scoped_to_the_contractor(db):
    _save(db, "Duplin Backbone", 1, contractor="Rivr Tech",
          built=100, billed=100, period="Jul")
    _save(db, "Duplin Backbone", 1, contractor="Sandhills Directional",
          built=40, billed=60, period="Jul")
    pid = db.project_by_name("Duplin Backbone")["id"]
    rivr = db.get_or_create_contractor("Rivr Tech")
    t = db.trend(pid, rivr)
    assert len(t) == 1
    assert t[0]["billed_value"] == pytest.approx(1000)      # only Rivr's cycle


# --- portfolio stats for the Home page ------------------------------------ #
def test_project_stats_aggregates_the_portfolio(db):
    _save(db, "Bladen FTTH", 1, contractor="Ace Fiber Constructors",
          built=100, billed=100, period="Jul")
    _save(db, "Bladen FTTH", 2, contractor="PineBelt Utilities",
          built=200, billed=260, period="Aug")              # over-billed → flagged
    stats = {s["name"]: s for s in db.project_stats()}
    b = stats["Bladen FTTH"]
    assert sorted(b["contractors"]) == ["Ace Fiber Constructors", "PineBelt Utilities"]
    assert b["n_cycles"] == 2
    assert b["billed"] == pytest.approx(100 * 10 + 260 * 10)
    assert b["flagged"] > 0                                 # cycle 2 over-billed
    assert b["latest_period"] == "Aug"


def test_project_stats_empty_when_no_projects(db):
    assert db.project_stats() == []


# --- forward migration on a pre-5.0 database ------------------------------- #
def test_migration_adds_contractor_id_to_old_databases(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.executescript(
        """CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT, contractor TEXT,
                                 area TEXT, status TEXT, created_at TEXT);
           CREATE TABLE billing_cycle (id INTEGER PRIMARY KEY, project_id INTEGER,
                                       cycle_no INTEGER, period_label TEXT,
                                       billing_mode TEXT, retainage_pct REAL,
                                       prior_billed_to_date REAL, status TEXT,
                                       created_at TEXT);
           INSERT INTO project(id, name) VALUES (1, 'Old Job');
           INSERT INTO billing_cycle(id, project_id, cycle_no) VALUES (1, 1, 1);""")
    old.commit()
    old.close()

    db = Database(path)                                     # triggers _migrate
    try:
        cols = {r["name"] for r in
                db._conn.execute("PRAGMA table_info(billing_cycle)")}
        assert "contractor_id" in cols
        row = db._conn.execute(
            "SELECT contractor_id FROM billing_cycle WHERE id=1").fetchone()
        assert row["contractor_id"] is None                # old data preserved
        # and the new contractor tables now exist
        assert db.list_contractors() == []
    finally:
        db.close()
