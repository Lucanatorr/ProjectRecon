"""Step 0 — Contract: load/confirm the bid schedule; add change orders.

The loaded schedule renders as the mockup's card + .tbl table (HTML). Uploads,
edits, and change orders are Streamlit widgets styled to match."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import ROOT
from recon.contract import apply_change_orders, load_bid_schedule
from recon.models import ContractItem, UoM
from ui.state import WizardState
from ui.theme import card_close, card_open, lede, table_html, td
from ui.uploads import save_upload

SAMPLE = ROOT / "samples" / "Fiber_Build_2025_BidSchedule.xlsx"

_LEDE = ("The bid schedule is the anchor. Every quantity and price on an invoice "
         "reconciles against these authoritative unit rates. Load it once per job; "
         "change orders extend it.")


def render(state: WizardState) -> None:
    st.markdown(lede(_LEDE), unsafe_allow_html=True)

    if not state.contract:
        _uploader(state)
        st.info("No bid schedule loaded yet. Upload one or click **Load sample** to "
                "use the Robeson CAB — PON 5 demo data.")
        return

    # --- rendered schedule card (matches the mockup) ---
    note = (f'Source: {state.contract_source or "—"}')
    html = card_open(f"Bid schedule · {len(state.contract)} units", note)
    headers = [("Code", ""), ("Unit", ""), ("UoM", ""),
               ("Unit price", "r"), ("Est. qty", "r"), ("CO", "")]
    rows = []
    for ci in state.contract:
        rows.append([
            td(ci.code, "code"),
            td(ci.description),
            td(ci.uom.value),
            td(f"${ci.unit_price:,.2f}", "r num"),
            td(f"{ci.est_qty:,.0f}", "r num"),
            td("✓" if ci.is_change_order else ""),
        ])
    html += table_html(headers, rows) + card_close()
    st.markdown(html, unsafe_allow_html=True)

    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("Confirm bid schedule", type="primary", use_container_width=True):
            state.done.add("contract")
            st.success("Confirmed.")

    with st.expander("Replace schedule · add change order · edit values"):
        _uploader(state, compact=True)
        co = st.file_uploader("Change order (xlsx / csv)", type=["xlsx", "csv"],
                             key="co_up")
        if co is not None:
            try:
                state.contract = apply_change_orders(state.contract, save_upload(co))
                st.success("Change order applied.")
            except ValueError as e:
                st.error(f"Could not parse change order: {e}")
        _editor(state)


def _uploader(state: WizardState, compact: bool = False) -> None:
    c1, c2 = st.columns([3, 1])
    with c1:
        up = st.file_uploader("Bid schedule (xlsx / csv)", type=["xlsx", "csv"],
                             key="contract_up")
    with c2:
        st.write("")
        st.write("")
        if st.button("Load sample", use_container_width=True, key="c_sample") \
                and SAMPLE.exists():
            state.contract = load_bid_schedule(SAMPLE)
            state.contract_source = SAMPLE.name
            state.done.add("contract")
            st.rerun()
    if up is not None:
        try:
            state.contract = load_bid_schedule(save_upload(up))
            state.contract_source = up.name
            state.done.add("contract")
            st.rerun()
        except ValueError as e:
            st.error(f"Could not parse bid schedule: {e}")


def _editor(state: WizardState) -> None:
    df = pd.DataFrame([{
        "Code": ci.code, "Unit": ci.description, "UoM": ci.uom.value,
        "Unit price": ci.unit_price, "Est. qty": ci.est_qty,
        "CO": ci.is_change_order,
    } for ci in state.contract])
    edited = st.data_editor(df, use_container_width=True, hide_index=True,
                           key="contract_editor", num_rows="dynamic")
    if st.button("Apply edits", key="apply_contract"):
        state.contract = _items_from_df(edited)
        st.rerun()


def _items_from_df(df: pd.DataFrame) -> list[ContractItem]:
    items: list[ContractItem] = []
    for _, r in df.iterrows():
        code = str(r["Code"]).strip()
        if not code or code.lower() == "nan":
            continue
        items.append(ContractItem(
            code=code, description=str(r["Unit"]).strip(),
            uom=UoM.from_str(r["UoM"]) or UoM.EA,
            unit_price=float(r["Unit price"] or 0),
            est_qty=float(r["Est. qty"] or 0),
            is_change_order=bool(r.get("CO", False)),
        ))
    return items
