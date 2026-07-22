"""Parse contractor invoices (xlsx/csv, and zip archives thereof) with dedup.

Invoices are usually cumulative pay applications. Dedupe by invoice id to prevent
double counting on re-upload. PDF + per-contractor templates land in later Phase 2
sprints. See spec §5c / SDD §5.4.
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import pandas as pd

from recon.ingest.invoice_pdf import parse_invoice_pdf
from recon.ingest.normalize import normalize_uom
from recon.models import InvoiceLine, TemplateProfile

_TABULAR_EXTS = (".xlsx", ".xls", ".xlsm", ".csv", ".tsv")
# Everything we can parse from a zip: tabular files plus PDFs.
_SUPPORTED_EXTS = _TABULAR_EXTS + (".pdf",)

_DESC_TOKENS = ("description", "desc", "unit", "item", "work")
_QTY_TOKENS = ("qty", "quantity", "count", "units")
_PRICE_TOKENS = ("unit price", "price", "rate", "unit cost")
_AMT_TOKENS = ("amount", "extended", "total", "ext price", "line total")
_INV_TOKENS = ("invoice", "invoice #", "invoice no", "inv", "invoice id")
_PERIOD_TOKENS = ("period", "date", "billing period", "pay app", "cycle")
_UOM_TOKENS = ("uom", "unit of measure", "u/m", "measure")


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(path, dtype=object)
    else:
        sep = "\t" if path.suffix.lower() == ".tsv" else ","
        df = pd.read_csv(path, dtype=object, sep=sep)
    df.columns = [str(c).strip() for c in df.columns]
    return df


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
    s = str(val).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_invoice_file(
    path: str | Path,
    *,
    default_invoice_id: str | None = None,
    is_cumulative: bool = True,
) -> list[InvoiceLine]:
    """Parse a single xlsx/csv invoice file into InvoiceLines."""
    p = Path(path)
    df = _read_table(p)
    if df.empty:
        return []
    cols = df.columns.tolist()

    desc_col = _match_col(cols, _DESC_TOKENS)
    qty_col = _match_col(cols, _QTY_TOKENS)
    price_col = _match_col(cols, _PRICE_TOKENS)
    amt_col = _match_col(cols, _AMT_TOKENS)
    inv_col = _match_col(cols, _INV_TOKENS)
    period_col = _match_col(cols, _PERIOD_TOKENS)
    uom_col = _match_col(cols, _UOM_TOKENS)

    if desc_col is None or qty_col is None:
        raise ValueError(
            f"Could not locate description/qty columns in invoice {p.name}. "
            f"Header: {cols}"
        )

    fallback_id = default_invoice_id or p.stem
    lines: list[InvoiceLine] = []
    for idx, row in df.iterrows():
        desc = row.get(desc_col)
        if desc is None or (isinstance(desc, float) and pd.isna(desc)):
            continue
        desc = str(desc).strip()
        if not desc or desc.lower() in ("total", "subtotal", "sum"):
            continue
        qty = _to_float(row.get(qty_col))
        if qty is None:
            continue
        price = _to_float(row.get(price_col)) if price_col else None
        amount = _to_float(row.get(amt_col)) if amt_col else None
        # Derive missing price/amount from the other where possible.
        if price is None and amount is not None and qty:
            price = amount / qty
        if amount is None and price is not None:
            amount = qty * price
        inv_id = str(row.get(inv_col)).strip() if inv_col and pd.notna(row.get(inv_col)) else fallback_id
        period = str(row.get(period_col)).strip() if period_col and pd.notna(row.get(period_col)) else None
        uom = normalize_uom(row.get(uom_col)) if uom_col else None
        # UoM is parsed for future use; kept off InvoiceLine to match spec dataclass.
        _ = uom
        lines.append(InvoiceLine(
            invoice_id=inv_id,
            raw_desc=desc,
            qty=qty,
            unit_price=price if price is not None else 0.0,
            amount=amount if amount is not None else 0.0,
            period=period,
            is_cumulative=is_cumulative,
            source_file=p.name,
            line_ref=f"line {int(idx) + 1}",
        ))
    return lines


def _extract_zip_members(zip_path: Path) -> list[Path]:
    """Extract the tabular members of a zip to a temp dir and return their paths.

    Members are written under a per-index subdirectory so the original filenames
    are preserved (for source_file / invoice-id display) without name collisions,
    and the flattened target path prevents zip-slip escaping the temp dir.
    """
    out: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        base = Path(tempfile.mkdtemp(prefix="splice_zip_"))
        for i, member in enumerate(zf.namelist()):
            if member.endswith("/"):
                continue
            name = Path(member).name          # flatten — ignore internal dirs
            if not name or Path(name).suffix.lower() not in _SUPPORTED_EXTS:
                continue
            sub = base / str(i)
            sub.mkdir(parents=True, exist_ok=True)
            target = sub / name
            with zf.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())
            out.append(target)
    return out


def _parse_one(path: Path, *, is_cumulative: bool,
               profile: TemplateProfile | None = None) -> list[InvoiceLine]:
    """Dispatch a single file to the right parser by extension. A template profile,
    if given, only applies to PDF files (it describes a PDF invoice layout)."""
    if path.suffix.lower() == ".pdf":
        return parse_invoice_pdf(path, profile=profile, is_cumulative=is_cumulative)
    return parse_invoice_file(path, is_cumulative=is_cumulative)


def parse_invoices(
    paths: list[str | Path],
    *,
    is_cumulative: bool = True,
    profile: TemplateProfile | None = None,
) -> list[InvoiceLine]:
    """Parse invoice files (xlsx/csv/pdf) and/or zip archives, and dedupe.

    Dedup key is (invoice_id, raw_desc, qty, unit_price) so distinct lines survive
    but exact duplicates from a re-upload collapse. A zip is expanded to its
    supported members; a member that doesn't parse as an invoice is skipped (a zip
    may hold reference files), whereas a directly supplied file that fails still
    raises so the user gets feedback. ``profile`` applies a saved per-contractor
    layout to PDF invoices.
    """
    seen: set[tuple] = set()
    out: list[InvoiceLine] = []

    def _add(lines: list[InvoiceLine]) -> None:
        for ln in lines:
            key = (ln.invoice_id, ln.raw_desc, ln.qty, ln.unit_price)
            if key in seen:
                continue
            seen.add(key)
            out.append(ln)

    for path in paths:
        p = Path(path)
        if p.suffix.lower() == ".zip":
            for member in _extract_zip_members(p):
                try:
                    _add(_parse_one(member, is_cumulative=is_cumulative, profile=profile))
                except ValueError:
                    continue        # non-invoice / unreadable member — skip
        else:
            _add(_parse_one(p, is_cumulative=is_cumulative, profile=profile))
    return out
