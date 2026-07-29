"""Generate sample data files for the Robeson CAB — PON 5, Cycle 04 scenario
(the mockup example). Produces a bid schedule, a tally sheet, and an invoice as
real xlsx/csv files so the app and ingest tests have realistic input.

Run:  python samples/generate_samples.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def bid_schedule() -> pd.DataFrame:
    return pd.DataFrame([
        ("3.1", "Place 144ct ADSS aerial fiber", "FT", 1.85, 42000),
        ("3.2", "Place 288ct ADSS aerial fiber", "FT", 2.60, 8500),
        ("4.1", 'Directional bore 2"', "FT", 9.50, 6000),
        ("4.2", "Trench / plow fiber", "FT", 4.25, 12000),
        ("5.1", "Handhole 30x48", "EA", 340.00, 55),
        ("5.2", "Fiber pedestal", "EA", 185.00, 120),
        ("6.1", "Fusion splice (per fiber)", "EA", 7.50, 9600),
        ("6.2", "Splice closure", "EA", 210.00, 48),
        ("7.1", "OTDR test (per fiber)", "EA", 4.00, 9600),
        ("8.1", "Pole make-ready", "EA", 95.00, 310),
        ("9.1", "Drop placement", "EA", 145.00, 400),
    ], columns=["Code", "Description", "UoM", "Unit Price", "Est Qty"])


def tally_sheet() -> pd.DataFrame:
    # As-built with multiple segments and free-text descriptions (pre-crosswalk),
    # plus a subtotal row that the parser must ignore.
    return pd.DataFrame([
        ("WF-02", "144F Aerial Fiber (ADSS)", 20800, "FT"),
        ("WF-03", "144F Aerial Fiber (ADSS)", 20520, "FT"),
        ("UG-01", "Directional Bore 2 inch", 6180, "FT"),
        ("UG-02", "Trench / plow fiber", 11240, "FT"),
        ("STR-01", "Handhole 30x48", 58, "EA"),
        ("SPL-01", "Fusion Splice per fiber", 9720, "EA"),
        ("POLE-01", "Pole Make-Ready", 298, "EA"),
        ("DROP-01", "Drop Placement", 372, "EA"),
        ("", "Subtotal", 0, ""),  # noise row — must be dropped
    ], columns=["Segment", "Description", "Qty", "UoM"])


def invoice() -> pd.DataFrame:
    # Contractor invoice (cumulative pay app) with contractor wording that must be
    # crosswalked, an over-billed qty, an over-contract price, and a not-in-contract line.
    return pd.DataFrame([
        ("2025-06", "144F ADSS Aerial Place", 43900, 1.85),
        ("2025-06", "Directional Drilling 2 inch", 6180, 10.25),
        ("2025-06", "Trench / plow fiber", 11240, 4.25),
        ("2025-06", "Handhole 30x48", 58, 340.00),
        ("2025-06", "Fusion splice (per fiber)", 9720, 7.50),
        ("2025-06", "Pole make-ready", 298, 95.00),
        ("2025-06", "Drop placement", 350, 145.00),
        ("2025-06", "Traffic Control / Flagging (day)", 40, 650.00),
    ], columns=["Invoice", "Description", "Qty", "Unit Price"])


def write_asbuilt_pdf(path: Path) -> None:
    """Render the same tally data as a gridded PDF table (Sprint 2.2 fixture)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    df = tally_sheet()
    data = [list(df.columns)] + [[str(c) for c in row] for row in df.values.tolist()]
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    table = Table(data)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#18223a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    doc.build([Paragraph("As-Built Tally — Phase B (PON 5)", styles["Title"]),
               Spacer(1, 12), table])


def write_invoice_pdf(path: Path, inv_df) -> None:
    """Render the invoice as a gridded PDF table (Sprint 2.4 fixture)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    cols = ["Invoice", "Description", "Qty", "Unit Price", "Amount"]
    df = inv_df[cols]
    data = [cols] + [[str(c) for c in row] for row in df.values.tolist()]
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    table = Table(data)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#18223a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    doc.build([Paragraph("Pay Application 04 — Ace Fiber Constructors", styles["Title"]),
               Spacer(1, 12), table])


def change_order() -> pd.DataFrame:
    # Authorizes the higher directional-bore rate ($9.50 → $10.25) that was
    # otherwise flagged as an over-contract price. Same code = price revision.
    return pd.DataFrame([
        ("4.1", 'Directional bore 2"', "FT", 10.25, 6000),
    ], columns=["Code", "Description", "UoM", "Unit Price", "Est Qty"])


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    bid_schedule().to_excel(HERE / "Fiber_Build_2025_BidSchedule.xlsx", index=False)
    change_order().to_excel(HERE / "ChangeOrder_01.xlsx", index=False)
    tally_sheet().to_excel(HERE / "AsBuilt_PhaseB_Tally.xlsx", index=False)
    inv = invoice()
    inv["Amount"] = inv["Qty"] * inv["Unit Price"]
    inv_path = HERE / "Invoice_2025-06_PhaseB.xlsx"
    inv.to_excel(inv_path, index=False)

    # a zip bundle (invoice + a reference file that must be skipped) to demo Sprint 2.1
    import zipfile
    notes = HERE / "field_notes.csv"
    pd.DataFrame({"note": ["ONT swapped at NID"], "author": ["crew-4"]}).to_csv(
        notes, index=False)
    with zipfile.ZipFile(HERE / "Invoices_bundle.zip", "w") as zf:
        zf.write(inv_path, arcname="payapp/Invoice_2025-06_PhaseB.xlsx")
        zf.write(notes, arcname="reference/field_notes.csv")
    notes.unlink()

    write_asbuilt_pdf(HERE / "AsBuilt_PhaseB.pdf")
    write_invoice_pdf(HERE / "Invoice_2025-06_PhaseB.pdf", inv)
    print("Wrote sample files to", HERE)


if __name__ == "__main__":
    main()
