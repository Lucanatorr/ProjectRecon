"""Splice — Contractor Invoice ↔ As-Built Reconciliation Tool.

Streamlit entry point + step router. Run:  streamlit run app.py

The presentation layer only orchestrates session state and calls the pure-Python
domain core (recon/*). The chrome (sidebar stepper, top bar) is rendered as custom
HTML to match reconciliation_mockup.html; navigation is via query-param links.
"""
from __future__ import annotations

import streamlit as st

from ui import (
    step_asbuilt,
    step_contract,
    step_crosswalk,
    step_export,
    step_invoices,
    step_reconcile,
)
from ui.state import STEPS, get_sid, get_state, load_demo, step_meta
from ui.theme import CSS, sidebar_html, topbar_html

st.set_page_config(page_title="Project Recon — Invoice Reconciliation",
                   page_icon="🟦", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(CSS, unsafe_allow_html=True)

RENDERERS = {
    "contract": step_contract.render,
    "asbuilt": step_asbuilt.render,
    "invoices": step_invoices.render,
    "crosswalk": step_crosswalk.render,
    "reconcile": step_reconcile.render,
    "export": step_export.render,
}
_KEYS = [k for k, _, _ in STEPS]
_TITLES = {k: t for k, t, _ in STEPS}


def _sync_step(state) -> str:
    """Resolve the active step from the ?step= query param (source of truth for
    navigation) and mirror it into session state."""
    qp = st.query_params.get("step")
    if qp in RENDERERS:
        state.current = qp
    elif state.current not in RENDERERS:
        state.current = "contract"
    return state.current


def _top_actions(current: str) -> list[dict]:
    """Back / Continue links matching the mockup's top-bar actions."""
    idx = _KEYS.index(current)
    actions = []
    if idx > 0:
        actions.append({"label": "Back", "href": _KEYS[idx - 1], "primary": False})
    if idx < len(_KEYS) - 1:
        nxt = _KEYS[idx + 1]
        actions.append({"label": f"Continue to {_TITLES[nxt].lower()}",
                        "href": nxt, "primary": True})
    return actions


def _project_meta(state) -> str:
    parts = [state.contractor, f"Cycle {int(state.cycle_no):02d}" if state.cycle_no else "",
             state.period_label]
    return " · ".join(p for p in parts if p)


def render_sidebar(state, sid: str) -> None:
    st.sidebar.markdown(
        sidebar_html(step_meta(state), state.project_name, _project_meta(state), sid),
        unsafe_allow_html=True)
    with st.sidebar.expander("⚙  Project settings"):
        state.project_name = st.text_input("Project name", state.project_name)
        state.contractor = st.text_input("Contractor", state.contractor)
        state.area = st.text_input("Area", state.area)
        state.cycle_no = st.number_input("Cycle no.", min_value=1,
                                        value=int(state.cycle_no or 1))
        state.period_label = st.text_input("Period label", state.period_label)


def main() -> None:
    state = get_state()
    sid = get_sid()
    if st.query_params.get("demo") and not state.contract:
        load_demo(state)
    current = _sync_step(state)

    render_sidebar(state, sid)

    st.markdown(topbar_html(state.project_name, _TITLES[current],
                            _top_actions(current), sid), unsafe_allow_html=True)

    RENDERERS[current](state)


if __name__ == "__main__":
    main()
