"""Core domain entities.

These dataclasses are the shared vocabulary across ingest, crosswalk, reconcile,
report, and persistence. They mirror the technical spec §3 and SDD §6.1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class UoM(str, Enum):
    """Unit of measure. Inherits from str so values serialize/compare cleanly."""
    EA = "EA"
    FT = "FT"
    C_FT = "100FT"
    LS = "LS"

    @classmethod
    def from_str(cls, raw: str | None) -> "UoM | None":
        """Best-effort parse; returns None for unknown/blank input."""
        if raw is None:
            return None
        key = str(raw).strip().upper().replace(" ", "")
        aliases = {
            "EA": cls.EA, "EACH": cls.EA, "E": cls.EA,
            "FT": cls.FT, "F": cls.FT, "LF": cls.FT, "FOOT": cls.FT, "FEET": cls.FT,
            "100FT": cls.C_FT, "C_FT": cls.C_FT, "CFT": cls.C_FT, "MFT": cls.C_FT,
            "LS": cls.LS, "LUMPSUM": cls.LS, "LUMP": cls.LS,
        }
        return aliases.get(key)


class Severity(str, Enum):
    """Flag severity, ordered least → most serious for max() comparisons."""
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"ok": 0, "info": 1, "warning": 2, "critical": 3}[self.value]


@dataclass
class Flag:
    """A single reconciliation finding attached to a ReconRow."""
    rule: str            # stable rule id, e.g. "qty_over"
    severity: Severity
    message: str         # human-readable, rendered in UI and report


@dataclass
class ContractItem:
    code: str                 # canonical unit code, e.g. "3.2"
    description: str
    uom: UoM
    unit_price: float         # authoritative price
    est_qty: float            # estimated / bid quantity
    is_change_order: bool = False
    effective_date: str | None = None


@dataclass
class AsBuiltLine:
    raw_desc: str             # as it appeared in tally / PDF
    qty: float
    uom: UoM | None = None
    segment: str | None = None
    code: str | None = None   # filled after crosswalk
    source_file: str | None = None
    source_ref: str | None = None    # row / page reference for traceability
    confidence: str = "sum"          # sum | pdf | ocr


@dataclass
class InvoiceLine:
    invoice_id: str
    raw_desc: str
    qty: float                # this line's quantity
    unit_price: float
    amount: float
    period: str | None = None       # billing period / date
    is_cumulative: bool = True
    code: str | None = None         # filled after crosswalk
    source_file: str | None = None
    line_ref: str | None = None


# Logical fields a template profile can map. desc + qty are required; the rest
# are optional and derived when absent.
TEMPLATE_FIELDS = ("desc", "qty", "price", "amount", "invoice", "period")


@dataclass
class TemplateProfile:
    """A per-contractor PDF invoice layout: which table column holds each logical
    field, and where the header row sits. Saved once, reused for that contractor's
    future invoices when their columns don't auto-detect. See spec §5c / SDD §5.4."""
    contractor: str
    columns: dict[str, int]         # logical field -> 0-based column index
    header_row: int = 0             # row index of the header within the table
    table_index: int = 0            # which extracted table to use (reserved)

    def is_valid(self) -> bool:
        """A profile must at least locate the description and quantity columns."""
        return self.columns.get("desc") is not None and self.columns.get("qty") is not None


@dataclass
class ReconRow:
    """One reconciled unit: built vs billed with derived money fields."""
    code: str | None
    description: str
    uom: UoM
    built_qty: float
    billed_qty: float
    contract_price: float | None
    billed_price: float
    est_qty: float | None
    flags: list[Flag] = field(default_factory=list)
    is_change_order: bool = False       # authorized via a change order
    # Contributing source-line references for drill-down / traceability.
    asbuilt_refs: list[str] = field(default_factory=list)
    invoice_refs: list[str] = field(default_factory=list)

    # --- derived quantity / price deltas ---
    @property
    def qty_delta(self) -> float:
        return self.billed_qty - self.built_qty

    @property
    def price_delta(self) -> float:
        if self.contract_price is None:
            return 0.0
        return self.billed_price - self.contract_price

    # --- derived money ---
    @property
    def billed_amount(self) -> float:
        return self.billed_qty * self.billed_price

    @property
    def expected_amount(self) -> float:
        """Documented (built) work priced at the contract rate — what they
        *should* be paid. None contract price means we can't value it."""
        if self.contract_price is None:
            return 0.0
        return self.built_qty * self.contract_price

    @property
    def amount_variance(self) -> float:
        """Dollar exposure for this unit. Sum of positive criticals is the
        headline over-billing number."""
        return self.billed_amount - self.expected_amount

    @property
    def severity(self) -> Severity:
        """Max severity across this row's flags (OK if none)."""
        if not self.flags:
            return Severity.OK
        return max((f.severity for f in self.flags), key=lambda s: s.rank)
