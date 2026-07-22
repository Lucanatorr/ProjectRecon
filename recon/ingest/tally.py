"""Parse structured tally sheets (xlsx/csv) — the preferred as-built source.

Real-world tally sheets have interspersed header/subtotal rows and merged cells.
Strategy: detect the header row by expected tokens, drop non-numeric-qty rows,
group by description, sum quantity. See spec §5a / SDD §5.2.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from recon.ingest.normalize import normalize, normalize_uom
from recon.models import AsBuiltLine

_DESC_TOKENS = ("description", "desc", "unit", "item", "work")
_QTY_TOKENS = ("qty", "quantity", "count", "total", "built")
_UOM_TOKENS = ("uom", "unit of measure", "u/m", "measure")
_SEG_TOKENS = ("segment", "seg", "route", "span", "sheet", "area")


def _find_header_row(raw: pd.DataFrame) -> int:
    """Scan the first ~15 rows for one containing both a desc-like and qty-like
    token. Returns the 0-based row index, or 0 if none found."""
    limit = min(15, len(raw))
    for i in range(limit):
        cells = [str(c).lower().strip() for c in raw.iloc[i].tolist()]
        has_desc = any(any(t in c for t in _DESC_TOKENS) for c in cells)
        has_qty = any(any(t in c for t in _QTY_TOKENS) for c in cells)
        if has_desc and has_qty:
            return i
    return 0


def _match_col(columns: list[str], tokens: tuple[str, ...]) -> str | None:
    low = {c: str(c).lower().strip() for c in columns}
    for c, l in low.items():
        if any(l == t for t in tokens):
            return c
    for c, l in low.items():
        if any(t in l for t in tokens):
            return c
    return None


def _to_float(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_tally(path: str | Path, *, group: bool = True) -> list[AsBuiltLine]:
    """Parse a tally sheet into AsBuiltLines.

    With group=True (default), rows are grouped by normalized description and
    quantities summed — the coordinator's manual "step 1a". Segment is preserved
    when a single segment feeds a group; mixed groups record None.
    """
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        raw = pd.read_excel(p, header=None, dtype=object)
    else:
        sep = "\t" if p.suffix.lower() == ".tsv" else ","
        raw = pd.read_csv(p, header=None, dtype=object, sep=sep)

    if raw.empty:
        return []

    hdr = _find_header_row(raw)
    header = [str(c).strip() for c in raw.iloc[hdr].tolist()]
    body = raw.iloc[hdr + 1:].copy()
    body.columns = header

    desc_col = _match_col(header, _DESC_TOKENS)
    qty_col = _match_col(header, _QTY_TOKENS)
    uom_col = _match_col(header, _UOM_TOKENS)
    seg_col = _match_col(header, _SEG_TOKENS)

    if desc_col is None or qty_col is None:
        raise ValueError(
            f"Could not locate description/qty columns in tally sheet. Header: {header}"
        )

    lines: list[AsBuiltLine] = []
    for idx, row in body.iterrows():
        desc = row.get(desc_col)
        qty = _to_float(row.get(qty_col))
        # Drop rows without a real description or a numeric qty (subtotals, blanks).
        if desc is None or (isinstance(desc, float) and pd.isna(desc)):
            continue
        desc = str(desc).strip()
        if not desc or qty is None:
            continue
        # Skip obvious subtotal/total rows.
        if desc.lower() in ("total", "subtotal", "sum", "grand total"):
            continue
        uom = normalize_uom(row.get(uom_col)) if uom_col else None
        seg = None
        if seg_col is not None:
            sv = row.get(seg_col)
            seg = str(sv).strip() if sv is not None and str(sv).strip() else None
        lines.append(AsBuiltLine(
            raw_desc=desc, qty=qty, uom=uom, segment=seg,
            source_file=p.name, source_ref=f"row {int(idx) + 1}",
            confidence="sum",
        ))

    if not group:
        return lines
    return _group_by_desc(lines, source=p.name)


def _group_by_desc(lines: list[AsBuiltLine], source: str) -> list[AsBuiltLine]:
    """Group by normalized description, summing quantity and collecting refs."""
    buckets: dict[str, dict] = {}
    for ln in lines:
        key = normalize(ln.raw_desc)
        b = buckets.setdefault(key, {
            "raw_desc": ln.raw_desc, "qty": 0.0, "uom": ln.uom,
            "segments": set(), "refs": [],
        })
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
            raw_desc=b["raw_desc"],
            qty=b["qty"],
            uom=b["uom"],
            segment=", ".join(segs) if segs else None,
            source_file=source,
            source_ref="; ".join(b["refs"]) if b["refs"] else None,
            confidence="sum",
        ))
    return grouped
