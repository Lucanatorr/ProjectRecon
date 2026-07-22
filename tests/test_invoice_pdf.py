"""Sprint 2.4 — PDF invoice parsing (FR-5, PDF path)."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from recon.ingest.invoice_pdf import parse_invoice_pdf
from recon.ingest.invoices import parse_invoices

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
INVOICE_PDF = SAMPLES / "Invoice_2025-06_PhaseB.pdf"


@pytest.fixture(scope="module", autouse=True)
def ensure_samples():
    if not INVOICE_PDF.exists():
        subprocess.run([sys.executable, str(SAMPLES / "generate_samples.py")], check=True)


def _pdf_with_table(path: Path, rows: list[list[str]]):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    t = Table(rows)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    doc.build([t])


def test_extract_all_lines():
    lines = parse_invoice_pdf(INVOICE_PDF)
    assert len(lines) == 8
    by = {l.raw_desc: l for l in lines}
    aerial = by["144F ADSS Aerial Place"]
    assert aerial.qty == pytest.approx(43900)
    assert aerial.unit_price == pytest.approx(1.85)
    assert aerial.amount == pytest.approx(81215.0)


def test_columns_not_confused():
    # "Unit Price" must map to price, never to the description column
    lines = parse_invoice_pdf(INVOICE_PDF)
    bore = next(l for l in lines if "Directional" in l.raw_desc)
    assert bore.unit_price == pytest.approx(10.25)     # the over-contract price
    assert bore.raw_desc == "Directional Drilling 2 inch"


def test_invoice_id_from_column():
    lines = parse_invoice_pdf(INVOICE_PDF)
    assert all(l.invoice_id == "2025-06" for l in lines)
    assert all(l.source_file == "Invoice_2025-06_PhaseB.pdf" for l in lines)


def test_derives_price_from_amount(tmp_path):
    # a table with amount but no unit-price column -> price derived as amount/qty
    pdf = tmp_path / "no_price.pdf"
    _pdf_with_table(pdf, [
        ["Description", "Qty", "Amount"],
        ["Fusion splice", "100", "750.00"],
    ])
    lines = parse_invoice_pdf(pdf)
    assert len(lines) == 1
    assert lines[0].unit_price == pytest.approx(7.5)


def test_total_row_skipped(tmp_path):
    pdf = tmp_path / "with_total.pdf"
    _pdf_with_table(pdf, [
        ["Description", "Qty", "Unit Price", "Amount"],
        ["Trench / plow", "100", "4.25", "425.00"],
        ["Total", "", "", "425.00"],
    ])
    lines = parse_invoice_pdf(pdf)
    assert len(lines) == 1
    assert lines[0].raw_desc == "Trench / plow"


def test_non_invoice_pdf_returns_empty(tmp_path):
    pdf = tmp_path / "notes.pdf"
    _pdf_with_table(pdf, [["Author", "Note"], ["crew-4", "swapped ONT"]])
    assert parse_invoice_pdf(pdf) == []          # no desc/qty table -> no lines


def test_unreadable_pdf_raises(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not a real pdf")
    with pytest.raises(ValueError):
        parse_invoice_pdf(bad)


def test_parse_invoices_dispatches_pdf():
    # the public entry point routes a .pdf to the PDF parser
    lines = parse_invoices([INVOICE_PDF])
    assert len(lines) == 8


def test_zip_with_pdf_invoice(tmp_path):
    z = tmp_path / "bundle.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(INVOICE_PDF, arcname="payapp/Invoice_2025-06_PhaseB.pdf")
    lines = parse_invoices([z])
    assert len(lines) == 8


def test_full_pipeline_all_pdf_reaches_golden():
    # PDF as-built + PDF invoice + contract → the same $35,408 headline
    from recon.contract import load_bid_schedule
    from recon.crosswalk import AliasStore, resolve
    from recon.ingest.asbuilt_pdf import extract_asbuilt_pdf
    from recon.reconcile import cycle_totals, reconcile

    contract = load_bid_schedule(SAMPLES / "Fiber_Build_2025_BidSchedule.xlsx")
    asbuilt, _ = extract_asbuilt_pdf(SAMPLES / "AsBuilt_PhaseB.pdf")
    invoices = parse_invoices([INVOICE_PDF])
    store = AliasStore()
    store.confirm("Directional Drilling 2 inch", "4.1")
    for ln in list(asbuilt) + list(invoices):
        ln.code = resolve(ln.raw_desc, contract, store).code
    rows = reconcile(asbuilt, invoices, contract)
    totals = cycle_totals(rows, retainage_pct=10.0)
    assert totals.flagged_over_billing == pytest.approx(35408.0)
    assert totals.n_critical == 3
