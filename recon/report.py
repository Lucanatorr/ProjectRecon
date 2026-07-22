"""Excel/PDF report builders.

The Excel workbook is the reviewable, sign-off-able artifact and audit trail:
tabs for Summary, Flagged, Full detail, Unmatched, with conditional formatting on
variance. See spec §9 / SDD §5.8.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from recon.models import ReconRow, Severity
from recon.reconcile import CycleTotals

# Severity → fill color (matches the mockup palette).
_FILL = {
    Severity.CRITICAL: PatternFill("solid", fgColor="FBE9E7"),
    Severity.WARNING: PatternFill("solid", fgColor="FBF0DF"),
    Severity.INFO: PatternFill("solid", fgColor="EEF1F6"),
    Severity.OK: PatternFill("solid", fgColor="E6F2EC"),
}
_HEADER_FILL = PatternFill("solid", fgColor="18223A")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
_MONEY = "$#,##0.00"
_NUM = "#,##0.###"

_DETAIL_COLS = [
    ("Code", "code"), ("Description", "description"), ("UoM", "uom"),
    ("Built qty", "built_qty"), ("Billed qty", "billed_qty"),
    ("Contract $", "contract_price"), ("Billed $", "billed_price"),
    ("Est qty", "est_qty"), ("Billed amount", "billed_amount"),
    ("Expected amount", "expected_amount"), ("Variance", "amount_variance"),
    ("Severity", "severity"), ("Flags", "flags"),
]


def _row_values(r: ReconRow) -> dict:
    return {
        "code": r.code or "—",
        "description": r.description,
        "uom": r.uom.value,
        "built_qty": r.built_qty,
        "billed_qty": r.billed_qty,
        "contract_price": r.contract_price,
        "billed_price": r.billed_price,
        "est_qty": r.est_qty,
        "billed_amount": r.billed_amount,
        "expected_amount": r.expected_amount,
        "amount_variance": r.amount_variance,
        "severity": r.severity.value,
        "flags": " | ".join(f.message for f in r.flags),
    }


def _write_sheet(ws, rows: list[ReconRow]) -> None:
    # header
    for c, (label, _) in enumerate(_DETAIL_COLS, start=1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left")
    # body
    for ri, r in enumerate(rows, start=2):
        vals = _row_values(r)
        fill = _FILL.get(r.severity)
        for ci, (_, key) in enumerate(_DETAIL_COLS, start=1):
            cell = ws.cell(row=ri, column=ci, value=vals[key])
            if key in ("contract_price", "billed_price", "billed_amount",
                       "expected_amount", "amount_variance"):
                cell.number_format = _MONEY
            elif key in ("built_qty", "billed_qty", "est_qty"):
                cell.number_format = _NUM
            if fill and key in ("severity", "amount_variance"):
                cell.fill = fill
    # widths
    for c, (label, _) in enumerate(_DETAIL_COLS, start=1):
        ws.column_dimensions[get_column_letter(c)].width = max(12, len(label) + 4)
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["M"].width = 50
    ws.freeze_panes = "A2"


def _write_summary(ws, totals: CycleTotals, cycle_label: str) -> None:
    ws["A1"] = "Splice — Reconciliation Summary"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = cycle_label
    ws["A2"].font = Font(color="5C6B80")

    metrics = [
        ("Billed this cycle (gross)", totals.total_billed, _MONEY),
        ("Expected (built × contract)", totals.total_expected, _MONEY),
        ("Flagged over-billing", totals.flagged_over_billing, _MONEY),
        ("Retainage held", totals.retainage_held, _MONEY),
        ("Net recommended", totals.net_recommended, _MONEY),
        ("Critical flags", totals.n_critical, "0"),
        ("Warnings", totals.n_warning, "0"),
        ("Reconciled / info", totals.n_ok, "0"),
    ]
    for i, (label, val, fmt) in enumerate(metrics, start=4):
        ws.cell(row=i, column=1, value=label).font = Font(bold=True)
        c = ws.cell(row=i, column=2, value=val)
        c.number_format = fmt
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 18


def build_workbook(rows: list[ReconRow], totals: CycleTotals,
                  cycle_label: str = "") -> bytes:
    """Build the multi-tab Excel workbook and return it as bytes."""
    wb = Workbook()

    ws_sum = wb.active
    ws_sum.title = "Summary"
    _write_summary(ws_sum, totals, cycle_label)

    flagged = [r for r in rows if r.severity in (Severity.CRITICAL, Severity.WARNING)]
    _write_sheet(wb.create_sheet("Flagged"), flagged)
    _write_sheet(wb.create_sheet("Full detail"), rows)

    unmatched = [r for r in rows if r.code is None or r.contract_price is None]
    _write_sheet(wb.create_sheet("Unmatched"), unmatched)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_pdf_summary(rows: list[ReconRow], totals: CycleTotals,
                     cycle_label: str = "") -> bytes:
    """One-page text/PDF summary for the approval packet.

    Phase 1 emits a plain-text summary (bytes). A formatted PDF via reportlab
    lands in Phase 4; keeping the signature stable now.
    """
    lines = [
        "SPLICE — RECONCILIATION SUMMARY",
        cycle_label,
        "=" * 48,
        f"Billed this cycle (gross): ${totals.total_billed:,.2f}",
        f"Expected (built x contract): ${totals.total_expected:,.2f}",
        f"Flagged over-billing:        ${totals.flagged_over_billing:,.2f}",
        f"Retainage held:              ${totals.retainage_held:,.2f}",
        f"Net recommended:             ${totals.net_recommended:,.2f}",
        "",
        f"Critical: {totals.n_critical}  Warnings: {totals.n_warning}  "
        f"OK/Info: {totals.n_ok}",
        "-" * 48,
        "FLAGGED ITEMS",
    ]
    for r in rows:
        if r.severity in (Severity.CRITICAL, Severity.WARNING):
            lines.append(
                f"[{r.severity.value.upper():8}] {r.code or '—':>5} "
                f"{r.description[:32]:32} variance ${r.amount_variance:,.2f}")
            for f in r.flags:
                lines.append(f"           - {f.message}")
    return ("\n".join(lines)).encode("utf-8")
