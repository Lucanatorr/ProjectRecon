"""Field-takeoff session state.

Lives in the same kind of process-global store as the Reconciliation wizard,
keyed by the same ``?sid=`` — so moving between the two apps (which reloads the
page) keeps each app's work exactly where it was left.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import streamlit as st

from takeoff.drawings import DrawingTakeoff
from takeoff.invoices import InvoiceSet

VIEWS = (("drawings", "Drawings", "Materials & fiber from comments"),
         ("invoices", "Invoices", "Line items, quantities & totals"))


@dataclass
class TakeoffState:
    view: str = "drawings"
    drawings: list[DrawingTakeoff] = field(default_factory=list)
    drawing_uploads: list[str] = field(default_factory=list)
    invoices: InvoiceSet | None = None
    invoice_uploads: list[str] = field(default_factory=list)
    sent_to_recon: str = ""          # what was last handed to Reconciliation
    flash: str = ""


@st.cache_resource
def _store() -> dict:
    return {}


def get_state(sid: str) -> TakeoffState:
    store = _store()
    if sid not in store:
        store[sid] = TakeoffState()
    return store[sid]


def recon_step(sid: str) -> str:
    """Where the Reconciliation link should land: the step that session's wizard
    was last on, or Home when it hasn't been opened."""
    from ui.state import _state_store
    ws = _state_store().get(sid)
    return ws.current if ws is not None and ws.current else "home"
