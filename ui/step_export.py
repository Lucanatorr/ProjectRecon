"""Step 5 — Export: preview tabs and download the workbook / summary.

Tab bar + preview table render as the mockup's .tabs/.prev HTML (tabs via ?etab=);
downloads are Streamlit buttons (they must serve bytes)."""
from __future__ import annotations

import streamlit as st

from recon.models import Severity
from recon.reconcile import cycle_totals
from recon.report import build_pdf_summary, build_workbook
from ui.state import WizardState, get_sid
from ui.theme import _esc, card_open, lede, table_html, td

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

    tab = st.query_params.get("etab", "sum")
    if tab not in {k for k, _ in _TABS}:
        tab = "sum"

    tabbar = '<div class="tabs">' + "".join(
        f'<a class="tab{" on" if tab == k else ""}" '
        f'href="?step=export&sid={sid}&etab={k}" target="_self">{label}</a>'
        for k, label in _TABS) + "</div>"

    if tab == "sum":
        preview = _summary_table(totals)
    else:
        preview = _preview_table(_rows_for(tab, rows))

    st.markdown(f'<div class="card card--flush">{tabbar}'
                f'<div class="prev">{preview}</div></div>', unsafe_allow_html=True)

    # recommendation + downloads
    st.markdown(
        f'<div class="card__h" style="margin-top:4px"><div class="card__note">'
        f'Recommended payment this cycle: <b style="color:var(--text)" class="num">'
        f'${totals.net_recommended:,.0f}</b> — holds ${totals.flagged_over_billing:,.0f}'
        f' in flags, withholds ${totals.retainage_held:,.0f} retainage.</div></div>',
        unsafe_allow_html=True)

    label = f"{state.project_name} · Cycle {state.cycle_no} · {state.period_label}".strip(" ·")
    d1, d2, _ = st.columns([1, 1, 3])
    with d1:
        st.download_button(
            "Download Excel workbook", data=build_workbook(rows, totals, label),
            file_name="reconciliation_report.xlsx", type="primary",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True)
    with d2:
        st.download_button(
            "Download summary", data=build_pdf_summary(rows, totals, label),
            file_name="reconciliation_summary.txt", mime="text/plain",
            use_container_width=True)


def _summary_table(totals) -> str:
    headers = [("Metric", ""), ("Value", "r")]
    metrics = [
        ("Billed this cycle (gross)", f"${totals.total_billed:,.2f}"),
        ("Expected (built × contract)", f"${totals.total_expected:,.2f}"),
        ("Flagged over-billing", f"${totals.flagged_over_billing:,.2f}"),
        ("Retainage held", f"${totals.retainage_held:,.2f}"),
        ("Net recommended", f"${totals.net_recommended:,.2f}"),
        ("Critical flags", str(totals.n_critical)),
        ("Warnings", str(totals.n_warning)),
    ]
    body = [[td(k), td(v, "r num")] for k, v in metrics]
    return table_html(headers, body)
