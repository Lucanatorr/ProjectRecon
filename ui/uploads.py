"""Helper to persist Streamlit UploadedFile objects to a temp path so the
ingest parsers (which take file paths) can read them."""
from __future__ import annotations

import tempfile
from pathlib import Path


def save_upload(uploaded) -> Path:
    """Write an UploadedFile to a temp file and return its path."""
    suffix = Path(uploaded.name).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(uploaded.getbuffer())
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


def save_bytes(data: bytes, suffix: str = "") -> Path:
    """Write raw bytes to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.flush()
    tmp.close()
    return Path(tmp.name)
