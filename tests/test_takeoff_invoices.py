"""Field takeoff — invoices: line items, quantities and totals from invoice PDFs,
plus the hand-off into a Reconciliation cycle. All invoices are synthetic."""
from __future__ import annotations

import pytest

from takeoff.invoices import (
    InvoiceSet,
    check,
    invoices_from_sources,
    parse_page,
    to_recon_lines,
    work_item,
)
from takeoff.sources import PdfSource
from tests.takeoff_fixtures import acme_invoice, invoice_pdf

ROWS_A = ['120ft- Directional Bore- Pulling 2" | $7.50/ft | $900.00',
          "5000ft-Pulling Fiber through Installed Duct | $.55/ft | $2,750.00"]


def _text(lines):
    return "\n".join(l.replace(" | ", " ") for l in lines)


def _set(*pages):
    docs = [parse_page(_text(p), "inv.pdf", i) for i, p in enumerate(pages, 1)]
    return check(InvoiceSet(docs=[d for d in docs if d]))


def test_header_rows_and_total():
    d = parse_page(_text(acme_invoice("A101", "January 29, 2024", ROWS_A, "$3,650.00")), "f.pdf", 1)
    assert (d.number, str(d.date), d.vendor, d.bill_to, d.total) == \
        ("A101", "2024-01-29", "ACME UNDERGROUND, LLC", "Example Electric Co-op", 3650.0)
    assert d.project == "FB-TEST-0001 Maple Ridge"
    assert [(i.qty, i.unit, i.rate, i.amount) for i in d.items] == \
        [(120, "FT", 7.5, 900.0), (5000, "FT", 0.55, 2750.0)]
    assert d.line_sum == d.total


def test_quantity_forms():
    rows = ["-1,320ft-Directional Bore-Pulling 2\" | $7.50/ft | $9,900.00",
            "640-Pull Additional Duct | $1.50/ft | $960.00",
            "4-Set Small Vault | $175.00/ea | $700.00",
            "- 2 Men, truck, trailer & excavator - 3 hours | $140.00/ea | $420.00",
            "- Set LV Box 24x48x36 | $210.00/ea | $210.00"]
    d = parse_page(_text(acme_invoice("A1", "May 5, 2024", rows, "$12,190.00")), "f.pdf", 1)
    assert [(i.qty, i.unit) for i in d.items] == [(1320, "FT"), (640, "FT"), (4, "EA"),
                                                  (3, "HR"), (1, "EA")]
    assert "no quantity written" in d.items[-1].notes[0]


def test_amount_with_stray_spaces_and_total_printed_above_the_word_total():
    lines = acme_invoice("A2", "October 20, 2023",
                         ['640ft- Directional Bore- Pulling 2" | $7.50/ft | $ 4 ,800.00'], "")
    lines[-2:-1] = ["$4,800.00", "Total"]          # amount on its own line, above "Total"
    d = parse_page(_text(lines), "f.pdf", 1)
    assert d.items[0].amount == 4800.0 and d.total == 4800.0


def test_multi_site_invoice_keeps_each_location():
    rows = ["Oak Hollow Ct.", "-2,000ft Pulling fiber through existing duct | $0.55/ft | $1,100.00",
            "Mill Pond Rd.", "- 1,200ft-Pulling 4ct fiber through existing duct | $.55/ft | $660.00"]
    d = parse_page(_text(acme_invoice("A3", "July 22, 2024", rows, "$1,760.00", project="")), "f.pdf", 1)
    assert [i.section for i in d.items] == ["Oak Hollow Ct.", "Mill Pond Rd."]


def test_mispriced_line_is_flagged_and_billed_amount_kept():
    s = _set(acme_invoice("A4", "October 3, 2023",
                          ['2500ft- Directional Bore- Pulling 2" | $7.50/ft | $17,500.00'], "$17,500.00"))
    item = s.items()[0]
    assert item.amount == 17500.0 and item.qty_x_rate == 18750.0 and item.difference == -1250.0
    assert any("1,250.00" in why for *_, why in s.flagged())


def test_repeat_page_counted_once_and_conflicting_number_flagged():
    page = acme_invoice("A5", "May 25, 2024", ROWS_A, "$3,650.00")
    other = acme_invoice("A6", "May 25, 2024", ROWS_A[:1], "$900.00")
    clash = acme_invoice("A6", "May 26, 2024", ROWS_A[1:], "$2,750.00")
    s = _set(page, other, page, clash)
    assert [d.counted for d in s.docs] == [True, True, False, True]
    t = s.totals()
    assert (t["unique_invoices"], t["unique_total"], t["all_pages_total"]) == (3, 7300.0, 10950.0)
    assert len(s.conflicts) == 1


def test_unit_follows_the_work_not_a_mistyped_rate_unit():
    rows = ["-3-Set Pedestal | $50.00/ft | $150.00", "-300 Pull Additional Duct | $1.50/ea | $450.00"]
    s = _set(acme_invoice("A7", "June 6, 2024", rows, "$600.00"))
    assert [(i.work_item, i.unit) for i in s.items()] == [("Set/place pedestal", "EA"),
                                                         ("Pull additional duct", "FT")]
    assert all(i.notes for i in s.items())


def test_rate_unlike_the_usual_rate_for_the_same_work_is_flagged():
    rows = ["2-Large Vault-24x48x36 | $210.00/ea | $420.00", "1-Set Large Vault | $210.00/ea | $210.00",
            "-2-Set Large Vault | $175.00/ea | $350.00"]
    s = _set(acme_invoice("A8", "May 25, 2024", rows, "$980.00"))
    flagged = [i for i in s.items() if i.notes]
    assert len(flagged) == 1 and flagged[0].rate == 175.0


@pytest.mark.parametrize("desc, kind", [
    ('120ft- Directional Bore- Pulling 2"', 'Directional bore, pulling 2"'),
    ("2,000ft-Directional Bore", 'Directional bore, pulling 2"'),
    ("1,100ft-Trench/Plow", "Trench/plow"),
    ("380ft- Pulled Additional", "Pull additional duct"),
    ("2400 ft-Pulled Fiver Through Installed Duct", "Pull fiber through installed/existing duct"),
    ("-1,900ft-Pulling 24ct Fiber through existing duct", "Pull 24ct fiber through existing duct"),
    ("3-Set HH-24x36x36", "Set small vault / box / handhole 24x36x36"),
    ("1-Extra LV-36x60x36", "Set extra large vault 36x60x36"),
    ("4-Install widget", "Install widget"),
])
def test_work_item_grouping(desc, kind):
    assert work_item(desc) == kind


def test_end_to_end_pdf_and_recon_lines():
    pdf = invoice_pdf([acme_invoice("A9", "February 2, 2024", ROWS_A, "$3,650.00"),
                       ["Remittance copy — not an invoice"]])
    s = invoices_from_sources([PdfSource("acme.pdf", pdf)])
    assert len(s.docs) == 1 and s.skipped_pages == [("acme.pdf", 2)]
    assert s.totals()["unique_total"] == 3650.0
    lines = to_recon_lines(s)
    assert [(l.invoice_id, l.raw_desc, l.qty, l.unit_price, l.amount, l.is_cumulative)
            for l in lines] == [
        ("A9", 'Directional bore, pulling 2"', 120, 7.5, 900.0, False),
        ("A9", "Pull fiber through installed/existing duct", 5000, 0.55, 2750.0, False)]
    assert lines[0].period == "2024-02-02" and lines[0].source_file == "acme.pdf"


def test_handoff_sets_the_recon_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr("recon.persistence.DB_PATH", tmp_path / "t.db")
    from takeoff_ui.handoff import send_invoices_to_recon
    from ui.state import WizardState
    ws = WizardState(billing_mode="cumulative")
    ws.done.add("invoices")
    page = acme_invoice("A10", "March 1, 2024", ROWS_A, "$3,650.00")
    s = _set(page, page)
    summary = send_invoices_to_recon(ws, s)
    assert len(ws.invoices) == 2 and ws.billing_mode == "discrete"
    assert "invoices" not in ws.done                 # the reviewer still confirms
    assert "$3,650.00" in summary and ws.flash


def test_cover_sheets_and_bare_numbers_are_not_invoices():
    cover = ("EXAMPLE UTILITY CO  INVOICE INFORMATION\nBILLING ADDRESS\nInvoice #\n"
             "Invoice Date :\nTotal Amount Due: 98,000.00")
    assert parse_page(cover, "d.pdf", 1) is None              # no number with a digit
    ref = "See INVOICE A77 for the prior period.\nNotes only."
    assert parse_page(ref, "d.pdf", 1) is None                 # no rows, no total


def test_pages_without_an_invoice_and_an_amount_are_skipped_before_layout_reading():
    pdf = invoice_pdf([["SHEET 4 OF 12 — AERIAL PLAN", "Invoice references: see cover"],
                       acme_invoice("A11", "April 1, 2024", ROWS_A, "$3,650.00")])
    s = invoices_from_sources([PdfSource("mixed.pdf", pdf)])
    assert [d.number for d in s.docs] == ["A11"] and s.skipped_pages == [("mixed.pdf", 1)]
