"""Sprint 2.5 — per-contractor template profiles (parsing + persistence)."""
from __future__ import annotations

from pathlib import Path

import pytest

from recon.ingest.invoice_pdf import (
    extract_tables,
    parse_invoice_pdf,
    suggest_mapping,
)
from recon.models import TemplateProfile
from recon.persistence import Database


def _pdf_with_table(path: Path, rows: list[list[str]]):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    t = Table(rows)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    doc.build([t])


# a PDF whose headers carry no recognizable tokens -> auto-detection fails
_OPAQUE = [
    ["C1", "C2", "C3", "C4"],
    ["x", "Fusion splice", "100", "7.50"],
    ["x", "Directional bore", "6180", "10.25"],
]


def test_auto_detect_fails_on_opaque_headers(tmp_path):
    pdf = tmp_path / "opaque.pdf"
    _pdf_with_table(pdf, _OPAQUE)
    assert parse_invoice_pdf(pdf) == []          # no tokens -> nothing auto-parsed


def test_profile_parses_opaque_invoice(tmp_path):
    pdf = tmp_path / "opaque.pdf"
    _pdf_with_table(pdf, _OPAQUE)
    profile = TemplateProfile(
        contractor="Opaque Co",
        columns={"desc": 1, "qty": 2, "price": 3},
        header_row=0,
    )
    lines = parse_invoice_pdf(pdf, profile=profile)
    assert len(lines) == 2
    fusion = next(l for l in lines if "Fusion" in l.raw_desc)
    assert fusion.qty == pytest.approx(100)
    assert fusion.unit_price == pytest.approx(7.5)
    assert fusion.amount == pytest.approx(750.0)   # derived qty*price


def test_invalid_profile_yields_nothing(tmp_path):
    pdf = tmp_path / "opaque.pdf"
    _pdf_with_table(pdf, _OPAQUE)
    bad = TemplateProfile(contractor="x", columns={"desc": 1})   # no qty
    assert not bad.is_valid()
    assert parse_invoice_pdf(pdf, profile=bad) == []


def test_extract_tables_and_suggest_mapping(tmp_path):
    pdf = tmp_path / "std.pdf"
    _pdf_with_table(pdf, [
        ["Description", "Qty", "Unit Price", "Amount"],
        ["Trench", "100", "4.25", "425.00"],
    ])
    grids = extract_tables(pdf)
    assert len(grids) == 1
    assert grids[0][0] == ["Description", "Qty", "Unit Price", "Amount"]
    header_row, cols = suggest_mapping(grids[0])
    assert header_row == 0
    assert cols["desc"] == 0 and cols["qty"] == 1 and cols["price"] == 2
    assert cols["amount"] == 3


# --- persistence ---
@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def test_profile_roundtrip(db):
    p = TemplateProfile(contractor="Ace Fiber",
                        columns={"desc": 1, "qty": 2, "price": 3, "amount": 4},
                        header_row=1, table_index=0)
    db.save_template_profile(p)
    loaded = db.load_template_profile("Ace Fiber")
    assert loaded is not None
    assert loaded.columns == {"desc": 1, "qty": 2, "price": 3, "amount": 4}
    assert loaded.header_row == 1
    assert "Ace Fiber" in db.list_template_profiles()


def test_profile_upsert_and_delete(db):
    db.save_template_profile(TemplateProfile("Ace", {"desc": 0, "qty": 1}))
    db.save_template_profile(TemplateProfile("Ace", {"desc": 2, "qty": 3}))  # update
    loaded = db.load_template_profile("Ace")
    assert loaded.columns == {"desc": 2, "qty": 3}       # last write wins
    assert db.list_template_profiles() == ["Ace"]        # not duplicated
    db.delete_template_profile("Ace")
    assert db.load_template_profile("Ace") is None


def test_load_missing_profile_returns_none(db):
    assert db.load_template_profile("Nobody") is None
    assert db.load_template_profile("") is None


def test_end_to_end_saved_profile_parses_next_invoice(db, tmp_path):
    # a coordinator maps a contractor's opaque invoice once, saves it, and the
    # next invoice in the same layout parses straight through
    pdf = tmp_path / "opaque.pdf"
    _pdf_with_table(pdf, _OPAQUE)
    db.save_template_profile(TemplateProfile(
        "Opaque Co", {"desc": 1, "qty": 2, "price": 3}, header_row=0))

    from recon.ingest.invoices import parse_invoices
    profile = db.load_template_profile("Opaque Co")
    lines = parse_invoices([pdf], profile=profile)
    assert len(lines) == 2
