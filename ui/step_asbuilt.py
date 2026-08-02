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
from ui.theme import (
    badge,
    card_close,
    card_open,
    kpi_row_html,
    lede,
    table_html,
    td,
)
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
    _stationing_panel()
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


def _stationing_panel() -> None:
    """Cross-check of the stated span footages against the drawing's fiber
    sequentials. Any span whose footage doesn't close its stationing is listed —
    exact match, no tolerance."""
    rep = st.session_state.get("_annot_stationing")
    if not rep or not rep.verdicts:
        return
    from recon.ingest.stationing import PLAUSIBLE, VERIFIED

    n = len(rep.verdicts)
    n_ok = len(rep.by_verdict(VERIFIED))
    n_maybe = len(rep.by_verdict(PLAUSIBLE))
    todo = rep.unverified
    label = (f"Footage check · {len(todo)} span(s) to verify by hand" if todo
             else f"Footage check · all {n} spans accounted for")
    with st.expander(label, expanded=bool(todo)):
        st.caption("Every span's footage is checked against the distance between "
                   "fiber sequentials on the drawing — the same check done by hand, "
                   "on every span.")
        (st.warning if todo else st.success)(rep.summary())

        st.markdown(kpi_row_html([
            {"label": "Verified", "value": f"{n_ok}",
             "sub": "footage closes its run exactly"},
            {"label": "Consistent", "value": f"{n_maybe}",
             "sub": "matches a run on the route"},
            {"label": "Unaccounted", "value": f"{len(todo)}",
             "sub": "check these first", "flag": True},
        ]), unsafe_allow_html=True)

        if todo:
            st.markdown('<div class="card__t" style="margin-top:6px">Spans to '
                        'verify</div>', unsafe_allow_html=True)
            headers = [("Page", ""), ("Route", ""), ("Station", ""), ("Stated", "r")]
            body = [[td(f"p{v.page}"), td(v.route_label), td(f"{v.station:,}", "code"),
                     f'<td class="r num" style="color:var(--critical)">'
                     f'{v.span_ft:,.0f} ft</td>'] for v in todo]
            st.markdown(table_html(headers, body), unsafe_allow_html=True)
            st.markdown('<div class="hint">These footages match no distance between '
                        'any two sequentials on their route, so they are the most '
                        'likely to be mis-stated.</div>', unsafe_allow_html=True)

        if rep.conduit:
            bad = rep.conduit_failures
            st.markdown('<div class="card__t" style="margin-top:14px">Buried '
                        'conduit</div>'
                        f'<div class="card__note">{len(rep.conduit) - len(bad)} of '
                        f'{len(rep.conduit)} runs place the conduit their stationing '
                        'calls for.</div>', unsafe_allow_html=True)
            if bad:
                headers = [("Page", ""), ("Route", ""), ("Stationing", ""),
                           ("Needed", "r"), ("Claimed", "r"), ("Diff", "r")]
                body = [[td(f"p{c.page}"), td(c.route_label),
                         td(f"{c.station_from:,} → {c.station_to:,}", "code"),
                         td(f"{c.gap:,.0f} ft", "r num"),
                         td(f"{c.stated:,.0f} ft", "r num"),
                         f'<td class="r num" style="color:var(--critical)">'
                         f'{c.delta:+,.0f} ft</td>'] for c in bad]
                st.markdown(table_html(headers, body), unsafe_allow_html=True)

        if rep.failures:
            with st.expander(f"Chain detail · {len(rep.failures)} run(s) that "
                             "don't close"):
                headers = [("Page", ""), ("Route", ""), ("Stationing", ""),
                           ("Built", "r"), ("Stated", "r"), ("Diff", "r")]
                body = [[td(f"p{c.page}"), td(c.route_label),
                         td(f"{c.station_from:,} → {c.station_to:,}", "code"),
                         td(f"{c.gap:,.0f} ft", "r num"),
                         td(f"{c.stated:,.0f} ft", "r num"),
                         f'<td class="r num" style="color:var(--critical)">'
                         f'{c.delta:+,.0f} ft</td>'] for c in rep.failures]
                st.markdown(table_html(headers, body), unsafe_allow_html=True)
                st.markdown('<div class="hint">A run that doesn\'t close is often '
                            'the route leaving the sheet and returning, rather than '
                            'a bad footage — the list above is the sharper signal.'
                            '</div>', unsafe_allow_html=True)


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
            st.session_state.pop("_annot_stationing", None)
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
                st.session_state.pop("_annot_stationing", None)
                _log_asbuilt_load(state, up.name)
                state.flash = f"Loaded {len(state.asbuilt)} built units."
            st.rerun()
        except ValueError as e:
            st.error(f"Could not parse as-built: {e}")


def _ingest_pdf_annotations(state: WizardState, path, name: str) -> None:
    """Parse a construction PDF's comment annotations into as-built quantities,
    matched against the already-loaded bid schedule; unmatched items fall to the
    crosswalk."""
    from recon.ingest.stationing import check_stationing

    with loading_bar("Reading PDF comments…") as step:
        step(25, "Extracting comments…")
        res = parse_annotations(extract_annotations(path))
        step(55, "Checking footages against stationing…")
        stationing = check_stationing(res.span_records, res.coil_marks,
                                      res.buried_runs)
        step(80, "Matching to the bid schedule…")
        lines, resolved = to_asbuilt_lines(res, state.contract, state.aliases)
        state.asbuilt = lines
        step(100, "Done")
    state.asbuilt_source = name
    state.resolved.update(resolved)              # confident contract matches
    st.session_state["_annot_unresolved"] = res.unresolved
    st.session_state["_annot_stationing"] = stationing

    warnings = []
    if not state.contract:
        warnings.append("No bid schedule loaded yet — load the Contract step first "
                        "so quantities can be matched to contract codes.")
    unmatched = len(lines) - len(resolved)
    if state.contract and unmatched:
        warnings.append(f"{unmatched} of {len(lines)} line(s) didn't match the bid "
                        "schedule — resolve them in the Crosswalk step.")
    if stationing.unverified:
        warnings.append(
            f"{len(stationing.unverified)} span footage(s) match no distance on "
            "their route — see the footage check below.")
    if res.excluded:
        warnings.append(f"{len(res.excluded)} span(s) marked “DID NOT BUILD” were "
                        "excluded.")
    if res.unresolved:
        warnings.append(f"{len(res.unresolved)} comment item(s) vary (conduit / "
                        "pedestal / splice) and need manual entry — see the list "
                        "below, then add them in the grid.")
    state.asbuilt_warnings = warnings
    _log_asbuilt_load(state, name, stationing=stationing)
    state.flash = (f"Parsed {len(lines)} quantity line(s) from {res.records} PDF "
                   f"comment(s); {len(resolved)} matched the bid schedule.")


def _log_asbuilt_load(state: WizardState, source: str, stationing=None) -> None:
    from ui.db import log_action
    kinds = sorted({a.confidence for a in state.asbuilt})
    detail = {"source": source, "units": len(state.asbuilt), "confidence": kinds,
              "warnings": len(state.asbuilt_warnings)}
    if stationing is not None and stationing.checked:
        detail["stationing_checked"] = stationing.checked
        detail["stationing_unverified"] = len(stationing.unverified)
    log_action("load_asbuilt", "asbuilt", actor=state.reviewer or None,
               detail=detail)


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
