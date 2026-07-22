"""Sprint 2.1 — zip ingest for invoices (FR-5)."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest

from recon.ingest.invoices import parse_invoices

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
INVOICE = SAMPLES / "Invoice_2025-06_PhaseB.xlsx"


@pytest.fixture(scope="module", autouse=True)
def ensure_samples():
    if not INVOICE.exists():
        import subprocess
        import sys
        subprocess.run([sys.executable, str(SAMPLES / "generate_samples.py")], check=True)


def _make_zip(tmp_path: Path, members: dict[str, Path]) -> Path:
    """members: arcname -> source file path."""
    z = tmp_path / "invoices.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for arc, src in members.items():
            zf.write(src, arcname=arc)
    return z


def test_zip_with_single_invoice(tmp_path):
    direct = parse_invoices([INVOICE])
    z = _make_zip(tmp_path, {"Invoice_2025-06_PhaseB.xlsx": INVOICE})
    zipped = parse_invoices([z])
    assert len(zipped) == len(direct) > 0
    assert {l.raw_desc for l in zipped} == {l.raw_desc for l in direct}


def test_zip_dedupes_same_invoice_twice(tmp_path):
    # same invoice under two arcnames must not double-count (dedup key ignores file)
    z = _make_zip(tmp_path, {
        "a/Invoice_2025-06_PhaseB.xlsx": INVOICE,
        "b/Invoice_2025-06_PhaseB.xlsx": INVOICE,
    })
    once = parse_invoices([INVOICE])
    zipped = parse_invoices([z])
    assert len(zipped) == len(once)


def test_zip_nested_dirs_and_original_names(tmp_path):
    z = _make_zip(tmp_path, {"2025-06/payapp/Invoice_2025-06_PhaseB.xlsx": INVOICE})
    lines = parse_invoices([z])
    assert lines
    # original filename preserved for traceability despite nested archive path
    assert all(l.source_file == "Invoice_2025-06_PhaseB.xlsx" for l in lines)


def test_zip_skips_non_invoice_members(tmp_path):
    # a stray reference spreadsheet with no desc/qty columns must be skipped,
    # not raise, while the real invoice still parses
    junk = tmp_path / "field_notes.csv"
    pd.DataFrame({"note": ["ok"], "author": ["x"]}).to_csv(junk, index=False)
    z = _make_zip(tmp_path, {
        "Invoice_2025-06_PhaseB.xlsx": INVOICE,
        "field_notes.csv": junk,
    })
    lines = parse_invoices([z])
    assert lines
    assert all("note" not in l.raw_desc.lower() for l in lines)


def test_zip_skips_unreadable_pdf_member(tmp_path):
    # a corrupt/unreadable PDF inside a zip is skipped, not fatal; the real
    # invoice still parses (valid PDF invoices in zips are covered in test_invoice_pdf)
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 not really a pdf")
    z = _make_zip(tmp_path, {
        "Invoice_2025-06_PhaseB.xlsx": INVOICE,
        "scan.pdf": fake_pdf,
    })
    lines = parse_invoices([z])          # must not raise on the bad pdf
    assert lines


def test_direct_bad_file_still_raises(tmp_path):
    # a directly supplied unparseable file should still surface an error
    bad = tmp_path / "not_an_invoice.csv"
    pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(bad, index=False)
    with pytest.raises(ValueError):
        parse_invoices([bad])
