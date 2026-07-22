"""Tolerance boundary + flag-rule unit tests (SDD FR-11/12)."""
import pytest

from recon.models import AsBuiltLine, ContractItem, InvoiceLine, Severity, UoM
from recon.reconcile import reconcile


def _one(built, billed, uom, contract_price=10.0, est=None, billed_price=None):
    ci = ContractItem("X", "Test unit", uom, contract_price, est or 0)
    ab = [AsBuiltLine(raw_desc="X", qty=built, uom=uom, code="X")] if built is not None else []
    bp = billed_price if billed_price is not None else contract_price
    iv = [InvoiceLine("INV1", "X", billed, bp, billed * bp, code="X")] if billed is not None else []
    return reconcile(ab, iv, [ci])[0]


def _rules(row):
    return {f.rule for f in row.flags}


def test_ft_tolerance_absolute_floor():
    # built 1000 ft, 2% = 20 ft, but abs floor is 50 ft. 40 over → within tol.
    r = _one(1000, 1040, UoM.FT)
    assert "qty_over" not in _rules(r)
    # 60 over → beyond 50 ft floor → critical
    r = _one(1000, 1060, UoM.FT)
    assert "qty_over" in _rules(r)
    assert r.severity == Severity.CRITICAL


def test_ft_tolerance_percent_band():
    # built 10000 ft, 2% = 200 ft (beats 50 floor). 150 over → within tol.
    r = _one(10000, 10150, UoM.FT)
    assert "qty_over" not in _rules(r)
    # 250 over → beyond 200 → critical
    r = _one(10000, 10250, UoM.FT)
    assert "qty_over" in _rules(r)


def test_ea_matches_exactly():
    r = _one(58, 59, UoM.EA)   # one over on a counted unit → critical
    assert "qty_over" in _rules(r)


def test_price_over_and_under():
    over = _one(100, 100, UoM.FT, contract_price=9.5, billed_price=10.25)
    assert "price_over" in _rules(over) and over.severity == Severity.CRITICAL
    under = _one(100, 100, UoM.FT, contract_price=9.5, billed_price=9.0)
    assert "price_under" in _rules(under) and under.severity == Severity.INFO


def test_no_contract_item_is_critical():
    ab = []
    iv = [InvoiceLine("INV1", "Traffic control", 40, 650, 26000, code=None)]
    r = reconcile(ab, iv, [])[0]
    assert "no_contract" in _rules(r)
    assert r.severity == Severity.CRITICAL
    assert r.amount_variance == pytest.approx(26000)


def test_negative_qty_backout_does_not_falsely_over_bill():
    # a credit/back-out: billed less than built should never read as qty_over
    r = _one(500, -50, UoM.FT)
    assert "qty_over" not in _rules(r)


def test_two_unmatched_lines_stay_separate():
    # Regression: distinct unauthorized units (both code=None) must not collapse
    # into one bucket — the cumulative max-qty reduction would otherwise drop the
    # smaller one entirely.
    iv = [
        InvoiceLine("INV1", "Traffic control / flagging", 40, 650, 26000, code=None),
        InvoiceLine("INV1", "Directional drilling 2in", 6180, 10.25, 63345, code=None),
    ]
    rows = reconcile([], iv, [])
    unmatched = [r for r in rows if r.code is None]
    assert len(unmatched) == 2
    amounts = sorted(r.billed_amount for r in unmatched)
    assert amounts == pytest.approx([26000, 63345])
    assert all("no_contract" in {f.rule for f in r.flags} for r in unmatched)


def test_cumulative_same_unit_uses_latest_to_date():
    # Two cumulative pay-app lines for the SAME code: take the latest (max) to-date.
    iv = [
        InvoiceLine("INV1", "X", 100, 10.0, 1000, code="X", period="2025-05"),
        InvoiceLine("INV2", "X", 250, 10.0, 2500, code="X", period="2025-06"),
    ]
    ci = ContractItem("X", "Test", UoM.EA, 10.0, 300)
    r = reconcile([AsBuiltLine("X", 250, UoM.EA, code="X")], iv, [ci])[0]
    assert r.billed_qty == pytest.approx(250)   # not 350 (sum) — cumulative to-date


def test_tolerance_never_masks_price_critical():
    # qty within tolerance but price over contract must still be CRITICAL
    r = _one(1000, 1010, UoM.FT, contract_price=9.5, billed_price=10.0)
    assert r.severity == Severity.CRITICAL
    assert "price_over" in _rules(r)
