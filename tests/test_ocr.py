"""Sprint 4.5 — OCR fallback for scanned as-built PDFs (FR-4, NFR-4).

The table-reconstruction logic is tested with synthetic word boxes so it is
verified without the Tesseract binary; the end-to-end OCR pass is skipped when
Tesseract isn't installed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from recon.ingest.asbuilt_pdf import extract_asbuilt_pdf
from recon.ingest.ocr import INSTALL_HINT, ocr_status, words_to_grid

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SCANNED = SAMPLES / "AsBuilt_PhaseB_scanned.pdf"

OCR_OK, OCR_MSG = ocr_status()
requires_tesseract = pytest.mark.skipif(
    not OCR_OK, reason=f"Tesseract not available: {OCR_MSG}")


@pytest.fixture(scope="module", autouse=True)
def ensure_samples():
    if not SCANNED.exists():
        subprocess.run([sys.executable, str(SAMPLES / "generate_samples.py")], check=True)


def _w(text, left, top, line, width=None):
    # default width approximates a proportional font so intra-cell gaps stay small
    return {"text": text, "left": left, "top": top, "line": line,
            "width": len(text) * 9 if width is None else width}


# --- table reconstruction from OCR word boxes (no Tesseract needed) ---
def test_words_to_grid_rebuilds_columns():
    words = [
        _w("Segment", 100, 100, 1), _w("Description", 300, 100, 1),
        _w("Qty", 700, 100, 1), _w("UoM", 850, 100, 1),
        _w("WF-02", 100, 140, 2), _w("144F", 300, 140, 2),
        _w("Aerial", 345, 140, 2), _w("Fiber", 400, 140, 2),
        _w("20800", 700, 140, 2), _w("FT", 850, 140, 2),
    ]
    grid = words_to_grid(words, cell_gap=25, col_gap=25)
    assert grid[0] == ["Segment", "Description", "Qty", "UoM"]
    # words separated by ordinary spaces stay in ONE cell, not three columns
    assert grid[1] == ["WF-02", "144F Aerial Fiber", "20800", "FT"]


def test_empty_cell_keeps_row_alignment():
    """The point of page-wide column bands: a missing cell must not shift the row."""
    words = [
        _w("Segment", 100, 100, 1), _w("Description", 300, 100, 1),
        _w("Qty", 700, 100, 1),
        _w("Handhole", 300, 140, 2), _w("58", 700, 140, 2),   # no segment
    ]
    grid = words_to_grid(words, cell_gap=25, col_gap=25)
    assert grid[1] == ["", "Handhole", "58"]                  # qty stays in column 3


def test_rows_come_back_in_reading_order():
    words = [_w("third", 100, 300, 3), _w("first", 100, 100, 1), _w("second", 100, 200, 2)]
    assert [r[0] for r in words_to_grid(words)] == ["first", "second", "third"]


def test_blank_and_empty_input():
    assert words_to_grid([]) == []
    assert words_to_grid([_w("   ", 10, 10, 1)]) == []


def test_ocr_status_is_safe_to_call():
    ok, msg = ocr_status()
    assert isinstance(ok, bool) and isinstance(msg, str) and msg
    if not ok:
        assert "Tesseract" in msg or "pytesseract" in msg


# --- graceful degradation when Tesseract is absent ---
@pytest.mark.skipif(OCR_OK, reason="Tesseract is installed; degradation path n/a")
def test_scanned_pdf_reports_install_hint_when_ocr_missing():
    lines, report = extract_asbuilt_pdf(SCANNED)
    assert lines == []
    assert report.image_only_pages == [1]
    assert report.ocr_pages == []
    assert report.ocr_available is False
    assert report.needs_ocr is True
    joined = " ".join(report.warnings)
    assert "scanned" in joined
    assert INSTALL_HINT.split("—")[0].strip() in joined     # tells the user how to fix it


def test_ocr_can_be_disabled_explicitly():
    _, report = extract_asbuilt_pdf(SCANNED, ocr=False)
    assert report.ocr_pages == []
    assert report.needs_ocr is True


def test_text_pdf_never_needs_ocr():
    _, report = extract_asbuilt_pdf(SAMPLES / "AsBuilt_PhaseB.pdf")
    assert report.image_only_pages == []
    assert report.needs_ocr is False


# --- end-to-end OCR (requires the Tesseract binary) ---
@requires_tesseract
def test_scanned_pdf_is_read_by_ocr():
    lines, report = extract_asbuilt_pdf(SCANNED)
    assert report.ocr_available is True
    assert report.ocr_pages == [1]
    assert report.needs_ocr is False
    assert lines, "OCR produced no as-built rows"
    # OCR output is never trusted without a human confirming it
    assert all(l.confidence == "ocr" for l in lines)
    assert any("read by OCR" in w for w in report.warnings)


@requires_tesseract
def test_ocr_recovers_the_aerial_quantity():
    lines, _ = extract_asbuilt_pdf(SCANNED)
    aerial = [l for l in lines if "aerial" in l.raw_desc.lower()]
    assert aerial, [l.raw_desc for l in lines]
    # the two aerial segments total 41,320 ft in the source document
    assert aerial[0].qty == pytest.approx(41320, rel=0.02)
