"""Step 2 — Invoices: upload files (xlsx/csv/pdf/zip); set billing mode & retainage.

For PDF invoices, a saved per-contractor template profile is applied automatically;
if a contractor's columns don't auto-detect, the column-mapping panel lets the
coordinator map them once and save the template for reuse."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from config import ROOT
from recon.ingest.invoice_pdf import extract_tables, suggest_mapping
from recon.ingest.invoices import parse_invoices
from recon.models import TemplateProfile
from ui.db import load_profile, save_profile
from ui.progress import is_new_upload, loading_bar, show_flash, upload_signature
from ui.state import WizardState
from ui.theme import badge, card_close, card_open, file_row, lede, table_html, td
from ui.uploads import save_bytes, save_upload

SAMPLE = ROOT / "samples" / "Invoice_2025-06_PhaseB.xlsx"
PDF_SAMPLE = ROOT / "samples" / "Invoice_2025-06_PhaseB.pdf"

_LEDE = ("Add this cycle's invoices — files or a zip. Tell the tool whether they "
         "bill cumulatively and how much retainage is held. PDF invoices from a "
         "known contractor parse with their saved column template.")


def _source_files(invoices) -> list[str]:
    seen: list[str] = []
    for ln in invoices:
        name = ln.source_file or ""
        if name and name not in seen:
            seen.append(name)
    return seen


def _ingest(state: WizardState, paths, *, pdf_bytes=None, pdf_name="") -> None:
    """Parse invoices (applying the contractor's saved template to PDFs) and record
    the representative PDF for the mapping panel."""
    profile = load_profile(state.contractor)
    state.invoices = parse_invoices(
        paths, is_cumulative=state.billing_mode == "cumulative", profile=profile)
    state.invoice_files = _source_files(state.invoices)
    state.pending_pdf = pdf_bytes
    state.pending_pdf_name = pdf_name
    touched_pdf = any(Path(str(p)).suffix.lower() in (".pdf", ".zip") for p in paths)
    state.invoice_profile_note = (
        f"Applied saved template for “{state.contractor}”."
        if profile and touched_pdf else "")


def render(state: WizardState) -> None:
    st.markdown(lede(_LEDE), unsafe_allow_html=True)
    show_flash(state)

    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        ups = st.file_uploader("Invoices (xlsx / csv / pdf / zip)",
                              type=["xlsx", "csv", "pdf", "zip"],
                              accept_multiple_files=True, key="inv_up")
    with c2:
        st.write("")
        st.write("")
        if st.button("Load xlsx", use_container_width=True, key="inv_sample") \
                and SAMPLE.exists():
            with loading_bar("Loading sample invoice…") as step:
                step(45, "Parsing invoice…")
                _ingest(state, [SAMPLE])
                step(100, "Done")
            state.flash = f"Loaded {len(state.invoices)} line items."
            st.rerun()
    with c3:
        st.write("")
        st.write("")
        if st.button("Load PDF", use_container_width=True, key="inv_pdf_sample") \
                and PDF_SAMPLE.exists():
            with loading_bar("Loading sample PDF invoice…") as step:
                step(45, "Extracting table…")
                _ingest(state, [PDF_SAMPLE], pdf_bytes=PDF_SAMPLE.read_bytes(),
                        pdf_name=PDF_SAMPLE.name)
                step(100, "Done")
            state.flash = f"Extracted {len(state.invoices)} line items from the PDF."
            st.rerun()
    if ups and is_new_upload("inv_up_sig", upload_signature(ups)):
        try:
            with loading_bar("Loading invoices…") as step:
                step(20, "Reading files…")
                paths = [save_upload(u) for u in ups]
                pdf_up = next((u for u in ups
                               if Path(u.name).suffix.lower() == ".pdf"), None)
                step(55, "Parsing invoices…")
                _ingest(state, paths,
                        pdf_bytes=pdf_up.getvalue() if pdf_up else None,
                        pdf_name=pdf_up.name if pdf_up else "")
                step(100, "Done")
            state.flash = (f"Loaded {len(state.invoices)} line items from "
                           f"{len(state.invoice_files)} file(s).")
            st.rerun()
        except ValueError as e:
            st.error(f"Could not parse invoice: {e}")

    if state.invoice_profile_note:
        st.success(state.invoice_profile_note)

    _files_card(state)
    _billing_settings(state)

    if state.pending_pdf:
        _mapping_panel(state)

    if state.invoices:
        with st.expander(f"Line items · {len(state.invoices)}"):
            df = pd.DataFrame([{
                "Invoice": l.invoice_id, "Description": l.raw_desc, "Qty": l.qty,
                "Unit price": l.unit_price, "Amount": l.amount,
            } for l in state.invoices])
            st.dataframe(df, use_container_width=True, hide_index=True)
        if st.button("Confirm invoices", type="primary"):
            state.done.add("invoices")
            st.success("Invoices confirmed.")


def _files_card(state: WizardState) -> None:
    if not state.invoice_files:
        return
    lines_by_file: dict[str, int] = {}
    for ln in state.invoices:
        lines_by_file[ln.source_file or ""] = lines_by_file.get(ln.source_file or "", 0) + 1
    mode_note = "cumulative to date" if state.billing_mode == "cumulative" else "discrete"
    html = card_open(f"Uploaded · {len(state.invoice_files)} files")
    rows_html = ""
    for name in state.invoice_files:
        ext = Path(name).suffix.upper().lstrip(".") or "FILE"
        n = lines_by_file.get(name, 0)
        rows_html += file_row(ext, name, f"{n} line items · {mode_note}",
                              badge("Parsed", "ok"))
    st.markdown(html + rows_html + card_close(), unsafe_allow_html=True)


def _billing_settings(state: WizardState) -> None:
    with st.container(border=True, key="billing_card"):
        st.markdown('<div class="card__t" style="margin-bottom:12px">Billing settings</div>',
                    unsafe_allow_html=True)
        m1, m2, m3 = st.columns(3)
        with m1:
            mode = st.segmented_control(
                "Billing mode", ["cumulative", "discrete"],
                default=state.billing_mode, key="billing_mode_ctl")
            if mode:
                state.billing_mode = mode
        with m2:
            state.retainage_pct = st.number_input(
                "Contract retainage (%)", min_value=0.0, max_value=100.0,
                value=state.retainage_pct, step=0.5)
        with m3:
            state.prior_billed = st.number_input(
                "Prior billed to date ($)", min_value=0.0, value=state.prior_billed,
                step=1000.0)

        verify = st.checkbox("Verify retainage withheld on the invoice",
                            value=state.actual_retainage is not None,
                            key="verify_retainage")
        if verify:
            state.actual_retainage = st.number_input(
                "Retainage withheld on invoice ($)", min_value=0.0,
                value=float(state.actual_retainage or 0.0), step=100.0,
                help="Enter the retainage the contractor actually withheld this cycle "
                     "so it can be checked against the contract rate.")
        else:
            state.actual_retainage = None

        st.markdown('<div class="hint">Cumulative mode validates this period = to-date '
                    '− prior, and checks retainage against the contract.</div>',
                    unsafe_allow_html=True)


def _preview_html(grid: list[list[str]], ncols: int) -> str:
    headers = [(f"col {i}", "") for i in range(ncols)]
    rows = []
    for row in grid[:6]:
        rows.append([td(row[i] if i < len(row) else "") for i in range(ncols)])
    return (card_open("Extracted table preview") + table_html(headers, rows)
            + card_close())


def _mapping_panel(state: WizardState) -> None:
    """Map a contractor's PDF invoice columns and save the template for reuse."""
    tmp = save_bytes(state.pending_pdf, ".pdf")
    try:
        grids = extract_tables(tmp)
    except ValueError:
        grids = []
    if not grids:
        return
    grid = grids[0]
    ncols = max((len(r) for r in grid), default=0)
    hdr_default, cols_default = suggest_mapping(grid)

    with st.expander("Column mapping · per-contractor template",
                     expanded=not state.invoices):
        st.caption(f"Source: {state.pending_pdf_name}. Map this contractor's columns "
                   "once — the template is saved and reused for their future PDF "
                   "invoices that don't auto-detect.")
        st.markdown(_preview_html(grid, ncols), unsafe_allow_html=True)

        top = st.columns([2, 1])
        contractor = top[0].text_input("Contractor", state.contractor or "",
                                       key="tpl_contractor")
        header_row = int(top[1].number_input(
            "Header row", min_value=0, max_value=max(len(grid) - 1, 0),
            value=int(hdr_default), key="tpl_hrow"))

        hdr_cells = grid[header_row] if header_row < len(grid) else []

        def _label(i: int) -> str:
            t = hdr_cells[i] if i < len(hdr_cells) and hdr_cells[i] else ""
            return f"col {i}" + (f" · {t}" if t else "")

        options = ["—"] + [_label(i) for i in range(ncols)]

        def _field(field: str, col, required: bool = False) -> int | None:
            di = cols_default.get(field)
            idx = (di + 1) if (di is not None and di < ncols) else 0
            label = field + (" *" if required else "")
            choice = col.selectbox(label, options, index=idx, key=f"tpl_{field}")
            return None if choice == "—" else int(choice.split()[1])

        r1 = st.columns(3)
        picks = {
            "desc": _field("desc", r1[0], required=True),
            "qty": _field("qty", r1[1], required=True),
            "price": _field("price", r1[2]),
        }
        r2 = st.columns(3)
        picks["amount"] = _field("amount", r2[0])
        picks["invoice"] = _field("invoice", r2[1])
        picks["period"] = _field("period", r2[2])

        if st.button("Save & apply template", type="primary", key="tpl_save"):
            columns = {k: v for k, v in picks.items() if v is not None}
            profile = TemplateProfile(contractor.strip(), columns, header_row)
            if not contractor.strip():
                st.error("Enter a contractor name to save the template.")
            elif not profile.is_valid():
                st.error("Map at least the description and quantity columns.")
            else:
                save_profile(profile)
                if not state.contractor:
                    state.contractor = contractor.strip()
                state.invoices = parse_invoices(
                    [tmp], is_cumulative=state.billing_mode == "cumulative",
                    profile=profile)
                state.invoice_files = _source_files(state.invoices)
                state.invoice_profile_note = (
                    f"Saved & applied template for “{contractor.strip()}”.")
                st.rerun()
