"""Excel workbooks for a drawing takeoff and an invoice takeoff.

Totals are live formulas (SUMIFS / COUNTIFS) over the detail sheets, so every
figure can be traced back to the comment or invoice line it came from. The
workbooks are flagged to recalculate when opened.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from takeoff.drawings import DrawingTakeoff
from takeoff.invoices import InvoiceSet

_H = Font(bold=True, color="FFFFFF")
_HF = PatternFill("solid", fgColor="1F4E78")
_B = Font(bold=True)
_TITLE = Font(bold=True, size=13)
_GREY = PatternFill("solid", fgColor="EDEDED")
_FLAG = PatternFill("solid", fgColor="FFF2CC")
_CUR = '"$"#,##0.00'
_NUM = "#,##0.##"


def _head(ws, cols, row=1):
    for i, c in enumerate(cols, 1):
        x = ws.cell(row, i, c)
        x.font, x.fill = _H, _HF
        x.alignment = Alignment(wrap_text=True, vertical="center")


def _widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _save(wb) -> bytes:
    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  drawings
# --------------------------------------------------------------------------- #
DRAWING_NOTES = [
    "The comment boxes are PDF annotations, read directly from each file (not by eye).",
    "Materials = every comment box headed MATERIALS. Spelling variants are combined; the "
    "\"Written as\" column on Material Totals lists exactly what was combined. STRAIGHT and "
    "STRAIGHT CLAMP are the same item, as are CURVE and CURVE/CURVED CLAMP, DG and DOWN GUY, "
    "GG and GUY GUARD. NUT, SQ NUT and BUG NUT are kept apart, as are WASHER, SQ WASHER and "
    "FLAT WASHER. A name that is not recognised is kept exactly as written.",
    "Copper and strand are in feet; lashing wire is in rolls (\".5 ROLL\" counts as 0.5).",
    "Fiber = every comment box with a sequential calculation (start - end = footage), using "
    "the footage written in the box. Lines that only repeat a box total (\"MST TOTAL=640'\") "
    "are not added; they are checked against the box instead.",
    "Fiber is grouped by fiber count (48 / 96 / 288 ...), whether written CT, AF or DF; the "
    "label breakdown keeps them apart. Boxes that name only a cable ID are totalled by cable.",
    "Files whose comments are identical to another file's (a resubmission) are listed on "
    "Source Files and counted once.",
    "The drawing's printed title-block table (MATERIAL / PROPOSED AMOUNT / ACTUAL AMOUNT) is "
    "design data, not a comment box, and is not counted.",
]


def drawing_workbook(t: DrawingTakeoff) -> bytes:
    wb = Workbook()
    mt = wb.active
    mt.title = "Material Totals"
    ft = wb.create_sheet("Fiber Totals")
    md = wb.create_sheet("Material Detail")
    fd = wb.create_sheet("Fiber Detail")
    sf = wb.create_sheet("Source Files")
    nt = wb.create_sheet("Notes")

    _head(md, ["File", "Page", "Line as written", "Qty", "Unit", "Material (normalized)",
               "Source", "Needs a look", "How it was read"])
    for m in t.materials:
        md.append([m.file, m.page, m.line, m.qty, m.unit, m.item, m.source,
                   "; ".join(m.notes), "; ".join(m.info)])
        if m.notes:
            for c in range(1, 10):
                md.cell(md.max_row, c).fill = _FLAG
    nmd = max(len(t.materials) + 1, 2)
    _widths(md, [34, 6, 30, 9, 7, 20, 18, 50, 45])
    md.freeze_panes = "A2"
    md.auto_filter.ref = f"A1:I{nmd}"

    _head(fd, ["File", "Page", "Label as written", "Fiber count", "Cable ID (no count written)",
               "Start seq", "End seq", "Seq unit", "Footage (ft)", "Calculation as written",
               "Needs a look", "How it was read"])
    for f in t.footage:
        fd.append([f.file, f.page, f.label, f.fiber_count, f.cable or None, f.start, f.end,
                   f.seq_unit, f.feet, f.line, "; ".join(f.notes), "; ".join(f.info)])
        if f.notes:
            for c in range(1, 13):
                fd.cell(fd.max_row, c).fill = _FLAG
    nfd = max(len(t.footage) + 1, 2)
    _widths(fd, [34, 6, 26, 11, 16, 10, 10, 8, 12, 26, 55, 45])
    fd.freeze_panes = "A2"
    fd.auto_filter.ref = f"A1:L{nfd}"

    counted = t.counted_files
    n_boxes = sum(1 for f in counted for c in f.comments if c.text.upper().lstrip().startswith("MATERIAL"))
    mt["A1"] = f"{t.name}: material totals from every MATERIALS comment box"
    mt["A1"].font = _TITLE
    mt["A2"] = (f"{n_boxes} MATERIALS boxes in {len(counted)} file(s)"
                f"{f'; {len(t.files) - len(counted)} duplicate file(s) not counted' if len(t.files) > len(counted) else ''}. "
                "Totals are SUMIFS over \"Material Detail\".")
    _head(mt, ["Material", "Unit", "Total", "Written as (variants combined)"], 4)
    totals = t.material_totals()
    for i, a in enumerate(totals):
        r = 5 + i
        mt.cell(r, 1, a["item"])
        mt.cell(r, 2, a["unit"])
        mt.cell(r, 3, f"=SUMIFS('Material Detail'!$D$2:$D${nmd},'Material Detail'!$F$2:$F${nmd},A{r},"
                       f"'Material Detail'!$E$2:$E${nmd},B{r})").number_format = _NUM
        mt.cell(r, 4, ", ".join(a["written"]))
        if not a["known"]:
            for c in range(1, 5):
                mt.cell(r, c).fill = _FLAG
    last = 5 + len(totals) - 1
    mt.cell(last + 2, 1, "Lines counted").font = _B
    mt.cell(last + 2, 3, f"=COUNTA('Material Detail'!$C$2:$C${nmd})")
    _widths(mt, [20, 7, 11, 60])
    mt.freeze_panes = "A5"

    ft["A1"] = f"{t.name}: fiber footage by fiber count"
    ft["A1"].font = _TITLE
    ft["A2"] = ("Footage is the figure written in each box. Totals are SUMIFS over "
                "\"Fiber Detail\".")
    _head(ft, ["Fiber count", "Total footage (ft)", "Calculations"], 4)
    counts = t.fiber_totals()
    for i, a in enumerate(counts):
        r = 5 + i
        ft.cell(r, 1, a["fiber_count"])
        ft.cell(r, 2, f"=SUMIFS('Fiber Detail'!$I$2:$I${nfd},'Fiber Detail'!$D$2:$D${nfd},A{r})").number_format = _NUM
        ft.cell(r, 3, f"=COUNTIFS('Fiber Detail'!$D$2:$D${nfd},A{r})")
    r = 5 + len(counts)
    ft.cell(r, 1, "All counted fiber").font = _B
    ft.cell(r, 2, f"=SUM(B5:B{max(r - 1, 5)})" if counts else 0).font = _B
    ft.cell(r, 3, f"=SUM(C5:C{max(r - 1, 5)})" if counts else 0).font = _B
    r += 2
    ft.cell(r, 1, "Breakdown by the label written in the box").font = _B
    r += 1
    _head(ft, ["Fiber count", "Label as written", "Footage (ft)", "Calculations"], r)
    r += 1
    for a in t.fiber_by_label():
        ft.cell(r, 1, a["fiber_count"])
        ft.cell(r, 2, a["label"])
        ft.cell(r, 3, f"=SUMIFS('Fiber Detail'!$I$2:$I${nfd},'Fiber Detail'!$D$2:$D${nfd},A{r},"
                       f"'Fiber Detail'!$C$2:$C${nfd},B{r})").number_format = _NUM
        ft.cell(r, 4, f"=COUNTIFS('Fiber Detail'!$D$2:$D${nfd},A{r},'Fiber Detail'!$C$2:$C${nfd},B{r})")
        r += 1
    cables = t.cable_totals()
    if cables:
        r += 1
        ft.cell(r, 1, "Cable IDs whose boxes state no fiber count (not included above)").font = _B
        r += 1
        _head(ft, ["Cable ID", "Footage (ft)", "Calculations", "Labels as written"], r)
        r += 1
        c0 = r
        for a in cables:
            ft.cell(r, 1, a["cable"])
            ft.cell(r, 2, f"=SUMIFS('Fiber Detail'!$I$2:$I${nfd},'Fiber Detail'!$E$2:$E${nfd},A{r})").number_format = _NUM
            ft.cell(r, 3, f"=COUNTIFS('Fiber Detail'!$E$2:$E${nfd},A{r})")
            ft.cell(r, 4, ", ".join(a["labels"]))
            r += 1
        ft.cell(r, 1, "Total, count not stated").font = _B
        ft.cell(r, 2, f"=SUM(B{c0}:B{r - 1})").font = _B
        ft.cell(r, 3, f"=SUM(C{c0}:C{r - 1})").font = _B
    _widths(ft, [26, 34, 13, 50])

    _head(sf, ["File", "Pages", "Comment boxes", "MATERIALS boxes", "Footage boxes", "Counted?"])
    from takeoff.drawings import is_footage_box, is_materials_box
    for f in t.files:
        sf.append([f.name, f.pages, len(f.comments),
                   sum(1 for c in f.comments if is_materials_box(c.text)),
                   sum(1 for c in f.comments if is_footage_box(c.text)),
                   "YES" if f.counted else f"NO: same comment boxes as {f.duplicate_of}"])
        if not f.counted:
            for c in range(1, 7):
                sf.cell(sf.max_row, c).fill = _GREY
    _widths(sf, [44, 7, 14, 16, 14, 60])

    nt["A1"] = f"{t.name}: notes and assumptions"
    nt["A1"].font = _TITLE
    r = 3
    for n in DRAWING_NOTES:
        nt.cell(r, 1, n).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    flagged = t.flagged()
    r += 1
    nt.cell(r, 1, f"Needs a look ({len(flagged)})").font = _B
    r += 1
    for f, p, line, why in flagged:
        nt.cell(r, 1, f"{f} p{p}: \"{line}\" — {why}")
        r += 1
    if t.checks:
        r += 1
        nt.cell(r, 1, "Box-total lines checked against the calculations in the same box:").font = _B
        r += 1
        for c in t.checks:
            nt.cell(r, 1, f"{c.file} p{c.page}: \"{c.text}\" vs calculations = {c.computed:,} ft — "
                          f"{'MATCHES' if c.matches else 'DIFFERS'}")
            r += 1
    nt.column_dimensions["A"].width = 130
    return _save(wb)


# --------------------------------------------------------------------------- #
#  invoices
# --------------------------------------------------------------------------- #
def invoice_workbook(s: InvoiceSet, title: str = "Invoices") -> bytes:
    """One sheet: every line item, the invoice totals, grand totals and the
    quantities by kind of work."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    vendors = sorted({d.vendor for d in s.docs if d.vendor})
    ws["A1"] = f"{title}: every line item" + (f" — {', '.join(vendors)}" if vendors else "")
    ws["A1"].font = _TITLE
    tot = s.totals()
    ws["A2"] = (f"{tot['pages']} invoice page(s) = {tot['unique_invoices']} invoice(s); "
                f"{tot['repeat_pages']} page(s) repeat an invoice already in the batch and are "
                "greyed out and NOT counted in the grand total.")
    r = 4
    ws.cell(r, 1, "LINE ITEMS").font = Font(bold=True, size=12)
    r += 1
    cols = ["File", "Page", "Invoice #", "Invoice date", "Project (as written)", "Location / section",
            "Line item (as written)", "Qty", "Unit", "Rate", "Line total (billed)", "Qty x Rate",
            "Billed - (Qty x Rate)", "Counted in grand total?", "Work item (grouped)", "Needs a look",
            "How it was read"]
    _head(ws, cols, r)
    hdr = r
    r += 1
    first = r
    for d in s.docs:
        for i in d.items:
            ws.cell(r, 1, i.file)
            ws.cell(r, 2, i.page)
            ws.cell(r, 3, i.invoice)
            if i.date:
                ws.cell(r, 4, i.date).number_format = "mm/dd/yyyy"
            ws.cell(r, 5, i.project)
            ws.cell(r, 6, i.section)
            ws.cell(r, 7, i.desc)
            ws.cell(r, 8, i.qty).number_format = _NUM
            ws.cell(r, 9, i.unit)
            ws.cell(r, 10, i.rate).number_format = _CUR
            ws.cell(r, 11, i.amount).number_format = _CUR
            ws.cell(r, 12, f"=ROUND(H{r}*J{r},2)").number_format = _CUR
            ws.cell(r, 13, f"=K{r}-L{r}").number_format = _CUR
            ws.cell(r, 14, "Yes" if d.counted else f"No - repeat of {d.duplicate_of[0]} p{d.duplicate_of[1]}")
            ws.cell(r, 15, i.work_item)
            ws.cell(r, 16, "; ".join(i.notes) if d.counted else "")
            ws.cell(r, 17, "; ".join(i.info))
            for c in range(1, 18):
                if not d.counted:
                    ws.cell(r, c).fill = _GREY
                elif i.notes and c in (11, 12, 13, 16):
                    ws.cell(r, c).fill = _FLAG
            r += 1
    last = max(r - 1, first)
    ws.auto_filter.ref = f"A{hdr}:Q{last}"

    def rng(col):
        return f"${col}${first}:${col}${last}"

    r += 1
    ws.cell(r, 1, "INVOICE TOTALS").font = Font(bold=True, size=12)
    r += 1
    _head(ws, ["File", "Page", "Invoice #", "Invoice date", "Project (as written)", "Billed to",
               "Line items", "", "", "", "Sum of line totals", "Total printed on invoice",
               "Printed - sum of lines", "Counted in grand total?"], r)
    r += 1
    inv_first = r
    for d in s.docs:
        ws.cell(r, 1, d.file)
        ws.cell(r, 2, d.page)
        ws.cell(r, 3, d.number)
        if d.date:
            ws.cell(r, 4, d.date).number_format = "mm/dd/yyyy"
        ws.cell(r, 5, d.project)
        ws.cell(r, 6, d.bill_to)
        ws.cell(r, 7, f"=COUNTIFS({rng('A')},A{r},{rng('B')},B{r})")
        ws.cell(r, 11, f"=SUMIFS({rng('K')},{rng('A')},A{r},{rng('B')},B{r})").number_format = _CUR
        ws.cell(r, 12, d.total if d.total is not None else None).number_format = _CUR
        ws.cell(r, 13, f"=IF(L{r}=\"\",\"no total printed\",L{r}-K{r})").number_format = _CUR
        ws.cell(r, 14, "Yes" if d.counted else f"No - repeat of {d.duplicate_of[0]} p{d.duplicate_of[1]}")
        if not d.counted:
            for c in range(1, 15):
                ws.cell(r, c).fill = _GREY
        r += 1
    inv_last = max(r - 1, inv_first)

    def irng(col):
        return f"${col}${inv_first}:${col}${inv_last}"

    r += 1
    ws.cell(r, 1, "GRAND TOTALS").font = Font(bold=True, size=12)
    r += 1
    ws.cell(r, 5, "Unique invoices (repeat copies excluded)").font = _B
    ws.cell(r, 7, f"=COUNTIFS({irng('N')},\"Yes\")").font = _B
    ws.cell(r, 11, f"=SUMIFS({irng('K')},{irng('N')},\"Yes\")").number_format = _CUR
    ws.cell(r, 12, f"=SUMIFS({irng('L')},{irng('N')},\"Yes\")").number_format = _CUR
    ws.cell(r, 11).font = ws.cell(r, 12).font = _B
    ws.cell(r, 16, "Column G here = number of invoices")
    r += 1
    ws.cell(r, 5, "Every page as printed (repeat copies included)")
    ws.cell(r, 7, f"=COUNTA({irng('C')})")
    ws.cell(r, 11, f"=SUM({irng('K')})").number_format = _CUR
    ws.cell(r, 12, f"=SUM({irng('L')})").number_format = _CUR
    r += 2

    ws.cell(r, 1, "QUANTITIES AND TOTALS BY WORK ITEM (unique invoices only)").font = Font(bold=True, size=12)
    r += 1
    _head(ws, ["", "", "", "", "Work item", "", "Line items", "Total qty", "Unit", "", "Total billed",
               "", "", "", "", "Rates seen"], r)
    r += 1
    k_first = r
    for w in s.by_work_item():
        ws.cell(r, 5, w["work_item"])
        ws.cell(r, 7, f"=COUNTIFS({rng('O')},E{r},{rng('N')},\"Yes\")")
        ws.cell(r, 8, f"=SUMIFS({rng('H')},{rng('O')},E{r},{rng('N')},\"Yes\")").number_format = _NUM
        ws.cell(r, 9, ", ".join(w["units"]))
        ws.cell(r, 11, f"=SUMIFS({rng('K')},{rng('O')},E{r},{rng('N')},\"Yes\")").number_format = _CUR
        ws.cell(r, 16, ", ".join(f"${v:,.2f}" for v in w["rates"]))
        r += 1
    ws.cell(r, 5, "Total (matches the unique-invoice line total)").font = _B
    ws.cell(r, 7, f"=SUM(G{k_first}:G{max(r - 1, k_first)})").font = _B
    ws.cell(r, 11, f"=SUM(K{k_first}:K{max(r - 1, k_first)})").number_format = _CUR
    ws.cell(r, 11).font = _B
    r += 2

    ws.cell(r, 1, "NOTES").font = Font(bold=True, size=12)
    r += 1
    notes = [
        "Read from the PDF text (not by eye). One invoice per page.",
        "A leading \"-\" before a quantity (e.g. \"-766ft-Directional Bore\") is a bullet, not a minus sign.",
        "Grey rows repeat an invoice already in the batch (same number, same lines, same total) and "
        "are counted once. Yellow cells need a look (see the \"Needs a look\" column).",
    ]
    notes += [f"{where}: \"{what}\" — {why}" if where else f"{what} — {why}"
              for where, what, why in s.flagged()]
    for n in notes:
        ws.cell(r, 1, n)
        r += 1
    _widths(ws, [24, 6, 10, 12, 34, 30, 46, 10, 6, 10, 15, 14, 14, 22, 36, 60, 40])
    ws.freeze_panes = ws.cell(hdr + 1, 4)
    return _save(wb)
