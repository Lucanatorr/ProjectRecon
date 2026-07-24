"""Step 1 — As-built: upload tally sheet or PDF; confirm built quantities.

Trusted tally sheets render as the mockup's badge table (read-only, editable on
demand). PDF extractions are lower-confidence, so they land in an editable review
grid up front — the human confirms every number before it counts (spec §5b)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import ROOT
from recon.ingest.asbuilt_pdf import extract_asbuilt_pdf
from recon.ingest.tally import parse_tally
from recon.models import AsBuiltLine, UoM
from ui.progress import is_new_upload, loading_bar, show_flash, upload_signature
from ui.state import WizardState
from ui.theme import badge, card_close, card_open, lede, table_html, td
from ui.uploads import save_upload

SAMPLE = ROOT / "samples" / "AsBuilt_PhaseB_Tally.xlsx"
PDF_SAMPLE = ROOT / "samples" / "AsBuilt_PhaseB.pdf"
SCAN_SAMPLE = ROOT / "samples" / "AsBuilt_PhaseB_scanned.pdf"

_LEDE = ("Upload the tally sheet or as-built PDF. Structured tally sheets are summed "
         "by unit automatically; PDF extractions land in an editable grid so you "
         "confirm every number before it counts.")

# Confidence values that still require a human to review the numbers.
_UNCONFIRMED = ("pdf", "ocr")


def _conf_badge(confidence: str) -> str:
    if confidence == "sum":
        return badge("Tally sum", "ok")
    if confidence == "confirmed":
        return badge("Confirmed", "ok")
    if confidence == "ocr":
        return badge("OCR · verify", "low")
    return badge("PDF · verify", "low")


def render(state: WizardState) -> None:
    st.markdown(lede(_LEDE), unsafe_allow_html=True)
    show_flash(state)
    _uploader(state)

    # warnings first: when an extraction yields nothing (e.g. a scan with no OCR
    # installed) the explanation is the only thing worth showing.
    for w in state.asbuilt_warnings:
        st.warning(w)

    if not state.asbuilt:
        st.info("Upload a tally sheet or PDF, or click **Load sample**.")
        return

    if any(a.confidence in _UNCONFIRMED for a in state.asbuilt):
        _render_review_grid(state)          # PDF/OCR — confirm before it counts
    else:
        _render_confirmed_table(state)      # trusted tally / already confirmed


def _render_confirmed_table(state: WizardState) -> None:
    note = f'Source: {state.asbuilt_source or "—"} · summed by unit'
    html = card_open(f"Built quantities · {len(state.asbuilt)} units", note)
    headers = [("Description", ""), ("Built qty", "r"), ("UoM", ""),
               ("Segment", ""), ("Confidence", "")]
    rows = []
    for a in state.asbuilt:
        rows.append([
            td(a.raw_desc),
            td(f"{a.qty:,.0f}", "r num"),
            td(a.uom.value if a.uom else ""),
            td(a.segment or ""),
            f"<td>{_conf_badge(a.confidence)}</td>",
        ])
    html += table_html(headers, rows) + card_close()
    st.markdown(html, unsafe_allow_html=True)

    if st.button("Confirm built quantities", type="primary"):
        state.done.add("asbuilt")
        st.success("Built quantities confirmed.")

    with st.expander("Edit built quantities"):
        _editor(state, key="asbuilt_editor", confirm_label="Apply edits")


def _render_review_grid(state: WizardState) -> None:
    st.markdown(
        f'<div class="card__t" style="margin-bottom:4px">Review extracted quantities '
        f'· {len(state.asbuilt)} units</div>'
        f'<div class="card__note" style="margin-bottom:10px">Source: '
        f'{state.asbuilt_source or "—"} · extracted from PDF — correct anything that '
        f'looks off, then confirm.</div>', unsafe_allow_html=True)
    _editor(state, key="asbuilt_review", confirm_label="Confirm built quantities",
            confirm=True)


def _uploader(state: WizardState) -> None:
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    with c1:
        up = st.file_uploader("Tally sheet or as-built PDF (xlsx / csv / pdf)",
                             type=["xlsx", "csv", "pdf"], key="asbuilt_up")
    with c2:
        st.write("")
        st.write("")
        if st.button("Load tally", use_container_width=True, key="ab_sample") \
                and SAMPLE.exists():
            with loading_bar("Loading sample tally…") as step:
                step(40, "Summing by unit…")
                state.asbuilt = parse_tally(SAMPLE)
                step(100, "Done")
            state.asbuilt_source = SAMPLE.name
            state.asbuilt_warnings = []
            _log_asbuilt_load(state, SAMPLE.name)
            state.flash = f"Loaded {len(state.asbuilt)} built units."
            st.rerun()
    with c3:
        st.write("")
        st.write("")
        if st.button("Load PDF", use_container_width=True, key="ab_pdf_sample") \
                and PDF_SAMPLE.exists():
            with loading_bar("Loading sample PDF…") as step:
                step(45, "Extracting table…")
                lines, report = extract_asbuilt_pdf(PDF_SAMPLE)
                step(100, "Done")
            state.asbuilt = lines
            state.asbuilt_source = PDF_SAMPLE.name
            state.asbuilt_warnings = list(report.warnings)
            _log_asbuilt_load(state, PDF_SAMPLE.name)
            state.flash = f"Extracted {len(lines)} built units from the PDF."
            st.rerun()
    with c4:
        st.write("")
        st.write("")
        if st.button("Load scan", use_container_width=True, key="ab_scan_sample",
                     help="A scanned (image-only) as-built — needs OCR to read.") \
                and SCAN_SAMPLE.exists():
            with loading_bar("Reading scanned PDF…") as step:
                step(45, "Running OCR…")
                lines, report = extract_asbuilt_pdf(SCAN_SAMPLE)
                step(100, "Done")
            state.asbuilt = lines
            state.asbuilt_source = SCAN_SAMPLE.name
            state.asbuilt_warnings = list(report.warnings)
            _log_asbuilt_load(state, SCAN_SAMPLE.name)
            state.flash = (f"Read {len(lines)} built units by OCR."
                           if report.ocr_pages else
                           "Scanned PDF could not be read — see the warning below.")
            st.rerun()
    if up is not None and is_new_upload("asbuilt_up_sig", upload_signature(up)):
        try:
            with loading_bar("Loading as-built…") as step:
                step(20, "Reading file…")
                path = save_upload(up)
                is_pdf = path.suffix.lower() == ".pdf"
                step(55, "Extracting table…" if is_pdf else "Summing by unit…")
                if is_pdf:
                    lines, report = extract_asbuilt_pdf(path)
                    state.asbuilt = lines
                    state.asbuilt_warnings = list(report.warnings)
                else:
                    state.asbuilt = parse_tally(path)
                    state.asbuilt_warnings = []
                step(100, "Done")
            state.asbuilt_source = up.name
            _log_asbuilt_load(state, up.name)
            state.flash = f"Loaded {len(state.asbuilt)} built units."
            st.rerun()
        except ValueError as e:
            st.error(f"Could not parse as-built: {e}")


def _log_asbuilt_load(state: WizardState, source: str) -> None:
    from ui.db import log_action
    kinds = sorted({a.confidence for a in state.asbuilt})
    log_action("load_asbuilt", "asbuilt", actor=state.reviewer or None,
               detail={"source": source, "units": len(state.asbuilt),
                       "confidence": kinds,
                       "warnings": len(state.asbuilt_warnings)})


def _editor(state: WizardState, *, key: str, confirm_label: str,
            confirm: bool = False) -> None:
    df = pd.DataFrame([{
        "Description": a.raw_desc, "Built qty": a.qty,
        "UoM": a.uom.value if a.uom else "", "Segment": a.segment or "",
        "Confidence": a.confidence,
    } for a in state.asbuilt])
    edited = st.data_editor(
        df, use_container_width=True, hide_index=True, key=key, num_rows="dynamic",
        disabled=["Confidence"],
        column_config={"Built qty": st.column_config.NumberColumn(format="%.3f")})
    if st.button(confirm_label, type="primary", key=f"{key}_apply"):
        state.asbuilt = [
            AsBuiltLine(
                raw_desc=str(r["Description"]).strip(),
                qty=float(r["Built qty"] or 0),
                uom=UoM.from_str(r["UoM"]),
                segment=str(r["Segment"]).strip() or None,
                # confirming a reviewed PDF row makes it trusted
                confidence="confirmed" if confirm else str(r["Confidence"]),
            )
            for _, r in edited.iterrows()
            if str(r["Description"]).strip() and str(r["Description"]).strip().lower() != "nan"
        ]
        if confirm:
            from ui.db import log_action
            state.done.add("asbuilt")
            log_action("confirm_asbuilt", "asbuilt", actor=state.reviewer or None,
                       detail={"source": state.asbuilt_source,
                               "units": len(state.asbuilt)})
        st.rerun()
