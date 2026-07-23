"""Upload feedback helpers: a loading bar shown while an upload is parsed, and a
guard so a staged file is processed once rather than on every rerun.

Streamlit's file_uploader keeps returning the staged file on every run, so the
naive ``if up: process(); st.rerun()`` re-parses forever. ``is_new_upload`` gates
processing to the run where the file actually changed."""
from __future__ import annotations

from contextlib import contextmanager

import streamlit as st


@contextmanager
def loading_bar(label: str = "Loading…"):
    """Show a progress bar for the duration of an ingest, then clear it. Yields a
    ``step(pct, text)`` callback to advance it through named stages."""
    holder = st.empty()
    bar = holder.progress(0, text=label)

    def step(pct: int, text: str | None = None) -> None:
        bar.progress(min(max(int(pct), 0), 100), text=text or label)

    try:
        yield step
    finally:
        holder.empty()


def upload_signature(files) -> tuple:
    """Stable signature of a file_uploader value (single file or list)."""
    if files is None:
        return ()
    seq = files if isinstance(files, (list, tuple)) else [files]
    return tuple((getattr(f, "name", ""), getattr(f, "size", None)) for f in seq)


def is_new_upload(key: str, sig: tuple) -> bool:
    """True the first time this signature is seen for ``key`` (per session). Storing
    it means a staged upload is parsed once, not on every subsequent rerun."""
    if not sig:
        return False
    if st.session_state.get(key) == sig:
        return False
    st.session_state[key] = sig
    return True


def show_flash(state) -> None:
    """Render (once) the completion message set by the last ingest, then clear it."""
    if getattr(state, "flash", ""):
        st.success(state.flash)
        state.flash = ""
