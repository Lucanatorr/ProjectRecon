"""Step 5 — Export: preview tabs and download the workbook / summary.

Tab bar + preview table render as the mockup's .tabs/.prev HTML (tabs via ?etab=);
downloads are Streamlit buttons (they must serve bytes)."""
from __future__ import annotations

import streamlit as st

from recon.models import Severity
from recon.reconcile import cycle_totals
from recon.report import build_pdf_summary, build_workbook
from ui.state import WizardState, get_sid
from ui.theme import _esc, card_close, card_open, lede, table_html, td, trend_html

_LEDE = ("One workbook for the payment packet: a summary, the flagged items to hold, "
         "the full reconciled detail, and anything that couldn't be matched. Every "
         "number traces back to a source line.")

_TABS = [("sum", "Summary"), ("flag", "Flagged"), ("full", "Full detail"),
         ("un", "Unmatched")]


def _var_cell(v: float) -> str:
    color = ("var(--critical)" if v > 0.5 else
             ("var(--muted)" if v < -0.5 else "var(--text)"))
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    return f'<td class="r num" style="color:{color}">{sign}${abs(v):,.0f}</td>'


def _chip_cell(r) -> str:
    sev = r.severity
    cls = {Severity.CRITICAL: "critical", Severity.WARNING: "warning",
           Severity.INFO: "info", Severity.OK: "ok"}[sev]
    label = (r.flags[0].rule.replace("_", " ").title() if r.flags else "OK")
    return f'<td><span class="chip chip--{cls}">{_esc(label)}</span></td>'


def _rows_for(tab: str, rows):
    if tab == "flag":
        return [r for r in rows if r.severity in (Severity.CRITICAL, Severity.WARNING)]
    if tab == "un":
        return [r for r in rows if r.code is None or r.contract_price is None]
    return rows


def _preview_table(rows) -> str:
    headers = [("Line", ""), ("Built", "r"), ("Billed", "r"),
               ("Variance", "r"), ("Flag", "")]
    body = []
    for r in rows:
        line = f'{r.code} {r.description}' if r.code else r.description
        body.append([
            td(line),
            td(f"{r.built_qty:,.0f}" if r.built_qty else "—", "r num"),
            td(f"{r.billed_qty:,.0f}" if r.billed_qty else "—", "r num"),
            _var_cell(r.amount_variance),
            _chip_cell(r),
        ])
    return table_html(headers, body)


def render(state: WizardState) -> None:
    if not state.contract or (not state.asbuilt and not state.invoices):
        st.info("Complete the earlier steps first.")
        return

    from ui.step_reconcile import ensure_results
    ensure_results(state)
    rows = state.results
    totals = cycle_totals(rows, retainage_pct=state.retainage_pct)
    sid = get_sid()

    st.markdown(lede(_LEDE), unsafe_allow_html=True)
    from ui.progress import show_flash
    show_flash(state)

    tab = st.query_params.get("etab", "sum")
    if tab not in {k for k, _ in _TABS}:
        tab = "sum"

    tabbar = '<div class="tabs">' + "".join(
        f'<a class="tab{" on" if tab == k else ""}" '
        f'href="?step=export&sid={sid}&etab={k}" target="_self">{label}</a>'
        for k, label in _TABS) + "</div>"

    from recon.reconcile import check_retainage
    chk = check_retainage(totals.total_billed, state.retainage_pct,
                          state.actual_retainage)

    if tab == "sum":
        preview = _summary_table(totals, chk)
    else:
        preview = _preview_table(_rows_for(tab, rows))

    st.markdown(f'<div class="card card--flush">{tabbar}'
                f'<div class="prev">{preview}</div></div>', unsafe_allow_html=True)

    if chk.has_actual and not chk.ok:
        st.warning(chk.message)

    # recommendation + downloads
    st.markdown(
        f'<div class="card__h" style="margin-top:4px"><div class="card__note">'
        f'Recommended payment this cycle: <b style="color:var(--text)" class="num">'
        f'${totals.net_recommended:,.0f}</b> — holds ${totals.flagged_over_billing:,.0f}'
        f' in flags, withholds ${totals.retainage_held:,.0f} retainage.</div></div>',
        unsafe_allow_html=True)

    # --- pre-export validation gates (SDD §9) ---
    blockers = _gates_panel(state, sid)
    override = state.override if blockers else None
    locked = bool(blockers) and not state.override

    label = f"{state.project_name} · Cycle {state.cycle_no} · {state.period_label}".strip(" ·")

    def _log_export(kind: str):
        from ui.db import log_action
        return lambda: log_action(
            "export", "billing_cycle", actor=state.reviewer or None,
            detail={"artifact": kind, "project": state.project_name,
                    "cycle_no": int(state.cycle_no),
                    "overridden": bool(override)})

    d1, d2, _ = st.columns([1, 1, 3])
    with d1:
        st.download_button(
            "Download Excel workbook",
            data=build_workbook(rows, totals, label, override=override),
            file_name="reconciliation_report.xlsx", type="primary",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True, disabled=locked,
            on_click=_log_export("workbook"))
    with d2:
        st.download_button(
            "Download PDF summary",
            data=build_pdf_summary(rows, totals, label,
                                   resolutions=state.resolutions or None,
                                   override=override),
            file_name="reconciliation_summary.pdf", mime="application/pdf",
            use_container_width=True, disabled=locked,
            on_click=_log_export("pdf"))
    if locked:
        st.caption("Downloads unlock once the checks above pass — or with a "
                   "recorded override.")

    _cycle_history(state, rows, totals)
    _audit_panel()


def _gates_panel(state: WizardState, sid: str) -> list:
    """Render the pre-export checklist and the override flow. Returns the blockers."""
    from datetime import datetime

    from ui.db import log_action
    from ui.gates import blocking, evaluate_gates
    from ui.theme import gates_html

    gates = evaluate_gates(state)
    blockers = blocking(gates)

    note = ("Everything checks out — this cycle is ready for sign-off."
            if not blockers else
            f"{len(blockers)} check(s) block a clean export.")
    st.markdown(card_open("Pre-export checks", note) + gates_html(gates, sid)
                + card_close(), unsafe_allow_html=True)

    if not blockers:
        if state.override:                       # gates now pass — override is moot
            state.override = None
        return []

    if state.override:
        st.warning(
            f"Exporting with override — {state.override['reason']} "
            f"({state.override.get('by') or 'reviewer'} {state.override.get('at','')}). "
            "The artifacts are stamped accordingly.")
        if st.button("Remove override", key="rm_override"):
            state.override = None
            st.rerun()
        return blockers

    with st.expander("Export anyway (override the checks)"):
        st.caption("An override is recorded in the audit log and stamped on the "
                   "exported workbook and PDF.")
        reason = st.text_input("Reason for override", key="ovr_reason",
                              placeholder="e.g. paying uncontested items now; "
                                          "traffic control pending change order")
        if st.button("Confirm override", type="primary", key="do_override",
                     disabled=not reason.strip()):
            state.override = {
                "reason": reason.strip(),
                "by": state.reviewer or "",
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            log_action("export_override", "billing_cycle", actor=state.reviewer or None,
                       detail={"project": state.project_name,
                               "cycle_no": int(state.cycle_no),
                               "reason": state.override["reason"],
                               "failed_checks": [g.key for g in blockers]})
            state.flash = "Override recorded in the audit log — downloads unlocked."
            st.rerun()
    return blockers


def _cycle_history(state: WizardState, rows, totals) -> None:
    from ui.db import cycle_history, save_cycle
    from ui.progress import loading_bar

    st.markdown('<div class="card__t" style="margin-top:18px">Cycle history</div>'
                '<div class="card__note" style="margin-bottom:8px">Save this cycle to '
                'the project so it can be trended and compared against next month.</div>',
                unsafe_allow_html=True)

    proj = state.project_name or "New project"
    if st.button(f"Save cycle {int(state.cycle_no)} to “{proj}”", type="primary",
                 key="save_cycle"):
        with loading_bar("Saving cycle…") as step:
            step(50, "Persisting results…")
            save_cycle(
                project_name=proj, contractor=state.contractor or None,
                area=state.area or None, cycle_no=int(state.cycle_no),
                period_label=state.period_label or None,
                billing_mode=state.billing_mode, retainage_pct=state.retainage_pct,
                prior_billed=state.prior_billed, contract_items=state.contract,
                rows=rows, resolutions=state.resolutions or None,
                actor=state.reviewer or None)
            step(100, "Done")
        state.flash = f"Saved cycle {int(state.cycle_no)} to “{proj}”."
        st.rerun()

    history = cycle_history(proj)
    if not history:
        st.caption("No saved cycles yet for this project — save one to start the "
                   "built-vs-billed trend.")
        return

    headers = [("Cycle", ""), ("Period", ""), ("Mode", ""), ("Billed", "r"),
               ("Expected", "r"), ("Flagged", "r"), ("Net", "r"), ("Saved", "")]
    body = []
    for s in history:
        body.append([
            td(f'{s["cycle_no"]:02d}', "code"),
            td(s["period_label"] or "—"),
            td(s["billing_mode"]),
            td(f'${s["billed"]:,.0f}', "r num"),
            td(f'${s["expected"]:,.0f}', "r num"),
            td(f'${s["flagged"]:,.0f}', "r num"),
            td(f'${s["net"]:,.0f}', "r num"),
            td((s["created_at"] or "")[:16]),
        ])
    st.markdown(card_open(f"Saved cycles · {proj}") + table_html(headers, body)
                + card_close(), unsafe_allow_html=True)

    # built-to-date vs billed-to-date across the job (FR-16)
    st.markdown(
        card_open("Built vs billed by cycle",
                  "cumulative to date — widening bars mean billing outpacing "
                  "documented work")
        + trend_html(history) + card_close(), unsafe_allow_html=True)


def _audit_panel() -> None:
    """Who did what, when — ingest, mapping, resolution, export (FR-17)."""
    import json

    from ui.db import audit_entries

    entries = audit_entries(60)
    with st.expander(f"Audit trail · {len(entries)} recent action(s)"):
        if not entries:
            st.caption("Nothing recorded yet.")
            return
        st.caption("Every ingest, mapping, resolution, override, and export is "
                   "recorded here with who and when.")
        headers = [("When", ""), ("Actor", ""), ("Action", ""), ("Entity", ""),
                   ("Detail", "")]
        body = []
        for e in entries:
            try:
                detail = json.loads(e["detail_json"]) if e["detail_json"] else {}
            except (TypeError, ValueError):
                detail = {}
            summary = ", ".join(f"{k}={v}" for k, v in detail.items())
            body.append([
                td((e["ts"] or "")[:16]),
                td(e["actor"] or "—"),
                td(e["action"], "code"),
                td(e["entity"] or "—"),
                td(summary[:120]),
            ])
        st.markdown(table_html(headers, body), unsafe_allow_html=True)


def _summary_table(totals, chk=None) -> str:
    headers = [("Metric", ""), ("Value", "r")]
    metrics = [
        ("Billed this cycle (gross)", f"${totals.total_billed:,.2f}"),
        ("Expected (built × contract)", f"${totals.total_expected:,.2f}"),
        ("Flagged over-billing", f"${totals.flagged_over_billing:,.2f}"),
        (f"Retainage — contract ({chk.contract_pct:g}%)" if chk else "Retainage held",
         f"${totals.retainage_held:,.2f}"),
    ]
    if chk is not None and chk.has_actual:
        metrics.append(("Retainage — withheld on invoice", f"${chk.actual:,.2f}"))
        metrics.append(("Retainage — variance", f"${chk.variance:,.2f}"))
    metrics += [
        ("Net recommended", f"${totals.net_recommended:,.2f}"),
        ("Critical flags", str(totals.n_critical)),
        ("Warnings", str(totals.n_warning)),
    ]
    body = [[td(k), td(v, "r num")] for k, v in metrics]
    return table_html(headers, body)
