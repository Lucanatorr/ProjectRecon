"""Sprint 3.1 — change orders (FR-2): CO schedules extend/revise the contract and
CO-authorized units clear the unauthorized/over-price flags."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from recon.contract import apply_change_orders, index_by_code, load_bid_schedule
from recon.models import AsBuiltLine, ContractItem, InvoiceLine, Severity, UoM
from recon.reconcile import cycle_totals, reconcile

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture(scope="module", autouse=True)
def ensure_samples():
    if not (SAMPLES / "ChangeOrder_01.xlsx").exists():
        import subprocess
        import sys
        subprocess.run([sys.executable, str(SAMPLES / "generate_samples.py")], check=True)


def _co_file(tmp_path: Path, rows: list[tuple]) -> Path:
    p = tmp_path / "co.xlsx"
    pd.DataFrame(rows, columns=["Code", "Description", "UoM", "Unit Price", "Est Qty"]
                 ).to_excel(p, index=False)
    return p


def test_co_revises_existing_price():
    items = load_bid_schedule(SAMPLES / "Fiber_Build_2025_BidSchedule.xlsx")
    items = apply_change_orders(items, SAMPLES / "ChangeOrder_01.xlsx")
    ci = index_by_code(items)["4.1"]
    assert ci.unit_price == pytest.approx(10.25)
    assert ci.is_change_order is True
    assert len(items) == 11                     # revision, not an addition


def test_co_adds_new_code(tmp_path):
    base = [ContractItem("A", "Unit A", UoM.EA, 5.0, 10)]
    co = _co_file(tmp_path, [("B", "Unit B", "EA", 12.0, 3)])
    extended = apply_change_orders(base, co)
    idx = index_by_code(extended)
    assert set(idx) == {"A", "B"}
    assert idx["B"].is_change_order is True
    assert idx["A"].is_change_order is False    # untouched base item


def test_reconcile_marks_change_order_rows():
    contract = [ContractItem("X", "Unit X", UoM.EA, 100.0, 5, is_change_order=True)]
    ab = [AsBuiltLine("X", 5, UoM.EA, code="X")]
    iv = [InvoiceLine("INV1", "X", 5, 100.0, 500.0, code="X")]
    row = reconcile(ab, iv, contract)[0]
    assert row.is_change_order is True
    assert row.severity == Severity.OK


def test_co_clears_no_contract_flag():
    # billed unit with no base contract item -> unauthorized; adding it via CO clears it
    ab = [AsBuiltLine("X", 5, UoM.EA, code="X")]
    iv = [InvoiceLine("INV1", "X", 5, 100.0, 500.0, code="X")]
    unauth = reconcile(ab, iv, [])[0]
    assert "no_contract" in {f.rule for f in unauth.flags}

    contract = [ContractItem("X", "Unit X", UoM.EA, 100.0, 5, is_change_order=True)]
    authed = reconcile(ab, iv, contract)[0]
    assert "no_contract" not in {f.rule for f in authed.flags}
    assert authed.is_change_order is True


def test_co_price_revision_clears_price_over():
    # billed above the base price flags price_over; a CO raising the price clears it
    ab = [AsBuiltLine("4.1", 6180, UoM.FT, code="4.1")]
    iv = [InvoiceLine("INV1", "4.1", 6180, 10.25, 63345.0, code="4.1")]
    base = [ContractItem("4.1", "Directional bore", UoM.FT, 9.50, 6000)]
    assert "price_over" in {f.rule for f in reconcile(ab, iv, base)[0].flags}

    revised = [ContractItem("4.1", "Directional bore", UoM.FT, 10.25, 6000,
                           is_change_order=True)]
    row = reconcile(ab, iv, revised)[0]
    assert "price_over" not in {f.rule for f in row.flags}
    assert row.amount_variance == pytest.approx(0.0)
    assert row.is_change_order is True


def test_full_demo_with_change_order(golden_asbuilt, golden_invoices, golden_contract):
    # loading the CO authorizes the directional-bore rate, clearing its $4,635 critical
    contract = apply_change_orders(golden_contract, SAMPLES / "ChangeOrder_01.xlsx")
    rows = reconcile(golden_asbuilt, golden_invoices, contract)
    by_code = {r.code: r for r in rows}
    assert by_code["4.1"].is_change_order is True
    assert "price_over" not in {f.rule for f in by_code["4.1"].flags}

    totals = cycle_totals(rows, retainage_pct=10.0)
    # $35,408 headline minus the $4,635 price-over that the CO authorizes
    assert totals.flagged_over_billing == pytest.approx(30773.0)
    assert totals.n_critical == 2
