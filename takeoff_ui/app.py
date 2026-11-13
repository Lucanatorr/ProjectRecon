"""Field takeoff — entry point inside Project Recon (``?app=takeoff``).

Rendered by the main ``app.py`` so both apps run on one server and share one
session id: switching between Reconciliation and Field takeoff keeps each
app's work in place.
"""
from __future__ import annotations

import streamlit as st

from takeoff_ui.chrome import sidebar_html, topbar_html
from takeoff_ui.state import VIEWS, get_state, recon_step
from takeoff_ui.views import render_drawings, render_invoices
from ui.state import get_sid

_TITLES = {k: t for k, t, _ in VIEWS}


def render() -> None:
    sid = get_sid()
    state = get_state(sid)
    view = st.query_params.get("view")
    if view in _TITLES:
        state.view = view
    st.sidebar.markdown(sidebar_html(state, sid, recon_step(sid)), unsafe_allow_html=True)
    st.markdown(topbar_html(_TITLES[state.view]), unsafe_allow_html=True)
    if state.view == "invoices":
        render_invoices(state, sid)
    else:
        render_drawings(state)
