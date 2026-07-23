"""Step 4 — Reconciliation: headline metrics, filter chips, built-vs-billed rows.

Rendered almost entirely as custom HTML (KPI tiles, chips, <details> rows) to
match the mockup. Filtering is via the ?flt= query param."""
from __future__ import annotations

from urllib.parse import quote

import streamlit as st

from recon.models import Severity
from recon.reconcile import cycle_totals, reconcile
from ui.state import (
    WizardState,
    clear_resolution,
    inputs_fingerprint,
    row_key,
    set_resolution,
    unresolved_criticals,
)
from ui.step_crosswalk import apply_codes
from ui.theme import filter_bar_html, kpi_row_html, recon_list_html

_FLAGGED = (Severity.CRITICAL, Severity.WARNING)


def _res_href(sid: str, **params) -> str:
    q = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"?step=reconcile&sid={sid}&{q}"


def _process_resolution_actions(state: WizardState) -> None:
    """Apply hold / approve / clear actions arriving as query-param links."""
    qp = st.query_params
    from ui.db import log_action
    if "res" in qp and "st" in qp:
        key, status = qp["res"], qp["st"]
        current = state.resolutions.get(key)
        # re-applying on every rerun would churn the timestamp — only act on change
        if not current or current.get("status") != status:
            set_resolution(state, key, status, by=state.reviewer)
            log_action("resolve_flag", "recon_result", actor=state.reviewer or None,
                       detail={"row": key, "status": status})
    if "resclear" in qp:
        key = qp["resclear"]
        if key in state.resolutions:
            clear_resolution(state, key)
            log_action("clear_resolution", "recon_result",
                       actor=state.reviewer or None, detail={"row": key})


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
    _process_resolution_actions(state)
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

    # reviewer sign-off progress
    flagged = [r for r in rows if r.severity in _FLAGGED]
    if flagged:
        resolved = sum(1 for r in flagged if state.resolutions.get(row_key(r)))
        open_criticals = len(unresolved_criticals(state))
        msg = f"{resolved} of {len(flagged)} flagged item(s) reviewed."
        if open_criticals:
            st.warning(f"{msg} {open_criticals} critical item(s) still need a "
                       "hold or approve decision before sign-off.")
        else:
            st.success(f"✓ {msg} No critical items are awaiting a decision.")

    def keep(r):
        if flt == "all":
            return True
        if flt == "ok":
            return r.severity in (Severity.OK, Severity.INFO)
        return r.severity.value == flt

    shown = [r for r in rows if keep(r)]
    sid = get_sid()

    def _actions(r):
        """Reviewer action links for a flagged row (hold / approve / note / clear)."""
        if r.severity not in _FLAGGED:
            return ""
        key = row_key(r)
        links = [
            f'<a class="btn btn--sm" href="{_res_href(sid, res=key, st="hold")}" '
            f'target="_self">Hold</a>',
            f'<a class="btn btn--sm" href="{_res_href(sid, res=key, st="approve")}" '
            f'target="_self">Approve</a>',
            f'<a class="btn btn--sm" href="{_res_href(sid, resnote=key)}" '
            f'target="_self">Note</a>',
        ]
        if state.resolutions.get(key):
            links.append(
                f'<a class="btn btn--sm" href="{_res_href(sid, resclear=key)}" '
                f'target="_self">Clear</a>')
        return ('<div style="display:flex;gap:8px;flex-wrap:wrap">'
                + "".join(links) + '</div>')

    st.markdown(recon_list_html(shown, state.resolutions, _actions),
                unsafe_allow_html=True)
    st.markdown('<div class="hint">Tap any row to see the as-built and invoice '
                'lines feeding it, and to hold, approve, or annotate it. Critical '
                'flags hold payment; warnings need a quick confirm.</div>',
                unsafe_allow_html=True)

    _note_editor(state, sid)


def _note_editor(state: WizardState, sid: str) -> None:
    """Inline note field for the row opened via the "Note" action."""
    key = st.query_params.get("resnote")
    if not key:
        return
    current = state.resolutions.get(key, {})
    with st.container(border=True):
        st.markdown(f'<div class="card__t">Reviewer note · {key}</div>',
                    unsafe_allow_html=True)
        text = st.text_input("Note", value=current.get("note", ""),
                            key=f"resnote_{key}",
                            placeholder="e.g. awaiting field verification of 2,580 ft")
        c1, c2, _ = st.columns([1, 1, 4])
        with c1:
            if st.button("Save note", type="primary", key=f"savenote_{key}"):
                set_resolution(state, key, current.get("status") or "note",
                              note=text, by=state.reviewer)
                st.query_params.pop("resnote", None)
                st.rerun()
        with c2:
            if st.button("Cancel", key=f"cancelnote_{key}"):
                st.query_params.pop("resnote", None)
                st.rerun()
