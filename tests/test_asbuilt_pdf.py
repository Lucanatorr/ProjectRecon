"""Sprint 2.2 — as-built PDF table extraction (FR-4, table path; OCR is Phase 4)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from recon.ingest.asbuilt_pdf import extract_asbuilt_pdf

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PDF = SAMPLES / "AsBuilt_PhaseB.pdf"


@pytest.fixture(scope="module", autouse=True)
def ensure_samples():
    if not PDF.exists():
        subprocess.run([sys.executable, str(SAMPLES / "generate_samples.py")], check=True)


def test_extract_grouped_quantities():
    lines, report = extract_asbuilt_pdf(PDF)
    by_desc = {l.raw_desc: l for l in lines}
    # two aerial segments summed to the golden built quantity
    aerial = [l for l in lines if "aerial" in l.raw_desc.lower()]
    assert len(aerial) == 1
    assert aerial[0].qty == pytest.approx(41320)
    assert by_desc["Handhole 30x48"].qty == pytest.approx(58)
    assert report.n_pages == 1
    assert report.n_tables == 1
    assert report.n_rows == 7


def test_rows_tagged_pdf_confidence():
    lines, _ = extract_asbuilt_pdf(PDF)
    assert lines and all(l.confidence == "pdf" for l in lines)
    assert all(l.source_file == "AsBuilt_PhaseB.pdf" for l in lines)


def test_subtotal_row_dropped():
    lines, _ = extract_asbuilt_pdf(PDF)
    assert not any(l.raw_desc.lower() == "subtotal" for l in lines)


def test_clean_pdf_does_not_need_ocr():
    _, report = extract_asbuilt_pdf(PDF)
    assert report.needs_ocr is False
    assert report.image_only_pages == []


def _canvas_pdf(path: Path, *, draw):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    draw(c)
    c.showPage()
    c.save()


def test_image_only_page_flagged_for_ocr(tmp_path):
    # a page with only a filled rectangle (no text, no table) -> scanned-like
    scanned = tmp_path / "scanned.pdf"
    _canvas_pdf(scanned, draw=lambda c: c.rect(100, 100, 300, 200, fill=1))
    lines, report = extract_asbuilt_pdf(scanned)
    assert lines == []
    assert report.needs_ocr is True
    assert report.image_only_pages == [1]
    assert any("scanned" in w.lower() or "ocr" in w.lower() for w in report.warnings)


def test_text_without_table_warns(tmp_path):
    # a page with prose but no table -> warning, no rows (drawing-set case)
    prose = tmp_path / "prose.pdf"

    def draw(c):
        c.drawString(72, 720, "Field notes: crew completed the western span.")
        c.drawString(72, 700, "See attached drawings for handhole locations.")

    _canvas_pdf(prose, draw=draw)
    lines, report = extract_asbuilt_pdf(prose)
    assert lines == []
    assert report.needs_ocr is False          # text present, just no table
    assert any("no as-built table" in w.lower() for w in report.warnings)
