"""Field takeoff — Excel exports, and the links that join the two apps."""
from __future__ import annotations

import io

from openpyxl import load_workbook

from recon.ingest.asbuilt_annot import Annotation
from takeoff.drawings import DrawingFile, build_takeoff
from takeoff.export import drawing_workbook, invoice_workbook
from takeoff.invoices import InvoiceSet, check, parse_page
from tests.takeoff_fixtures import acme_invoice


def _drawing():
    pages = [["MATERIALS\n2-STRAIGHT\n3'-COPPER", "48CT\n1000-600=400'"],
             ["MATERIALS\n1-STRAIGHT CLAMP", "ZZA001\n300-100=200'"]]
    f = DrawingFile("a.pdf", 2, [Annotation(page=p, text=c.replace("\n", "\r"))
                                 for p, cs in enumerate(pages, 1) for c in cs])
    return build_takeoff("Maple Ridge", [f])


def test_drawing_workbook_sheets_and_formulas():
    wb = load_workbook(io.BytesIO(drawing_workbook(_drawing())))
    assert wb.sheetnames == ["Material Totals", "Fiber Totals", "Material Detail",
                             "Fiber Detail", "Source Files", "Notes"]
    assert wb.calculation.fullCalcOnLoad
    mt = wb["Material Totals"]
    rows = {mt.cell(r, 1).value: mt.cell(r, 3).value for r in range(5, 8)}
    assert {"STRAIGHT CLAMP", "COPPER"} <= set(rows)
    assert str(rows["STRAIGHT CLAMP"]).startswith("=SUMIFS('Material Detail'!$D$2:$D$4")
    detail = [[c.value for c in r] for r in wb["Material Detail"].iter_rows(min_row=2, max_col=6)]
    assert sum(r[3] for r in detail if r[5] == "STRAIGHT CLAMP") == 3
    ft = wb["Fiber Totals"]
    assert ft.cell(5, 1).value == 48
    assert any(ft.cell(r, 1).value == "ZZA001" for r in range(5, 25))


def test_invoice_workbook_one_sheet_with_greyed_repeat():
    page = acme_invoice("A1", "May 5, 2024", ['10-Placed Pedestal | $50.00/ea | $500.00'], "$500.00")
    docs = [parse_page("\n".join(l.replace(" | ", " ") for l in page), "x.pdf", n) for n in (1, 2)]
    wb = load_workbook(io.BytesIO(invoice_workbook(check(InvoiceSet(docs=docs)), "Acme")))
    assert wb.sheetnames == ["Invoices"]
    ws = wb.active
    vals = [[c.value for c in r] for r in ws.iter_rows(min_row=6, max_row=7, max_col=14)]
    assert vals[0][13] == "Yes" and vals[1][13].startswith("No - repeat of x.pdf p1")
    assert vals[0][11] == "=ROUND(H6*J6,2)"


def test_app_switch_links_share_the_session():
    from ui.theme import app_switch_html, sidebar_html, sidebar_home_html
    html = app_switch_html("takeoff", "abc123", "invoices")
    assert 'href="?app=takeoff&sid=abc123"' in html
    assert 'href="?step=invoices&sid=abc123"' in html
    assert '<a class="on" href="?app=takeoff' in html
    # both Reconciliation sidebars carry the switch
    assert "?app=takeoff&sid=s1" in sidebar_html([], "P", "", "s1")
    assert "?app=takeoff&sid=s1" in sidebar_home_html(0, 0, 0, [], "s1")


def test_takeoff_sidebar_links_back_to_reconciliation():
    from takeoff_ui.chrome import sidebar_html
    from takeoff_ui.state import TakeoffState
    html = sidebar_html(TakeoffState(view="invoices"), "s9", "crosswalk")
    assert 'href="?step=crosswalk&sid=s9"' in html          # back where Recon was left
    assert 'href="?app=takeoff&view=drawings&sid=s9"' in html
    assert '<a class="on" href="?app=takeoff&view=invoices&sid=s9"' in html
