"""End-to-end: parse the sample files, crosswalk descriptions, reconcile, and
assert the same golden result — validating ingest + crosswalk + reconcile together
(SDD §11 integration level)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from recon.contract import load_bid_schedule
from recon.crosswalk import AliasStore, resolve
from recon.ingest.invoices import parse_invoices
from recon.ingest.tally import parse_tally
from recon.models import Severity
from recon.reconcile import cycle_totals, reconcile

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture(scope="module", autouse=True)
def ensure_samples():
    if not (SAMPLES / "Fiber_Build_2025_BidSchedule.xlsx").exists():
        subprocess.run([sys.executable, str(SAMPLES / "generate_samples.py")], check=True)


def _apply_crosswalk(lines, contract, store):
    """Resolve each line's raw_desc to a code (mutates in place)."""
    for ln in lines:
        m = resolve(ln.raw_desc, contract, store)
        ln.code = m.code
    return lines


def test_full_pipeline_reproduces_golden():
    contract = load_bid_schedule(SAMPLES / "Fiber_Build_2025_BidSchedule.xlsx")
    asbuilt = parse_tally(SAMPLES / "AsBuilt_PhaseB_Tally.xlsx")
    invoices = parse_invoices([SAMPLES / "Invoice_2025-06_PhaseB.xlsx"])

    store = AliasStore()
    # Simulate the coordinator's crosswalk-step confirmations: "Directional
    # Drilling 2 inch" scores below auto threshold (75) and is confirmed to 4.1,
    # exactly as the mockup requires. "Traffic Control" is left unmapped
    # (marked not-in-contract), so it correctly raises the no_contract flag.
    store.confirm("Directional Drilling 2 inch", "4.1")
    _apply_crosswalk(asbuilt, contract, store)
    _apply_crosswalk(invoices, contract, store)

    rows = reconcile(asbuilt, invoices, contract)
    by_code = {r.code: r for r in rows}

    # Aerial segments WF-02 + WF-03 summed to 41,320 built
    assert by_code["3.1"].built_qty == pytest.approx(41320)
    assert by_code["3.1"].amount_variance == pytest.approx(4773.0)
    assert by_code["3.1"].severity == Severity.CRITICAL

    # Directional bore: price over contract
    assert by_code["4.1"].amount_variance == pytest.approx(4635.0)
    assert "price_over" in {f.rule for f in by_code["4.1"].flags}

    # Traffic control: never crosswalked → no contract → critical
    assert None in by_code
    assert by_code[None].amount_variance == pytest.approx(26000.0)

    totals = cycle_totals(rows, retainage_pct=10.0)
    assert totals.flagged_over_billing == pytest.approx(35408.0)
    assert totals.n_critical == 3


def test_tally_groups_segments_and_drops_subtotal():
    asbuilt = parse_tally(SAMPLES / "AsBuilt_PhaseB_Tally.xlsx")
    descs = [a.raw_desc for a in asbuilt]
    # The two aerial segment rows collapse into one grouped line.
    aerial = [a for a in asbuilt if "adss" in a.raw_desc.lower() or "aerial" in a.raw_desc.lower()]
    assert len(aerial) == 1
    assert aerial[0].qty == pytest.approx(41320)
    # Subtotal noise row is gone.
    assert not any(d.lower() == "subtotal" for d in descs)


def test_invoice_dedupe_on_reupload():
    path = SAMPLES / "Invoice_2025-06_PhaseB.xlsx"
    once = parse_invoices([path])
    twice = parse_invoices([path, path])   # same file twice
    assert len(once) == len(twice)          # re-upload must not double-count
