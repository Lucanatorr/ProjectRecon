"""Field-takeoff pages: Drawings and Invoices."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from takeoff.drawings import is_footage_box, is_materials_box, takeoffs_from_sources
from takeoff.export import drawing_workbook, invoice_workbook
from takeoff.invoices import invoices_from_sources
from takeoff.sources import expand_all
from takeoff_ui.state import TakeoffState
from ui.progress import is_new_upload, loading_bar, show_flash, upload_signature
from ui.theme import _esc, badge, card_close, card_open, kpi_row_html, lede, table_html, td


def _n(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _flag_table(rows: list[tuple[str, str, str]]) -> str:
    return table_html([("Where", ""), ("Written", ""), ("Why it needs a look", "")],
                      [[td(w), td(x, "code"), td(y)] for w, x, y in rows])


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "takeoff"


# --------------------------------------------------------------------------- #
#  Drawings
# --------------------------------------------------------------------------- #
_DRAWINGS_LEDE = ("Upload marked-up construction PDFs. Every comment box headed MATERIALS "
                  "is totalled by material, and every sequential calculation "
                  "(e.g. 48CT 12400-11950=450') is totalled by fiber count. Each zip is "
                  "one drawing set; loose PDFs form one set. A resubmitted drawing whose "
                  "comments repeat another file's is counted once.")


def render_drawings(state: TakeoffState) -> None:
    st.markdown(lede(_DRAWINGS_LEDE), unsafe_allow_html=True)
    show_flash(state)
    c1, c2 = st.columns([3, 1])
    with c1:
        ups = st.file_uploader("Drawing PDFs or zips of PDFs", type=["pdf", "zip"],
                               accept_multiple_files=True, key="tk_draw_up")
    with c2:
        loose = st.text_input("Name for loose PDFs", "Drawings", key="tk_loose_name")
        if state.drawings and st.button("Clear drawings", use_container_width=True):
            state.drawings, state.drawing_uploads = [], []
            st.rerun()
    if ups and is_new_upload("tk_draw_sig", upload_signature(ups)):
        try:
            with loading_bar("Reading comment boxes…") as step:
                step(20, "Opening PDFs…")
                sources = expand_all([(u.name, u.getvalue()) for u in ups])
                if not sources:
                    st.error("No PDFs found in the upload.")
                    return
                step(50, f"Reading comments in {len(sources)} PDF(s)…")
                state.drawings = takeoffs_from_sources(sources, loose_name=loose or "Drawings")
                state.drawing_uploads = [u.name for u in ups]
                step(100, "Done")
            n_files = sum(len(t.files) for t in state.drawings)
            state.flash = (f"Read {n_files} PDF(s) into {len(state.drawings)} drawing set(s).")
            st.rerun()
        except Exception as e:           # a malformed upload must not kill the page
            st.error(f"Could not read the drawings: {e}")
            return

    if not state.drawings:
        st.info("No drawings loaded yet.")
        return
    tabs = st.tabs([t.name for t in state.drawings]) if len(state.drawings) > 1 else [st.container()]
    for tab, t in zip(tabs, state.drawings):
        with tab:
            _drawing_set(t)


def _drawing_set(t) -> None:
    counted = t.counted_files
    if not any(f.comments for f in t.files):
        st.info(f"No comment boxes were found in {len(t.files)} file(s) of “{t.name}”. Only "
                "PDFs marked up with Adobe comments (FreeText boxes) can be read here; "
                "invoices go on the Invoices page.")
        return
    dupes = len(t.files) - len(counted)
    boxes = sum(1 for f in counted for c in f.comments if is_materials_box(c.text))
    fboxes = sum(1 for f in counted for c in f.comments if is_footage_box(c.text))
    fiber = t.fiber_totals()
    flagged = t.flagged()
    st.markdown(kpi_row_html([
        {"label": "Drawing files", "value": f"{len(counted)}",
         "sub": f"{dupes} duplicate not counted" if dupes else "all counted"},
        {"label": "Materials boxes", "value": f"{boxes}", "sub": f"{len(t.materials)} lines"},
        {"label": "Footage boxes", "value": f"{fboxes}", "sub": f"{len(t.footage)} calculations"},
        {"label": "Fiber with a count", "value": f"{sum(a['feet'] for a in fiber):,} ft",
         "sub": " · ".join(f"{a['fiber_count']}ct {a['feet']:,}" for a in fiber) or "none"},
        {"label": "Needs a look", "value": f"{len(flagged)}", "sub": "see below",
         "flag": bool(flagged)},
    ]), unsafe_allow_html=True)

    st.download_button("Download workbook (.xlsx)", drawing_workbook(t),
                       file_name=f"{_slug(t.name)} material and fiber totals.xlsx",
                       type="primary", key=f"dl_draw_{t.name}")

    rows = []
    for a in t.material_totals():
        rows.append([td(a["item"]) if a["known"] else
                     f'<td>{_esc(a["item"])} {badge("unrecognised", "low")}</td>',
                     td(a["unit"]), td(_n(a["qty"]), "r num"), td(a["pages"], "r num"),
                     td(", ".join(a["written"]), "code")])
    st.markdown(card_open("Material totals", "every MATERIALS comment box") +
                table_html([("Material", ""), ("Unit", ""), ("Total", "r"), ("Pages", "r"),
                            ("Written as", "")], rows) + card_close(), unsafe_allow_html=True)

    frows = [[td(f"{a['fiber_count']}-count"), td(f"{a['feet']:,}", "r num"),
              td(a["calcs"], "r num")] for a in fiber]
    lrows = [[td(f"{a['fiber_count']}"), td(a["label"], "code"), td(f"{a['feet']:,}", "r num"),
              td(a["calcs"], "r num")] for a in t.fiber_by_label()]
    html = card_open("Fiber footage by fiber count", "CT, AF and DF grouped by count") + \
        table_html([("Fiber count", ""), ("Footage (ft)", "r"), ("Calculations", "r")], frows)
    if lrows:
        html += '<div style="height:12px"></div>' + table_html(
            [("Count", ""), ("Label as written", ""), ("Footage (ft)", "r"), ("Calcs", "r")], lrows)
    cables = t.cable_totals()
    if cables:
        html += ('<div class="card__note" style="margin:14px 0 6px">Cable IDs with no fiber '
                 'count written — not included above</div>' + table_html(
                     [("Cable ID", ""), ("Footage (ft)", "r"), ("Calcs", "r"), ("Labels", "")],
                     [[td(c["cable"], "code"), td(f"{c['feet']:,}", "r num"), td(c["calcs"], "r num"),
                       td(", ".join(c["labels"]))] for c in cables]))
    st.markdown(html + card_close(), unsafe_allow_html=True)

    if flagged:
        st.markdown(card_open("Needs a look", f"{len(flagged)} item(s) — counted as described")
                    + _flag_table([(f"{f} p{p}", line, why) for f, p, line, why in flagged])
                    + card_close(), unsafe_allow_html=True)
    with st.expander(f"Source files ({len(t.files)})"):
        st.dataframe(pd.DataFrame([{
            "File": f.name, "Pages": f.pages, "Comment boxes": len(f.comments),
            "Counted": "yes" if f.counted else f"no — same comments as {f.duplicate_of}"}
            for f in t.files]), hide_index=True, use_container_width=True)
    with st.expander(f"Material detail ({len(t.materials)} lines)"):
        st.dataframe(pd.DataFrame([{
            "File": m.file, "Page": m.page, "As written": m.line, "Qty": m.qty, "Unit": m.unit,
            "Material": m.item, "Source": m.source, "Needs a look": "; ".join(m.notes),
            "How it was read": "; ".join(m.info)} for m in t.materials]),
            hide_index=True, use_container_width=True)
    with st.expander(f"Fiber detail ({len(t.footage)} calculations)"):
        st.dataframe(pd.DataFrame([{
            "File": f.file, "Page": f.page, "Label": f.label, "Count": f.fiber_count,
            "Cable": f.cable, "Start": f.start, "End": f.end, "Unit": f.seq_unit,
            "Footage (ft)": f.feet, "As written": f.line, "Needs a look": "; ".join(f.notes),
            "How it was read": "; ".join(f.info)} for f in t.footage]),
            hide_index=True, use_container_width=True)


# --------------------------------------------------------------------------- #
#  Invoices
# --------------------------------------------------------------------------- #
_INVOICE_LEDE = ("Upload contractor invoice PDFs (one invoice per page), or zips of them. "
                 "Every line item is read with its quantity, rate and total; each line is "
                 "checked against quantity × rate, each invoice against its printed total, "
                 "and a page that repeats an invoice already loaded is counted once.")


def render_invoices(state: TakeoffState, sid: str) -> None:
    st.markdown(lede(_INVOICE_LEDE), unsafe_allow_html=True)
    show_flash(state)
    c1, c2 = st.columns([3, 1])
    with c1:
        ups = st.file_uploader("Invoice PDFs or zips of PDFs", type=["pdf", "zip"],
                               accept_multiple_files=True, key="tk_inv_up")
    with c2:
        st.write("")
        st.write("")
        if state.invoices and st.button("Clear invoices", use_container_width=True):
            state.invoices, state.invoice_uploads, state.sent_to_recon = None, [], ""
            st.rerun()
    if ups and is_new_upload("tk_inv_sig", upload_signature(ups)):
        try:
            with loading_bar("Reading invoices…") as step:
                step(30, "Opening PDFs…")
                sources = expand_all([(u.name, u.getvalue()) for u in ups])
                if not sources:
                    st.error("No PDFs found in the upload.")
                    return
                step(60, f"Reading {len(sources)} PDF(s)…")
                state.invoices = invoices_from_sources(sources)
                state.invoice_uploads = [u.name for u in ups]
                state.sent_to_recon = ""
                step(100, "Done")
            t = state.invoices.totals()
            state.flash = (f"Read {t['pages']} invoice page(s): {t['unique_invoices']} invoice(s), "
                           f"{len(state.invoices.items(counted_only=True))} line item(s).")
            st.rerun()
        except Exception as e:
            st.error(f"Could not read the invoices: {e}")
            return

    s = state.invoices
    if not s or not s.docs:
        if s is not None:
            st.warning(f"No invoices were found in that upload — none of its "
                       f"{len(s.skipped_pages)} page(s) carried an invoice number with line "
                       "items or a total. Drawings go on the Drawings page.")
        else:
            st.info("No invoices loaded yet.")
        return
    t = s.totals()
    flagged = s.flagged()
    st.markdown(kpi_row_html([
        {"label": "Invoices", "value": f"{t['unique_invoices']}",
         "sub": f"{t['pages']} page(s) read"},
        {"label": "Total billed", "value": f"${t['unique_total']:,.2f}",
         "sub": "each invoice counted once"},
        {"label": "Repeat copies", "value": f"{t['repeat_pages']}",
         "sub": f"${t['repeat_total']:,.2f} not counted"},
        {"label": "Line items", "value": f"{len(s.items(counted_only=True))}",
         "sub": f"{len(s.by_work_item())} kinds of work"},
        {"label": "Needs a look", "value": f"{len(flagged)}", "sub": "see below",
         "flag": bool(flagged)},
    ]), unsafe_allow_html=True)
    if s.skipped_pages:
        by_file: dict[str, list[int]] = {}
        for f, pg in s.skipped_pages:
            by_file.setdefault(f, []).append(pg)
        st.caption("Not invoices, skipped: " + "; ".join(
            f"{f} p{', '.join(map(str, pgs[:12]))}{'…' if len(pgs) > 12 else ''}"
            for f, pgs in by_file.items()))
    st.download_button("Download spreadsheet (.xlsx)", invoice_workbook(s, "Invoices"),
                       file_name="invoices - line items and totals.xlsx", type="primary",
                       key="dl_inv")

    st.markdown(card_open("By kind of work", "unique invoices only") + table_html(
        [("Work item", ""), ("Lines", "r"), ("Total qty", "r"), ("Unit", ""), ("Billed", "r"),
         ("Rates", "")],
        [[td(w["work_item"]), td(w["lines"], "r num"), td(_n(w["qty"]), "r num"),
          td(", ".join(w["units"])), td(f"${w['amount']:,.2f}", "r num"),
          td(", ".join(f"${r:,.2f}" for r in w["rates"]), "code")] for w in s.by_work_item()])
        + card_close(), unsafe_allow_html=True)

    inv_rows = []
    for d in s.docs:
        status = badge("counted", "ok") if d.counted else badge(
            f"repeat of p{d.duplicate_of[1]}", "low")
        diff = "" if d.total is None or abs(d.total - d.line_sum) < 0.005 else \
            f" (lines ${d.line_sum:,.2f})"
        inv_rows.append([td(d.number, "code"), td(d.date.isoformat() if d.date else "—"),
                         td(f"{d.file} p{d.page}"), td(d.project or "—"), td(d.bill_to or "—"),
                         td(len(d.items), "r num"),
                         td((f"${d.total:,.2f}" if d.total is not None else "—") + diff, "r num"),
                         f"<td>{status}</td>"])
    st.markdown(card_open("Invoices", f"{len(s.docs)} page(s)") + table_html(
        [("Invoice", ""), ("Date", ""), ("Page", ""), ("Project", ""), ("Billed to", ""),
         ("Lines", "r"), ("Total", "r"), ("", "")], inv_rows) + card_close(),
        unsafe_allow_html=True)

    if flagged:
        st.markdown(card_open("Needs a look", f"{len(flagged)} item(s)") + _flag_table(flagged)
                    + card_close(), unsafe_allow_html=True)

    with st.expander(f"All line items ({len(s.items())})"):
        st.dataframe(pd.DataFrame([{
            "Invoice": i.invoice, "Date": i.date, "File": i.file, "Page": i.page,
            "Project": i.project, "Location": i.section, "Line item": i.desc, "Qty": i.qty,
            "Unit": i.unit, "Rate": i.rate, "Billed": i.amount, "Qty x Rate": i.qty_x_rate,
            "Difference": i.difference, "Work item": i.work_item,
            "Counted": "yes" if d.counted else "no (repeat)", "Needs a look": "; ".join(i.notes)}
            for d in s.docs for i in d.items]), hide_index=True, use_container_width=True)

    _send_to_recon(state, s, sid)


def _send_to_recon(state: TakeoffState, s, sid: str) -> None:
    from takeoff_ui.handoff import send_invoices_to_recon
    from ui.state import get_state as recon_state

    ws = recon_state()
    st.markdown(card_open("Use in Reconciliation",
                          f"cycle: {_esc(ws.project_name)}") +
                '<p class="lede" style="margin:0">Make these invoice lines (repeat copies '
                "left out) the invoices for this session's reconciliation cycle. Each kind "
                "of work arrives as one description, so the Crosswalk maps it once. The "
                "cycle is set to discrete billing, because each invoice bills its own "
                "period.</p>" + card_close(), unsafe_allow_html=True)
    replace_ok = True
    if ws.invoices:
        replace_ok = st.checkbox(f"Replace the {len(ws.invoices)} invoice line(s) already in "
                                 "that cycle", key="tk_replace")
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("Send to Reconciliation", disabled=not replace_ok, key="tk_send"):
            state.sent_to_recon = send_invoices_to_recon(ws, s)
            st.rerun()
    with c2:
        if state.sent_to_recon:
            st.markdown(f'<div class="lede">{_esc(state.sent_to_recon)}. '
                        f'<a class="btn btn--sm btn--pri" href="?step=invoices&sid={sid}" '
                        f'target="_self">Open Reconciliation › Invoices</a></div>',
                        unsafe_allow_html=True)
