"""Sprint 3.5 — built-to-date vs billed-to-date trend across cycles (FR-16)."""
from __future__ import annotations

import pytest

from recon.models import AsBuiltLine, ContractItem, InvoiceLine, UoM
from recon.persistence import Database
from recon.reconcile import reconcile
from ui.theme import trend_html


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _save(db, cycle_no, built, billed, *, period):
    """Save a cycle where `built` qty is documented and `billed` qty invoiced."""
    contract = [ContractItem("A", "Unit A", UoM.EA, 10.0, 1000)]
    rows = reconcile([AsBuiltLine("A", built, UoM.EA, code="A")],
                     [InvoiceLine("INV", "A", billed, 10.0, billed * 10.0, code="A")],
                     contract)
    return db.save_cycle_snapshot(
        project_name="Job", contractor=None, area=None, cycle_no=cycle_no,
        period_label=period, billing_mode="cumulative", retainage_pct=10.0,
        prior_billed=0.0, contract_items=contract, rows=rows)


def test_trend_returns_built_and_billed_per_cycle(db):
    _save(db, 1, built=100, billed=100, period="Jan")
    _save(db, 2, built=250, billed=300, period="Feb")   # billing outpaces built
    pid = db.project_by_name("Job")["id"]

    t = db.trend(pid)
    assert [r["cycle_no"] for r in t] == [1, 2]
    assert t[0]["built_value"] == pytest.approx(1000)    # 100 × $10
    assert t[0]["billed_value"] == pytest.approx(1000)
    assert t[1]["built_value"] == pytest.approx(2500)
    assert t[1]["billed_value"] == pytest.approx(3000)


def test_trend_empty_for_unknown_project(db):
    assert db.trend(999) == []


def test_cycle_summaries_drive_the_trend_view(db):
    _save(db, 1, built=100, billed=100, period="Jan")
    _save(db, 2, built=250, billed=300, period="Feb")
    pid = db.project_by_name("Job")["id"]
    summaries = db.cycle_summaries(pid)

    html = trend_html(summaries)
    assert "Cycle 01" in html and "Cycle 02" in html
    assert "Jan" in html and "Feb" in html
    # cycle 2 over-billed by $500 (3000 billed vs 2500 built)
    assert "+$500" in html
    # bars are drawn relative to the largest value across cycles
    assert 'width:100.0%' in html


def test_trend_html_empty_without_cycles():
    assert trend_html([]) == ""


def test_trend_html_is_blank_line_free():
    # guards the Markdown HTML-block rule (a blank line spills raw HTML as text)
    html = trend_html([
        {"cycle_no": 1, "period_label": "Jan", "expected": 100.0, "billed": 120.0},
        {"cycle_no": 2, "period_label": None, "expected": 200.0, "billed": 180.0},
    ])
    assert "\n" not in html
    assert "Cycle 02" in html and "—" in html      # missing period renders a dash
