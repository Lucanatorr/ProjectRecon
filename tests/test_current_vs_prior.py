"""Sprint 3.4 — current-vs-prior validation for cumulative pay apps (FR-8, SDD §7.5)."""
from __future__ import annotations

import pytest

from config import RECON
from recon.models import AsBuiltLine, ContractItem, InvoiceLine, Severity, UoM
from recon.persistence import Database
from recon.reconcile import reconcile


def _row(billed, prior=None, built=None, contract_price=10.0, cumulative=True):
    built = billed if built is None else built
    ci = ContractItem("A", "Unit A", UoM.EA, contract_price, 1000)
    ab = [AsBuiltLine("A", built, UoM.EA, code="A")]
    iv = [InvoiceLine("INV1", "A", billed, contract_price, billed * contract_price, code="A")]
    cfg = RECON.__class__(tolerance=RECON.tolerance, matching=RECON.matching,
                         cumulative=cumulative)
    prior = {"A": prior} if prior is not None else None
    return reconcile(ab, iv, [ci], cfg, prior_billed=prior)[0]


def test_current_period_computed_from_prior():
    r = _row(billed=250, prior=100)
    assert r.prior_billed_qty == pytest.approx(100)
    assert r.current_period_qty == pytest.approx(150)      # 250 to-date − 100 prior


def test_no_prior_leaves_period_undefined():
    r = _row(billed=250)                                   # no prior cycle
    assert r.prior_billed_qty is None
    assert r.current_period_qty is None
    assert "cumulative_decrease" not in {f.rule for f in r.flags}


def test_cumulative_increase_is_clean():
    r = _row(billed=250, prior=100)
    assert "cumulative_decrease" not in {f.rule for f in r.flags}


def test_cumulative_decrease_flagged():
    r = _row(billed=80, prior=100, built=80)              # to-date went backwards
    assert "cumulative_decrease" in {f.rule for f in r.flags}
    assert r.severity == Severity.WARNING
    assert r.current_period_qty == pytest.approx(-20)


def test_decrease_ignored_in_discrete_mode():
    r = _row(billed=80, prior=100, cumulative=False)
    assert r.prior_billed_qty is None                     # prior not applied when discrete
    assert "cumulative_decrease" not in {f.rule for f in r.flags}


def test_prior_billed_by_code_from_persistence():
    db = Database(":memory:")
    try:
        contract = [ContractItem("A", "Unit A", UoM.EA, 10.0, 1000)]
        rows1 = reconcile([AsBuiltLine("A", 100, UoM.EA, code="A")],
                          [InvoiceLine("INV1", "A", 100, 10.0, 1000.0, code="A")], contract)
        db.save_cycle_snapshot(
            project_name="Job", contractor=None, area=None, cycle_no=1,
            period_label="Jan", billing_mode="cumulative", retainage_pct=10.0,
            prior_billed=0.0, contract_items=contract, rows=rows1)
        pid = db.project_by_name("Job")["id"]
        prior = db.prior_billed_by_code(pid, before_cycle_no=2)
        assert prior == {"A": pytest.approx(100)}
        # nothing before cycle 1
        assert db.prior_billed_by_code(pid, before_cycle_no=1) == {}
    finally:
        db.close()
