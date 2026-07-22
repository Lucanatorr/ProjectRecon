"""Sprint 3.3 — multi-cycle persistence (FR-16): save/load finalized cycles."""
from __future__ import annotations

import pytest

from recon.models import AsBuiltLine, ContractItem, InvoiceLine, UoM
from recon.persistence import Database
from recon.reconcile import reconcile


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _cycle(db, project, cycle_no, contract, ab, iv, *, period, mode="cumulative",
           retainage=10.0, prior=0.0):
    rows = reconcile(ab, iv, contract)
    return db.save_cycle_snapshot(
        project_name=project, contractor="Rivr Tech", area="PON 5",
        cycle_no=cycle_no, period_label=period, billing_mode=mode,
        retainage_pct=retainage, prior_billed=prior, contract_items=contract, rows=rows)


def test_save_and_read_back_cycle(db):
    contract = [ContractItem("A", "Unit A", UoM.EA, 100.0, 10)]
    ab = [AsBuiltLine("A", 10, UoM.EA, code="A")]
    iv = [InvoiceLine("INV1", "A", 10, 100.0, 1000.0, code="A")]
    pid, cid = _cycle(db, "Job 1", 1, contract, ab, iv, period="Jan")

    results = db.load_results(cid)
    assert len(results) == 1
    assert results[0]["code"] == "A"
    assert results[0]["billed_qty"] == pytest.approx(10)

    summary = db.cycle_summary(cid)
    assert summary["cycle_no"] == 1
    assert summary["billed"] == pytest.approx(1000)
    assert summary["net"] == pytest.approx(900)         # 1000 − 0 flags − 100 retainage


def test_project_and_cycle_deduped_on_resave(db):
    contract = [ContractItem("A", "Unit A", UoM.EA, 100.0, 10)]
    ab = [AsBuiltLine("A", 10, UoM.EA, code="A")]
    iv = [InvoiceLine("INV1", "A", 10, 100.0, 1000.0, code="A")]
    _cycle(db, "Job 1", 1, contract, ab, iv, period="Jan")
    _cycle(db, "Job 1", 1, contract, ab, iv, period="Jan (revised)")   # same cycle

    assert len(db.list_projects()) == 1                 # not a second project
    pid = db.project_by_name("Job 1")["id"]
    cycles = db.list_cycles(pid)
    assert len(cycles) == 1                             # upserted, not duplicated
    assert db.cycle_summary(cycles[0]["id"])["period_label"] == "Jan (revised)"


def test_multiple_cycles_listed_in_order(db):
    contract = [ContractItem("A", "Unit A", UoM.EA, 100.0, 100)]
    ab1 = [AsBuiltLine("A", 30, UoM.EA, code="A")]
    iv1 = [InvoiceLine("INV1", "A", 30, 100.0, 3000.0, code="A")]
    ab2 = [AsBuiltLine("A", 70, UoM.EA, code="A")]
    iv2 = [InvoiceLine("INV2", "A", 70, 100.0, 7000.0, code="A")]
    _cycle(db, "Job 1", 1, contract, ab1, iv1, period="Jan")
    _cycle(db, "Job 1", 2, contract, ab2, iv2, period="Feb")

    pid = db.project_by_name("Job 1")["id"]
    summaries = db.cycle_summaries(pid)
    assert [s["cycle_no"] for s in summaries] == [1, 2]
    assert summaries[0]["billed"] == pytest.approx(3000)
    assert summaries[1]["billed"] == pytest.approx(7000)      # cumulative to-date


def test_saved_contract_reloads_with_change_orders(db):
    contract = [ContractItem("A", "Unit A", UoM.EA, 100.0, 10),
                ContractItem("B", "Unit B", UoM.EA, 50.0, 5, is_change_order=True)]
    ab = [AsBuiltLine("A", 10, UoM.EA, code="A")]
    iv = [InvoiceLine("INV1", "A", 10, 100.0, 1000.0, code="A")]
    pid, _ = _cycle(db, "Job 1", 1, contract, ab, iv, period="Jan")
    loaded = {c.code: c for c in db.load_contract(pid)}
    assert loaded["B"].is_change_order is True
    assert loaded["A"].is_change_order is False


def test_prior_cycle_billed_qty_available(db):
    # the data 3.4 (current-vs-prior) needs: prior cycle's per-unit billed-to-date
    contract = [ContractItem("A", "Unit A", UoM.EA, 100.0, 100)]
    _cycle(db, "Job 1", 1, contract, [AsBuiltLine("A", 30, UoM.EA, code="A")],
           [InvoiceLine("INV1", "A", 30, 100.0, 3000.0, code="A")], period="Jan")
    pid = db.project_by_name("Job 1")["id"]
    cyc1 = db.list_cycles(pid)[0]
    by_code = {r["code"]: r["billed_qty"] for r in db.load_results(cyc1["id"])}
    assert by_code["A"] == pytest.approx(30)
