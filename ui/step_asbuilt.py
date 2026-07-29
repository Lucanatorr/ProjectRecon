"""Step 1 — As-built: load a tally sheet or a marked-up construction PDF; confirm
built quantities.

Trusted tally sheets render as a read-only badge table. A construction PDF's
**Adobe comment annotations** (one per span/structure) are parsed straight from the
PDF's text — no OCR — into quantities keyed by the rate sheet's codes; those land in
an editable review grid so the coordinator confirms every number, and assigns codes
to the handful of items that vary (conduit, pedestal, splice), before it counts."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import ROOT
from recon.ingest.asbuilt_annot import (
    extract_annotations,
    parse_annotations,
    to_asbuilt_lines,
)
from recon.ingest.tally import parse_tally
from recon.models import AsBuiltLine, UoM
from ui.progress import is_new_upload, loading_bar, show_flash, upload_signature
from ui.state import WizardState
from ui.theme import badge, card_close, card_open, lede, table_html, td
from ui.uploads import save_upload

SAMPLE = ROOT / "samples" / "AsBuilt_PhaseB_Tally.xlsx"

_LEDE = ("Load the tally sheet or the marked-up construction PDF. Tally sheets are "
         "summed by unit; a PDF's Adobe comments are parsed into quantities by rate "
         "code and land in an editable grid so you confirm every number — and code "
         "the few items that vary — before it counts.")

# Confidence values that still require a human to review the numbers.
_UNCONFIRMED = ("annot",)


def _conf_badge(confidence: str) -> str:
    if confidence == "sum":
        return badge("Tally sum", "ok")
    if confidence == "confirmed":
        return badge("Confirmed", "ok")
    if confidence == "annot":
        return badge("PDF comments · verify", "low")
    return badge("Verify", "low")


def render(state: WizardState) -> None:
    st.markdown(lede(_LEDE), unsafe_allow_html=True)
    show_flash(state)
    _uploader(state)

    for w in state.asbuilt_warnings:
        st.warning(w)
    _unresolved_panel()

    if not state.asbuilt:
        st.info("Load a tally sheet or a marked-up construction PDF, or click "
                "**Load sample tally**.")
        return

    if any(a.confidence in _UNCONFIRMED for a in state.asbuilt):
        _render_review_grid(state)          # PDF comments — confirm before it counts
    else:
        _render_confirmed_table(state)      # trusted tally / already confirmed


def _render_confirmed_table(state: WizardState) -> None:
    note = f'Source: {state.asbuilt_source or "—"} · summed by unit'
    html = card_open(f"Built quantities · {len(state.asbuilt)} units", note)
    headers = [("Code", ""), ("Description", ""), ("Built qty", "r"), ("UoM", ""),
               ("Confidence", "")]
    rows = []
    for a in state.asbuilt:
        rows.append([
            td(a.code or "—", "code"),
            td(a.raw_desc),
            td(f"{a.qty:,.0f}", "r num"),
            td(a.uom.value if a.uom else ""),
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
        f'<div class="card__t" style="margin-bottom:4px">Review parsed quantities '
        f'· {len(state.asbuilt)} lines</div>'
        f'<div class="card__note" style="margin-bottom:10px">Source: '
        f'{state.asbuilt_source or "—"} · from PDF comments — correct anything that '
        f'looks off and set a code for any blank one, then confirm.</div>',
        unsafe_allow_html=True)
    _editor(state, key="asbuilt_review", confirm_label="Confirm built quantities",
            confirm=True)


def _unresolved_panel() -> None:
    """Comment tokens that couldn't be auto-classified (conduit / pedestal / splice
    vary) — listed so the coordinator can add them by hand in the grid."""
    items = st.session_state.get("_annot_unresolved") or []
    if not items:
        return
    with st.expander(f"Items needing manual entry · {len(items)}"):
        st.caption("These comment notes vary (conduit size/method, pedestal size, "
                   "splice type), so they aren't auto-coded — add them as rows below "
                   "with the right rate code.")
        headers = [("Page", ""), ("Comment token", "")]
        body = [[td(f"p{p}"), td(t)] for p, t in items]
        st.markdown(table_html(headers, body), unsafe_allow_html=True)


def _uploader(state: WizardState) -> None:
    c1, c2 = st.columns([3, 1])
    with c1:
        up = st.file_uploader(
            "Tally sheet (xlsx / csv) or marked-up construction PDF",
            type=["xlsx", "csv", "pdf"], key="asbuilt_up")
    with c2:
        st.write("")
        st.write("")
        if st.button("Load sample tally", use_container_width=True, key="ab_sample") \
                and SAMPLE.exists():
            with loading_bar("Loading sample tally…") as step:
                step(40, "Summing by unit…")
                state.asbuilt = parse_tally(SAMPLE)
                step(100, "Done")
            state.asbuilt_source = SAMPLE.name
            state.asbuilt_warnings = []
            st.session_state.pop("_annot_unresolved", None)
            _log_asbuilt_load(state, SAMPLE.name)
            state.flash = f"Loaded {len(state.asbuilt)} built units."
            st.rerun()

    if up is not None and is_new_upload("asbuilt_up_sig", upload_signature(up)):
        try:
            path = save_upload(up)
            if path.suffix.lower() == ".pdf":
                _ingest_pdf_annotations(state, path, up.name)
            else:
                with loading_bar("Loading tally…") as step:
                    step(55, "Summing by unit…")
                    state.asbuilt = parse_tally(path)
                    step(100, "Done")
                state.asbuilt_source = up.name
                state.asbuilt_warnings = []
                st.session_state.pop("_annot_unresolved", None)
                _log_asbuilt_load(state, up.name)
                state.flash = f"Loaded {len(state.asbuilt)} built units."
            st.rerun()
        except ValueError as e:
            st.error(f"Could not parse as-built: {e}")


def _ingest_pdf_annotations(state: WizardState, path, name: str) -> None:
    """Parse a construction PDF's comment annotations into as-built quantities."""
    with loading_bar("Reading PDF comments…") as step:
        step(30, "Extracting comments…")
        res = parse_annotations(extract_annotations(path))
        step(80, "Deriving quantities…")
        state.asbuilt = to_asbuilt_lines(res)
        step(100, "Done")
    state.asbuilt_source = name
    st.session_state["_annot_unresolved"] = res.unresolved

    warnings = []
    if res.excluded:
        warnings.append(f"{len(res.excluded)} span(s) marked “DID NOT BUILD” were "
                        "excluded.")
    if res.unresolved:
        warnings.append(f"{len(res.unresolved)} comment item(s) vary (conduit / "
                        "pedestal / splice) and need manual entry — see the list "
                        "below, then add them in the grid.")
    state.asbuilt_warnings = warnings
    _log_asbuilt_load(state, name)
    state.flash = (f"Parsed {len(state.asbuilt)} quantity line(s) from "
                   f"{res.records} PDF comment(s).")


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
        "Code": a.code or "", "Description": a.raw_desc, "Built qty": a.qty,
        "UoM": a.uom.value if a.uom else "", "Confidence": a.confidence,
    } for a in state.asbuilt])
    edited = st.data_editor(
        df, use_container_width=True, hide_index=True, key=key, num_rows="dynamic",
        disabled=["Confidence"],
        column_config={"Built qty": st.column_config.NumberColumn(format="%.3f")})
    if st.button(confirm_label, type="primary", key=f"{key}_apply"):
        rows = []
        for _, r in edited.iterrows():
            desc = str(r["Description"]).strip()
            if not desc or desc.lower() == "nan":
                continue
            rows.append(AsBuiltLine(
                raw_desc=desc, qty=float(r["Built qty"] or 0),
                uom=UoM.from_str(r["UoM"]), code=str(r["Code"]).strip() or None,
                confidence="confirmed" if confirm else str(r["Confidence"])))
        state.asbuilt = rows
        # coded lines carry their rate code straight through — seed the crosswalk so
        # they don't need description matching later.
        for a in rows:
            if a.code:
                state.resolved[a.raw_desc] = a.code
        if confirm:
            from ui.db import log_action
            state.done.add("asbuilt")
            log_action("confirm_asbuilt", "asbuilt", actor=state.reviewer or None,
                       detail={"source": state.asbuilt_source, "units": len(rows)})
        st.rerun()
