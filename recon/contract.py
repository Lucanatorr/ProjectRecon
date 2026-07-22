"""Load and represent the authoritative pricing (bid schedule + change orders).

The bid schedule is the anchor everything reconciles against. Without it the tool
degrades to quantity-only mode (spec §2). See SDD §5.5.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from recon.ingest.normalize import normalize_uom
from recon.models import ContractItem, UoM

# Column header tokens we accept for each logical field (lowercased, substring match).
_COL_ALIASES = {
    "code": ["code", "item", "bid item", "item no", "item #", "ref"],
    "description": ["description", "desc", "unit", "work item", "item description"],
    "uom": ["uom", "unit of measure", "u/m", "units", "measure"],
    "unit_price": ["unit price", "price", "rate", "unit cost", "cost"],
    "est_qty": ["est qty", "estimated qty", "estimated quantity", "est quantity",
                "qty", "quantity", "bid qty", "est. qty"],
}


def _read_table(path: str | Path) -> pd.DataFrame:
    """Read xlsx or csv into a DataFrame with string column headers."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(p, dtype=object)
    elif p.suffix.lower() in (".csv", ".tsv"):
        sep = "\t" if p.suffix.lower() == ".tsv" else ","
        df = pd.read_csv(p, dtype=object, sep=sep)
    else:
        raise ValueError(f"Unsupported bid schedule format: {p.suffix}")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    """Map logical field -> actual column name using alias substring matching."""
    resolved: dict[str, str] = {}
    lowered = {col: col.lower().strip() for col in df.columns}
    for field_name, aliases in _COL_ALIASES.items():
        for col, low in lowered.items():
            if any(low == a or low.startswith(a) for a in aliases):
                resolved[field_name] = col
                break
        else:
            # fall back to a looser substring match
            for col, low in lowered.items():
                if any(a in low for a in aliases):
                    resolved[field_name] = col
                    break
    return resolved


def _to_float(val) -> float | None:
    """Parse a currency/number cell to float, tolerating $, commas, blanks."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s:
        return None
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def load_bid_schedule(path: str | Path) -> list[ContractItem]:
    """Parse a bid schedule (xlsx/csv) into ContractItems.

    Raises ValueError if required columns (code, description, unit_price) can't be
    located, so a malformed schedule fails loudly rather than silently mis-parsing.
    """
    df = _read_table(path)
    cols = _resolve_columns(df)

    missing = [f for f in ("code", "description", "unit_price") if f not in cols]
    if missing:
        raise ValueError(
            f"Bid schedule missing required column(s): {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    items: list[ContractItem] = []
    for _, raw in df.iterrows():
        code = str(raw[cols["code"]]).strip() if pd.notna(raw[cols["code"]]) else ""
        desc = str(raw[cols["description"]]).strip() if pd.notna(raw[cols["description"]]) else ""
        price = _to_float(raw[cols["unit_price"]])
        # Skip blank / subtotal / header rows: need a code and a numeric price.
        if not code or code.lower() in ("nan", "code", "item"):
            continue
        if price is None:
            continue
        uom = normalize_uom(raw[cols["uom"]]) if "uom" in cols else None
        est = _to_float(raw[cols["est_qty"]]) if "est_qty" in cols else None
        items.append(ContractItem(
            code=code,
            description=desc,
            uom=uom or UoM.EA,
            unit_price=price,
            est_qty=est if est is not None else 0.0,
        ))
    if not items:
        raise ValueError("Bid schedule parsed to zero items — check the file format.")
    return items


def apply_change_orders(items: list[ContractItem], co_path: str | Path) -> list[ContractItem]:
    """Append change-order items (flagged is_change_order=True) to a contract.

    CO items with a code already present are treated as price revisions and
    replace the base item; new codes extend the contract.
    """
    co_items = load_bid_schedule(co_path)
    by_code = {ci.code: ci for ci in items}
    for co in co_items:
        co.is_change_order = True
        by_code[co.code] = co  # revision or addition
    return list(by_code.values())


def index_by_code(items: list[ContractItem]) -> dict[str, ContractItem]:
    """Convenience: dict keyed by canonical code (last wins on dupes)."""
    return {ci.code: ci for ci in items}
