"""Golden-file regression: reconcile the mockup's hand-reconciled cycle and assert
the exact flag set and dollar variances. This is the primary guard against logic
drift (SDD §11)."""
from __future__ import annotations

import pytest

from recon.reconcile import cycle_totals, reconcile
from recon.models import Severity


@pytest.fixture
def rows(golden_asbuilt, golden_invoices, golden_contract):
    result = reconcile(golden_asbuilt, golden_invoices, golden_contract)
    return {r.code: r for r in result}


def _rules(row):
    return {f.rule for f in row.flags}


def test_qty_over_31(rows):
    r = rows["3.1"]
    assert r.qty_delta == pytest.approx(2580)
    assert r.amount_variance == pytest.approx(4773.0)   # 2580 * 1.85
    assert "qty_over" in _rules(r)
    assert r.severity == Severity.CRITICAL


def test_price_over_41(rows):
    r = rows["4.1"]
    assert r.qty_delta == pytest.approx(0)
    assert r.price_delta == pytest.approx(0.75)
    assert r.amount_variance == pytest.approx(4635.0)   # 6180 * 0.75
    assert "price_over" in _rules(r)
    assert r.severity == Severity.CRITICAL


def test_not_in_contract(rows):
    # Unmatched billed line keyed under code=None
    r = rows[None]
    assert r.contract_price is None
    assert r.billed_amount == pytest.approx(26000.0)
    assert r.amount_variance == pytest.approx(26000.0)
    assert "no_contract" in _rules(r)
    assert r.severity == Severity.CRITICAL


def test_overrun_warning_51(rows):
    r = rows["5.1"]
    assert r.amount_variance == pytest.approx(0.0)
    assert "over_run" in _rules(r)
    assert "qty_over" not in _rules(r)         # 58==58, no qty issue
    assert r.severity == Severity.WARNING


def test_under_billed_info_91(rows):
    r = rows["9.1"]
    assert r.qty_delta == pytest.approx(-22)
    assert r.amount_variance == pytest.approx(-3190.0)   # -22 * 145
    assert "under_billed" in _rules(r)
    assert r.severity == Severity.INFO


@pytest.mark.parametrize("code", ["4.2", "8.1"])
def test_clean_rows(rows, code):
    r = rows[code]
    assert r.amount_variance == pytest.approx(0.0)
    assert r.severity == Severity.OK
    assert r.flags == []


def test_overrun_warning_61(rows):
    # 6.1: built/billed 9,720 vs bid estimate 9,600 → genuine over-run (+120).
    # The mockup rendered this row as "OK" for visual simplicity, but the SDD
    # §7.3 over-run rule (WARNING when qty > est) is authoritative and flags it.
    r = rows["6.1"]
    assert r.amount_variance == pytest.approx(0.0)     # qty & price reconcile
    assert "over_run" in _rules(r)
    assert r.severity == Severity.WARNING


def test_cycle_headline_numbers(golden_asbuilt, golden_invoices, golden_contract):
    result = reconcile(golden_asbuilt, golden_invoices, golden_contract)
    totals = cycle_totals(result, retainage_pct=10.0)
    # Flagged over-billing = 4773 + 4635 + 26000 (mockup KPI = $35,408).
    # This is the headline dollar exposure and must match exactly.
    assert totals.flagged_over_billing == pytest.approx(35408.0)
    assert totals.n_critical == 3
    # Two over-runs (5.1 and 6.1) per authoritative flag rules; mockup showed one.
    assert totals.n_warning == 2


def test_severity_ordering(golden_asbuilt, golden_invoices, golden_contract):
    result = reconcile(golden_asbuilt, golden_invoices, golden_contract)
    # First three rows must be the criticals.
    assert all(r.severity == Severity.CRITICAL for r in result[:3])
