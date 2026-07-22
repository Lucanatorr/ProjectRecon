"""Sprint 3.2 — retainage first-class + mismatch check (FR-9, SDD §7.3/§7.5)."""
from __future__ import annotations

import pytest

from recon.reconcile import check_retainage, cycle_totals
from recon.models import AsBuiltLine, ContractItem, InvoiceLine, UoM
from recon.reconcile import reconcile


def test_expected_retainage_only_when_no_actual():
    chk = check_retainage(gross=100_000, contract_pct=10.0)
    assert chk.expected == pytest.approx(10_000)
    assert chk.actual is None
    assert chk.has_actual is False
    assert chk.ok is True
    assert "10%" in chk.message


def test_matching_retainage_ok():
    chk = check_retainage(100_000, 10.0, actual=10_000)
    assert chk.ok is True
    assert chk.variance == pytest.approx(0)
    assert "matches contract" in chk.message


def test_under_withheld_flagged():
    chk = check_retainage(100_000, 10.0, actual=7_500)
    assert chk.ok is False
    assert chk.variance == pytest.approx(-2_500)
    assert "under-withheld" in chk.message


def test_over_withheld_flagged():
    chk = check_retainage(100_000, 10.0, actual=12_000)
    assert chk.ok is False
    assert chk.variance == pytest.approx(2_000)
    assert "over-withheld" in chk.message


def test_within_dollar_tolerance():
    # a $0.50 rounding difference is not a mismatch
    chk = check_retainage(100_000, 10.0, actual=10_000.50)
    assert chk.ok is True


def test_zero_pct_contract():
    chk = check_retainage(50_000, 0.0)
    assert chk.expected == pytest.approx(0)


def test_totals_retainage_and_net_still_consistent():
    # first-class gross → retainage → net stays coherent with the retainage check
    ci = ContractItem("A", "Unit A", UoM.EA, 100.0, 10)
    ab = [AsBuiltLine("A", 10, UoM.EA, code="A")]
    iv = [InvoiceLine("INV1", "A", 10, 100.0, 1000.0, code="A")]
    rows = reconcile(ab, iv, [ci])
    totals = cycle_totals(rows, retainage_pct=10.0)
    assert totals.total_billed == pytest.approx(1000)
    assert totals.retainage_held == pytest.approx(100)          # gross × 10%
    assert totals.net_recommended == pytest.approx(900)         # gross − flags − retainage
    chk = check_retainage(totals.total_billed, 10.0, actual=100.0)
    assert chk.expected == pytest.approx(totals.retainage_held)
    assert chk.ok is True
