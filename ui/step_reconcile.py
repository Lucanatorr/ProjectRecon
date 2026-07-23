"""Step 4 — Reconciliation: headline metrics, filter chips, built-vs-billed rows.

Rendered almost entirely as custom HTML (KPI tiles, chips, <details> rows) to
match the mockup. Filtering is via the ?flt= query param."""
from __future__ import annotations

import streamlit as st

from recon.models import Severity
from recon.reconcile import cycle_totals, reconcile
from ui.state import WizardState, inputs_fingerprint
from ui.step_crosswalk import apply_codes
from ui.theme import filter_bar_html, kpi_row_html, recon_list_html


def run_reconciliation(state: WizardState) -> None:
    apply_codes(state)
    from config import RECON
    cfg = RECON.__class__(
        tolerance=RECON.tolerance, matching=RECON.matching,
        retainage_default_pct=state.retainage_pct,
        cumulative=state.billing_mode == "cumulative",
    )
    state.results = reconcile(state.asbuilt, state.invoices, state.contract, cfg,
                             prior_billed=state.prior_billed_by_code or None)
    state.results_fp = inputs_fingerprint(state)
    state.done.add("reconcile")


def ensure_results(state: WizardState) -> None:
    """Recompute reconciliation only when inputs changed since the last run.
    Keeps Reconciliation and Export screens consistent regardless of nav order."""
    # load the prior cycle's per-unit billed-to-date (cumulative current-vs-prior)
    from ui.db import prior_billed as _load_prior
    state.prior_billed_by_code = (
        _load_prior(state.project_name, int(state.cycle_no))
        if state.billing_mode == "cumulative" else {})
    if not state.results or state.results_fp != inputs_fingerprint(state):
        run_reconciliation(state)


def render(state: WizardState) -> None:
    if not state.contract or (not state.asbuilt and not state.invoices):
        st.info("Complete the Contract, As-built, and Invoices steps first.")
        return

    ensure_results(state)
    rows = state.results
    totals = cycle_totals(rows, retainage_pct=state.retainage_pct)

    # KPI tiles
    tiles = [
        {"label": "Billed this cycle", "value": f"${totals.total_billed:,.0f}",
         "sub": "gross, before retainage"},
        {"label": "Expected (built × contract)", "value": f"${totals.total_expected:,.0f}",
         "sub": "documented work"},
        {"label": "Flagged over-billing", "value": f"${totals.flagged_over_billing:,.0f}",
         "sub": f"{totals.n_critical} critical items", "flag": True},
        {"label": f"Retainage held ({state.retainage_pct:.0f}%)",
         "value": f"${totals.retainage_held:,.0f}", "sub": "withheld this cycle"},
        {"label": "Net recommended", "value": f"${totals.net_recommended:,.0f}",
         "sub": "after flags & retainage"},
    ]
    st.markdown(kpi_row_html(tiles), unsafe_allow_html=True)

    # retainage verification (only when the coordinator supplied the withheld figure)
    from recon.reconcile import check_retainage
    chk = check_retainage(totals.total_billed, state.retainage_pct,
                          state.actual_retainage)
    if chk.has_actual:
        (st.success if chk.ok else st.warning)(
            ("✓ " if chk.ok else "") + chk.message)

    # current-vs-prior comparison status
    if state.prior_billed_by_code:
        n_dec = sum(1 for r in rows
                    if any(f.rule == "cumulative_decrease" for f in r.flags))
        note = (f"Cumulative mode — comparing billed-to-date against the prior saved "
                f"cycle ({len(state.prior_billed_by_code)} units).")
        if n_dec:
            st.warning(f"{note} {n_dec} unit(s) billed less than last cycle.")
        else:
            st.caption(note)

    # filter chips (?flt=)
    counts = {
        "all": len(rows),
        "critical": sum(r.severity == Severity.CRITICAL for r in rows),
        "warning": sum(r.severity == Severity.WARNING for r in rows),
        "ok": sum(r.severity in (Severity.OK, Severity.INFO) for r in rows),
    }
    flt = st.query_params.get("flt", "all")
    if flt not in counts:
        flt = "all"
    from ui.state import get_sid
    st.markdown(filter_bar_html(flt, counts, get_sid()), unsafe_allow_html=True)

    def keep(r):
        if flt == "all":
            return True
        if flt == "ok":
            return r.severity in (Severity.OK, Severity.INFO)
        return r.severity.value == flt

    shown = [r for r in rows if keep(r)]
    st.markdown(recon_list_html(shown), unsafe_allow_html=True)
    st.markdown('<div class="hint">Tap any row to see the as-built and invoice '
                'lines feeding it. Critical flags hold payment; warnings need a '
                'quick confirm; OK rows reconcile cleanly.</div>',
                unsafe_allow_html=True)
