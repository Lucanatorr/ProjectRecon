"""Wizard-flow logic test: exercises the state fingerprint + ensure_results path
(the stale-results bug fix) without a running Streamlit context. The UI render
functions call st.*, but run_reconciliation / ensure_results / apply_codes are
pure and safe to call directly."""
from __future__ import annotations

from pathlib import Path

import pytest

from recon.contract import load_bid_schedule
from recon.crosswalk import resolve
from recon.ingest.invoices import parse_invoices
from recon.ingest.tally import parse_tally
from recon.reconcile import cycle_totals
from ui.state import WizardState, inputs_fingerprint
from ui.step_reconcile import ensure_results, run_reconciliation

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _loaded_state() -> WizardState:
    s = WizardState(retainage_pct=10.0, billing_mode="cumulative")
    s.contract = load_bid_schedule(SAMPLES / "Fiber_Build_2025_BidSchedule.xlsx")
    s.asbuilt = parse_tally(SAMPLES / "AsBuilt_PhaseB_Tally.xlsx")
    s.invoices = parse_invoices([SAMPLES / "Invoice_2025-06_PhaseB.xlsx"])
    # Simulate the crosswalk step: accept auto matches, then human decisions.
    for ln in list(s.asbuilt) + list(s.invoices):
        m = resolve(ln.raw_desc, s.contract, s.aliases)
        if m.code is not None:
            s.resolved[ln.raw_desc] = m.code
    s.resolved["Directional Drilling 2 inch"] = "4.1"     # confirmed
    s.not_in_contract.add("Traffic Control / Flagging (day)")  # not in contract
    return s


def test_flow_reaches_golden_after_crosswalk():
    s = _loaded_state()
    ensure_results(s)
    totals = cycle_totals(s.results, retainage_pct=s.retainage_pct)
    assert totals.flagged_over_billing == pytest.approx(35408.0)
    assert totals.n_critical == 3


def test_results_invalidate_when_crosswalk_changes():
    s = _loaded_state()
    # First run WITHOUT confirming the directional-drilling mapping.
    s.resolved.pop("Directional Drilling 2 inch")
    run_reconciliation(s)
    fp_before = s.results_fp
    before = cycle_totals(s.results, retainage_pct=10.0).flagged_over_billing
    assert before == pytest.approx(35408.0 + 63345.0 - 4635.0) or before > 35408.0

    # Now confirm the mapping — fingerprint must change and ensure_results recompute.
    s.resolved["Directional Drilling 2 inch"] = "4.1"
    assert inputs_fingerprint(s) != fp_before
    ensure_results(s)
    after = cycle_totals(s.results, retainage_pct=10.0).flagged_over_billing
    assert after == pytest.approx(35408.0)


def test_fingerprint_stable_when_nothing_changes():
    s = _loaded_state()
    ensure_results(s)
    fp1 = s.results_fp
    ensure_results(s)                 # no input change → no recompute
    assert s.results_fp == fp1
