"""Extract as-built quantities from a PDF via pdfplumber table extraction.

Phase 2 handles PDFs that contain a real (ruled or text-aligned) tally/summary
table. Every extracted row is tagged ``confidence="pdf"`` and is never treated as
final — the UI routes them through an editable confirmation grid. Image-only pages
are flagged for OCR (Phase 4), not silently trusted. See spec §5b / SDD §5.3.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from recon.ingest.normalize import normalize, normalize_uom
from recon.models import AsBuiltLine

_DESC_TOKENS = ("description", "desc", "unit", "item", "work")
_QTY_TOKENS = ("qty", "quantity", "count", "total", "built")
_UOM_TOKENS = ("uom", "unit of measure", "u/m", "measure")
_SEG_TOKENS = ("segment", "seg", "route", "span", "sheet", "area")
_SKIP_DESCS = ("total", "subtotal", "sum", "grand total")


@dataclass
class ExtractionReport:
    """What happened during a PDF extraction — surfaced to the reviewer so nothing
    is trusted blindly."""
    source_file: str
    n_pages: int = 0
    n_tables: int = 0
    n_rows: int = 0
    image_only_pages: list[int] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)   # scanned pages read by OCR
    ocr_available: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def needs_ocr(self) -> bool:
        """True if a scanned page still hasn't been read — OCR was unavailable or
        found nothing legible."""
        return bool(set(self.image_only_pages) - set(self.ocr_pages))


def _to_float(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _match_col(header: list[str], tokens: tuple[str, ...]) -> int | None:
    """Return the column index in a positional table header, or None."""
    low = [h.lower().strip() for h in header]
    for i, h in enumerate(low):
        if any(h == t for t in tokens):
            return i
    for i, h in enumerate(low):
        if any(t in h for t in tokens):
            return i
    return None


def _find_header_idx(grid: list[list[str]]) -> int | None:
    for i, row in enumerate(grid[:15]):
        cells = [c.lower() for c in row]
        has_desc = any(any(t in c for t in _DESC_TOKENS) for c in cells)
        has_qty = any(any(t in c for t in _QTY_TOKENS) for c in cells)
        if has_desc and has_qty:
            return i
    return None


def _parse_table(table, source: str, pno: int, tno: int,
                 confidence: str = "pdf") -> list[AsBuiltLine]:
    """Parse one extracted table into AsBuiltLines (or [] if it has no header)."""
    grid = [[(c or "").strip().replace("\n", " ") for c in row] for row in table]
    hidx = _find_header_idx(grid)
    if hidx is None:
        return []
    header = grid[hidx]
    desc_i = _match_col(header, _DESC_TOKENS)
    qty_i = _match_col(header, _QTY_TOKENS)
    if desc_i is None or qty_i is None:
        return []
    uom_i = _match_col(header, _UOM_TOKENS)
    seg_i = _match_col(header, _SEG_TOKENS)

    lines: list[AsBuiltLine] = []
    for r in range(hidx + 1, len(grid)):
        row = grid[r]
        desc = row[desc_i] if desc_i < len(row) else ""
        qty = _to_float(row[qty_i]) if qty_i < len(row) else None
        if not desc or qty is None:
            continue
        if desc.lower() in _SKIP_DESCS:
            continue
        uom = normalize_uom(row[uom_i]) if uom_i is not None and uom_i < len(row) else None
        seg = row[seg_i] if seg_i is not None and seg_i < len(row) else None
        lines.append(AsBuiltLine(
            raw_desc=desc, qty=qty, uom=uom, segment=(seg or None),
            source_file=source, source_ref=f"page {pno} table {tno} row {r + 1}",
            confidence=confidence,
        ))
    return lines


def _group_pdf(lines: list[AsBuiltLine], source: str) -> list[AsBuiltLine]:
    """Group by normalized description, summing quantity — same as the tally flow.

    A group keeps the least-trusted confidence of its contributors, so a unit that
    was partly read by OCR is never presented as a clean PDF extraction."""
    buckets: dict[str, dict] = {}
    for ln in lines:
        key = normalize(ln.raw_desc)
        b = buckets.setdefault(key, {
            "raw_desc": ln.raw_desc, "qty": 0.0, "uom": ln.uom,
            "segments": set(), "refs": [], "confidence": ln.confidence,
        })
        if ln.confidence == "ocr":
            b["confidence"] = "ocr"
        b["qty"] += ln.qty
        if ln.uom and b["uom"] is None:
            b["uom"] = ln.uom
        if ln.segment:
            b["segments"].add(ln.segment)
        if ln.source_ref:
            b["refs"].append(ln.source_ref)

    grouped: list[AsBuiltLine] = []
    for b in buckets.values():
        segs = sorted(b["segments"])
        grouped.append(AsBuiltLine(
            raw_desc=b["raw_desc"], qty=b["qty"], uom=b["uom"],
            segment=", ".join(segs) if segs else None,
            source_file=source,
            source_ref="; ".join(b["refs"]) if b["refs"] else None,
            confidence=b["confidence"],
        ))
    return grouped


def extract_asbuilt_pdf(path: str | Path, *,
                        ocr: bool = True) -> tuple[list[AsBuiltLine], ExtractionReport]:
    """Extract as-built quantities from a PDF.

    Returns grouped AsBuiltLines plus an ExtractionReport. Pages with a text layer
    are parsed directly (confidence='pdf'). A scanned page — no text, no table — is
    OCR'd when Tesseract is available and its rows are tagged confidence='ocr' so
    the UI forces a human to confirm them; when OCR is unavailable the page is
    reported with an install hint rather than being silently dropped.
    """
    from recon.ingest.ocr import ocr_status, page_to_grid

    p = Path(path)
    report = ExtractionReport(source_file=p.name)
    ocr_ok, ocr_msg = ocr_status()
    report.ocr_available = ocr_ok
    raw_lines: list[AsBuiltLine] = []

    with pdfplumber.open(p) as pdf:
        report.n_pages = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or []
            text = (page.extract_text() or "").strip()

            if not tables and not text:
                report.image_only_pages.append(pno)
                if ocr and ocr_ok:
                    grid = page_to_grid(page)
                    rows = _parse_table(grid, p.name, pno, 1, confidence="ocr") if grid else []
                    if rows:
                        report.n_tables += 1
                        report.ocr_pages.append(pno)
                        raw_lines.extend(rows)
                    else:
                        report.warnings.append(
                            f"Page {pno}: scanned page — OCR could not make out an "
                            "as-built table; enter those quantities manually.")
                continue

            parsed_any = False
            for tno, table in enumerate(tables, start=1):
                rows = _parse_table(table, p.name, pno, tno)
                if rows:
                    report.n_tables += 1
                    raw_lines.extend(rows)
                    parsed_any = True
            if not parsed_any:
                report.warnings.append(
                    f"Page {pno}: no as-built table detected — complete it in the "
                    "editable grid.")

    grouped = _group_pdf(raw_lines, p.name)
    report.n_rows = len(grouped)

    if report.ocr_pages:
        report.warnings.append(
            f"Pages {report.ocr_pages} were read by OCR — every number needs "
            "checking against the source before it counts.")
    unread = sorted(set(report.image_only_pages) - set(report.ocr_pages))
    if unread:
        report.warnings.append(
            f"Pages {unread} look scanned (no text layer). "
            + (ocr_msg if not ocr_ok else "OCR found nothing legible.")
            + " Enter those quantities manually.")
    return grouped, report
