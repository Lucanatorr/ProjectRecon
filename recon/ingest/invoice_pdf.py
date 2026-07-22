"""Parse contractor invoices from PDF via pdfplumber table extraction.

Auto-detects the desc / qty / price / amount columns in a ruled or text-aligned
invoice table. Per-contractor template profiles (for invoices whose columns don't
auto-detect) arrive in the next sprint. See spec §5c / SDD §5.4.
"""
from __future__ import annotations

from pathlib import Path

import pdfplumber

from recon.models import TEMPLATE_FIELDS, InvoiceLine, TemplateProfile

# Column header tokens per logical field. Specific fields (amount/price/qty) are
# claimed before the generic description field so "Unit Price" is never mistaken
# for the description column.
_AMOUNT_TOKENS = ("amount", "extended", "line total", "ext price", "total")
_PRICE_TOKENS = ("unit price", "price", "rate", "unit cost")
_QTY_TOKENS = ("qty", "quantity", "count", "units")
_INV_TOKENS = ("invoice", "invoice #", "invoice no", "inv")
_PERIOD_TOKENS = ("period", "date", "billing period", "pay app", "cycle")
_DESC_TOKENS = ("description", "desc", "item", "work", "unit")
_SKIP_DESCS = ("total", "subtotal", "sum", "grand total")


def _to_float(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace("$", "").replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_header_idx(grid: list[list[str]]) -> int | None:
    for i, row in enumerate(grid[:15]):
        cells = [c.lower() for c in row]
        has_desc = any(any(t in c for t in _DESC_TOKENS) for c in cells)
        has_qty = any(any(t in c for t in _QTY_TOKENS) for c in cells)
        if has_desc and has_qty:
            return i
    return None


def _map_columns(header: list[str]) -> dict[str, int | None]:
    """Assign each logical field a column index, exact match first, then substring,
    claiming specific fields before the generic description so columns aren't stolen."""
    low = [h.lower().strip() for h in header]
    used: set[int] = set()

    def pick(tokens: tuple[str, ...]) -> int | None:
        for i, h in enumerate(low):
            if i not in used and any(h == t for t in tokens):
                used.add(i)
                return i
        for i, h in enumerate(low):
            if i not in used and any(t in h for t in tokens):
                used.add(i)
                return i
        return None

    return {
        "amount": pick(_AMOUNT_TOKENS),
        "price": pick(_PRICE_TOKENS),
        "qty": pick(_QTY_TOKENS),
        "invoice": pick(_INV_TOKENS),
        "period": pick(_PERIOD_TOKENS),
        "desc": pick(_DESC_TOKENS),
    }


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def _normalize_grid(table) -> list[list[str]]:
    return [[(c or "").strip().replace("\n", " ") for c in row] for row in table]


def _rows_from(grid: list[list[str]], cols: dict[str, int | None], header_row: int,
               source: str, stem: str, pno: int, tno: int,
               default_invoice_id: str | None, is_cumulative: bool) -> list[InvoiceLine]:
    """Build InvoiceLines from a grid given a column map and header row index."""
    fallback_id = default_invoice_id or stem
    lines: list[InvoiceLine] = []
    for r in range(header_row + 1, len(grid)):
        row = grid[r]
        desc = _cell(row, cols.get("desc")).strip()
        if not desc or desc.lower() in _SKIP_DESCS:
            continue
        qty = _to_float(_cell(row, cols.get("qty")))
        if qty is None:
            continue
        price = _to_float(_cell(row, cols.get("price")))
        amount = _to_float(_cell(row, cols.get("amount")))
        if price is None and amount is not None and qty:
            price = amount / qty
        if amount is None and price is not None:
            amount = qty * price
        inv_cell = _cell(row, cols.get("invoice")).strip()
        period = _cell(row, cols.get("period")).strip() or None
        lines.append(InvoiceLine(
            invoice_id=inv_cell or fallback_id,
            raw_desc=desc,
            qty=qty,
            unit_price=price if price is not None else 0.0,
            amount=amount if amount is not None else 0.0,
            period=period,
            is_cumulative=is_cumulative,
            source_file=source,
            line_ref=f"page {pno} table {tno} row {r + 1}",
        ))
    return lines


def _parse_table_auto(grid: list[list[str]], source: str, stem: str, pno: int, tno: int,
                      default_invoice_id: str | None, is_cumulative: bool) -> list[InvoiceLine]:
    hidx = _find_header_idx(grid)
    if hidx is None:
        return []
    cols = _map_columns(grid[hidx])
    if cols["desc"] is None or cols["qty"] is None:
        return []
    return _rows_from(grid, cols, hidx, source, stem, pno, tno,
                      default_invoice_id, is_cumulative)


def _parse_table_profile(grid: list[list[str]], profile: TemplateProfile, source: str,
                         stem: str, pno: int, tno: int, default_invoice_id: str | None,
                         is_cumulative: bool) -> list[InvoiceLine]:
    if not profile.is_valid():
        return []
    cols = {f: profile.columns.get(f) for f in TEMPLATE_FIELDS}
    return _rows_from(grid, cols, profile.header_row, source, stem, pno, tno,
                      default_invoice_id, is_cumulative)


def _open(path: Path):
    try:
        return pdfplumber.open(path)
    except Exception as e:                       # not a readable PDF
        raise ValueError(f"Could not open PDF {path.name}: {e}") from e


def parse_invoice_pdf(
    path: str | Path,
    *,
    profile: TemplateProfile | None = None,
    default_invoice_id: str | None = None,
    is_cumulative: bool = True,
) -> list[InvoiceLine]:
    """Parse a PDF invoice into InvoiceLines.

    With ``profile`` given (a saved per-contractor layout), the profile's column map
    and header row are applied instead of auto-detection — for invoices whose columns
    don't auto-detect. Returns [] for a PDF with no usable table, so it can be skipped
    inside a zip. Raises ValueError only when the file can't be opened as a PDF.
    """
    p = Path(path)
    lines: list[InvoiceLine] = []
    with _open(p) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            for tno, table in enumerate(page.extract_tables() or [], start=1):
                grid = _normalize_grid(table)
                if profile is not None:
                    lines.extend(_parse_table_profile(
                        grid, profile, p.name, p.stem, pno, tno,
                        default_invoice_id, is_cumulative))
                else:
                    lines.extend(_parse_table_auto(
                        grid, p.name, p.stem, pno, tno, default_invoice_id, is_cumulative))
    return lines


def extract_tables(path: str | Path) -> list[list[list[str]]]:
    """Return the normalized string grids of every table in the PDF, so the UI can
    display columns for manual mapping."""
    p = Path(path)
    grids: list[list[list[str]]] = []
    with _open(p) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                grids.append(_normalize_grid(table))
    return grids


def suggest_mapping(grid: list[list[str]]) -> tuple[int, dict[str, int | None]]:
    """Best-effort (header_row, column_map) for pre-filling the mapping UI. Falls
    back to header row 0 when no header tokens are found."""
    hidx = _find_header_idx(grid)
    if hidx is None:
        return 0, {f: None for f in TEMPLATE_FIELDS}
    return hidx, _map_columns(grid[hidx])
