"""Shared fixtures. The golden dataset is the mockup's Robeson CAB — PON 5, Cycle
04 example, which is a hand-reconciled known answer (see reconciliation_mockup.html)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root is importable when pytest is run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recon.models import AsBuiltLine, ContractItem, InvoiceLine, UoM  # noqa: E402


@pytest.fixture
def golden_contract() -> list[ContractItem]:
    """Bid schedule from the mockup Contract step (11 units)."""
    return [
        ContractItem("3.1", "Place 144ct ADSS aerial fiber", UoM.FT, 1.85, 42000),
        ContractItem("3.2", "Place 288ct ADSS aerial fiber", UoM.FT, 2.60, 8500),
        ContractItem("4.1", "Directional bore 2\"", UoM.FT, 9.50, 6000),
        ContractItem("4.2", "Trench / plow fiber", UoM.FT, 4.25, 12000),
        ContractItem("5.1", "Handhole 30x48", UoM.EA, 340.00, 55),
        ContractItem("5.2", "Fiber pedestal", UoM.EA, 185.00, 120),
        ContractItem("6.1", "Fusion splice (per fiber)", UoM.EA, 7.50, 9600),
        ContractItem("6.2", "Splice closure", UoM.EA, 210.00, 48),
        ContractItem("7.1", "OTDR test (per fiber)", UoM.EA, 4.00, 9600),
        ContractItem("8.1", "Pole make-ready", UoM.EA, 95.00, 310),
        ContractItem("9.1", "Drop placement", UoM.EA, 145.00, 400),
    ]


@pytest.fixture
def golden_asbuilt() -> list[AsBuiltLine]:
    """Built quantities from the mockup As-built + Reconciliation steps (codes
    pre-assigned, as if crosswalk already ran)."""
    def a(code, qty, uom):
        return AsBuiltLine(raw_desc=code, qty=qty, uom=uom, code=code)
    return [
        a("3.1", 41320, UoM.FT),
        a("4.1", 6180, UoM.FT),
        a("4.2", 11240, UoM.FT),
        a("5.1", 58, UoM.EA),
        a("6.1", 9720, UoM.EA),
        a("8.1", 298, UoM.EA),
        a("9.1", 372, UoM.EA),
    ]


@pytest.fixture
def golden_invoices() -> list[InvoiceLine]:
    """Billed quantities/prices from the mockup Reconciliation rows (cumulative)."""
    def inv(code, qty, price):
        return InvoiceLine(invoice_id="2025-06", raw_desc=code, qty=qty,
                           unit_price=price, amount=qty * price, code=code)
    return [
        inv("3.1", 43900, 1.85),        # qty over
        inv("4.1", 6180, 10.25),        # price over
        InvoiceLine("2025-06", "Traffic control / flagging", 40, 650.0, 26000.0,
                    code=None),          # not in contract
        inv("5.1", 58, 340.00),         # over-run vs bid, $0 variance
        inv("4.2", 11240, 4.25),        # ok
        inv("6.1", 9720, 7.50),         # ok
        inv("8.1", 298, 95.00),         # ok
        inv("9.1", 350, 145.00),        # built-not-billed (under)
    ]
