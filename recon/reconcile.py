"""Reconciliation engine — aggregate → deltas → flags.

Aggregates as-built and invoices by canonical code, computes quantity / price /
dollar variances against the contract, and classifies each unit with severity-
tagged flags. Pure and deterministic. See spec §7 / SDD §7.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from config import RECON, ReconConfig
from recon.ingest.normalize import base_uom, to_base_qty
from recon.models import (
    AsBuiltLine,
    ContractItem,
    Flag,
    InvoiceLine,
    ReconRow,
    Severity,
    UoM,
)


@dataclass
class Aggregated:
    """Per-unit rollup carried into reconcile(). `code` is the canonical contract
    code, or None for an unmatched line; `desc` holds a display description used
    when there is no contract item to name the row."""
    code: str | None
    desc: str | None = None
    built_qty: float = 0.0
    billed_qty: float = 0.0
    billed_amount: float = 0.0
    billed_price: float = 0.0
    uom: UoM | None = None
    asbuilt_refs: list[str] = None      # type: ignore[assignment]
    invoice_refs: list[str] = None      # type: ignore[assignment]

    def __post_init__(self):
        if self.asbuilt_refs is None:
            self.asbuilt_refs = []
        if self.invoice_refs is None:
            self.invoice_refs = []


def _ref(prefix: str, line) -> str:
    parts = [p for p in (getattr(line, "source_file", None),
                         getattr(line, "source_ref", None) or getattr(line, "line_ref", None))
             if p]
    return f"{prefix}: {' · '.join(parts)}" if parts else prefix


def _group_key(code: str | None, raw_desc: str) -> str:
    """Aggregation key. Matched lines group by code; unmatched lines (code=None)
    stay separate per description so distinct unauthorized units are never merged
    (and never collapsed by the cumulative max-qty reduction)."""
    return code if code is not None else f"\x00unmatched\x00{raw_desc.strip().lower()}"


def aggregate(
    asbuilt: list[AsBuiltLine],
    invoices: list[InvoiceLine],
    *,
    cumulative: bool = True,
) -> dict[str, Aggregated]:
    """Group as-built and invoices into per-unit rollups keyed by contract code
    (or per-description for unmatched lines).

    As-built quantities are summed. Invoices in cumulative mode use the latest
    to-date figure per unit (the max-qty line); in discrete mode they are summed.
    Quantities are converted to a canonical base UoM (100FT → FT) before summing.
    """
    aggs: dict[str, Aggregated] = {}

    def _get(code: str | None, raw_desc: str) -> Aggregated:
        key = _group_key(code, raw_desc)
        if key not in aggs:
            aggs[key] = Aggregated(code=code, desc=raw_desc)
        return aggs[key]

    # --- as-built: sum by code (unmatched kept separate by description) ---
    for ln in asbuilt:
        a = _get(ln.code, ln.raw_desc)
        a.built_qty += to_base_qty(ln.qty, ln.uom)
        if a.uom is None and ln.uom is not None:
            a.uom = base_uom(ln.uom)
        a.asbuilt_refs.append(_ref("as-built", ln))

    # --- invoices: group per unit, then reduce by billing mode ---
    by_key: dict[str, list[InvoiceLine]] = defaultdict(list)
    for ln in invoices:
        by_key[_group_key(ln.code, ln.raw_desc)].append(ln)

    for lines in by_key.values():
        a = _get(lines[0].code, lines[0].raw_desc)
        # InvoiceLine carries no UoM (per spec §3); quantities are taken as already
        # expressed in the contract unit. UoM for the row derives from as-built/contract.
        if cumulative:
            # Latest to-date per unit = the line carrying the greatest quantity.
            top = max(lines, key=lambda l: l.qty)
            a.billed_qty += top.qty
            a.billed_amount += top.amount if top.amount else top.qty * top.unit_price
        else:
            for ln in lines:
                a.billed_qty += ln.qty
                a.billed_amount += ln.amount if ln.amount else ln.qty * ln.unit_price
        for ln in lines:
            a.invoice_refs.append(_ref("invoice", ln))
        # Representative billed unit price = amount / qty (falls back to line price).
        a.billed_price = (a.billed_amount / a.billed_qty) if a.billed_qty else (
            lines[0].unit_price if lines else 0.0)

    return aggs


def reconcile(
    asbuilt: list[AsBuiltLine],
    invoices: list[InvoiceLine],
    contract: list[ContractItem] | dict[str, ContractItem],
    cfg: ReconConfig = RECON,
    prior_billed: dict[str, float] | None = None,
) -> list[ReconRow]:
    """Produce one ReconRow per code present in as-built or invoices.

    ``prior_billed`` maps code → the prior cycle's billed-to-date quantity; in
    cumulative mode it enables the current-vs-prior check (SDD §7.5).
    """
    contract_idx: dict[str, ContractItem] = (
        contract if isinstance(contract, dict) else {ci.code: ci for ci in contract}
    )
    aggs = aggregate(asbuilt, invoices, cumulative=cfg.cumulative)
    use_prior = cfg.cumulative and bool(prior_billed)

    rows: list[ReconRow] = []
    for a in aggs.values():
        code = a.code
        ci = contract_idx.get(code) if code is not None else None
        uom = ci.uom if ci else (a.uom or UoM.EA)
        # base UoM for tolerance math (100FT already converted to FT in aggregate)
        uom_for_tol = base_uom(uom) or UoM.EA
        row = ReconRow(
            code=code,
            description=ci.description if ci else (a.desc or code or "(unmatched)"),
            uom=uom_for_tol,
            built_qty=a.built_qty,
            billed_qty=a.billed_qty,
            contract_price=ci.unit_price if ci else None,
            billed_price=a.billed_price,
            est_qty=ci.est_qty if ci else None,
            is_change_order=bool(ci.is_change_order) if ci else False,
            prior_billed_qty=(prior_billed.get(code) if (use_prior and code) else None),
            asbuilt_refs=a.asbuilt_refs,
            invoice_refs=a.invoice_refs,
        )
        row.flags = flags_for(row, cfg)
        rows.append(row)

    # Stable order: critical → warning → info → ok, then by descending exposure.
    rows.sort(key=lambda r: (-r.severity.rank, -abs(r.amount_variance)))
    return rows


def flags_for(row: ReconRow, cfg: ReconConfig = RECON) -> list[Flag]:
    """Apply the flag rules (SDD §7.3) to a single row."""
    flags: list[Flag] = []
    tol = cfg.tolerance.band_for(row.uom.value, row.built_qty)
    eps = cfg.price_epsilon
    has_contract = row.contract_price is not None
    is_billed = row.billed_qty > 0 or row.billed_amount != 0

    # --- CRITICAL ---
    if not has_contract and is_billed:
        flags.append(Flag(
            "no_contract", Severity.CRITICAL,
            "Unit billed with no contract item (unauthorized / needs change order)",
        ))
    if row.billed_qty - row.built_qty > tol:
        flags.append(Flag(
            "qty_over", Severity.CRITICAL,
            f"Billed qty exceeds built by {row.qty_delta:,.6g} {row.uom.value} "
            f"(tolerance ±{tol:,.6g})",
        ))
    if has_contract and row.billed_price > row.contract_price + eps:
        flags.append(Flag(
            "price_over", Severity.CRITICAL,
            f"Unit price ${row.billed_price:,.2f} over contract "
            f"${row.contract_price:,.2f} (+${row.price_delta:,.2f}/unit)",
        ))

    # --- WARNING ---
    if row.est_qty and max(row.built_qty, row.billed_qty) > row.est_qty:
        over = max(row.built_qty, row.billed_qty) - row.est_qty
        flags.append(Flag(
            "over_run", Severity.WARNING,
            f"Over-run vs bid estimate (+{over:,.6g} over {row.est_qty:,.6g})",
        ))
    if row.code is None and is_billed:
        flags.append(Flag(
            "unmatched", Severity.WARNING,
            "Line not matched to a contract unit — resolve in crosswalk before finalize",
        ))
    # cumulative pay app: billed-to-date must not fall below the prior cycle
    if (cfg.cumulative and row.prior_billed_qty is not None
            and row.prior_billed_qty - row.billed_qty > tol):
        flags.append(Flag(
            "cumulative_decrease", Severity.WARNING,
            f"Billed-to-date {row.billed_qty:,.6g} fell below prior cycle "
            f"{row.prior_billed_qty:,.6g} {row.uom.value} — a cumulative pay app "
            "should not decrease",
        ))

    # --- INFO ---
    if row.built_qty - row.billed_qty > tol:
        flags.append(Flag(
            "under_billed", Severity.INFO,
            f"Built but not yet billed: {(row.built_qty - row.billed_qty):,.6g} "
            f"{row.uom.value} (under-billed)",
        ))
    if has_contract and row.billed_price < row.contract_price - eps and is_billed:
        flags.append(Flag(
            "price_under", Severity.INFO,
            f"Unit price ${row.billed_price:,.2f} below contract "
            f"${row.contract_price:,.2f}",
        ))

    return flags


# --- cycle-level rollups ------------------------------------------------------
@dataclass
class CycleTotals:
    total_billed: float
    total_expected: float
    flagged_over_billing: float     # sum of positive critical variances
    n_critical: int
    n_warning: int
    n_ok: int
    retainage_held: float
    net_recommended: float


def cycle_totals(rows: list[ReconRow], retainage_pct: float = 0.0) -> CycleTotals:
    """Roll rows into the headline KPI numbers shown on the dashboard."""
    total_billed = sum(r.billed_amount for r in rows)
    total_expected = sum(r.expected_amount for r in rows)
    flagged = sum(
        r.amount_variance for r in rows
        if r.severity == Severity.CRITICAL and r.amount_variance > 0
    )
    n_crit = sum(1 for r in rows if r.severity == Severity.CRITICAL)
    n_warn = sum(1 for r in rows if r.severity == Severity.WARNING)
    n_ok = sum(1 for r in rows if r.severity in (Severity.OK, Severity.INFO))
    retainage = total_billed * (retainage_pct / 100.0)
    net = total_billed - flagged - retainage
    return CycleTotals(
        total_billed=total_billed,
        total_expected=total_expected,
        flagged_over_billing=flagged,
        n_critical=n_crit,
        n_warning=n_warn,
        n_ok=n_ok,
        retainage_held=retainage,
        net_recommended=net,
    )


@dataclass
class RetainageCheck:
    """Validation of the retainage a contractor actually withheld against the
    contract-required amount (SDD §7.3 retainage-mismatch warning)."""
    contract_pct: float
    expected: float             # gross × contract_pct — what should be withheld
    actual: float | None        # what the invoice withheld (None if not provided)
    ok: bool
    variance: float             # actual − expected (0 when actual is None)
    message: str

    @property
    def has_actual(self) -> bool:
        return self.actual is not None


def check_retainage(gross: float, contract_pct: float, actual: float | None = None,
                    tol_abs: float = 1.0) -> RetainageCheck:
    """Compare the retainage withheld on the invoice against the contract rate.

    With no ``actual`` figure this just reports the expected withholding; given one,
    it flags an over- or under-withholding beyond ``tol_abs`` dollars.
    """
    expected = gross * (contract_pct / 100.0)
    if actual is None:
        return RetainageCheck(
            contract_pct, expected, None, True, 0.0,
            f"Contract retainage {contract_pct:g}% → ${expected:,.2f} withheld this cycle.")
    variance = actual - expected
    ok = abs(variance) <= tol_abs
    if ok:
        msg = (f"Retainage matches contract ({contract_pct:g}%): "
               f"${actual:,.2f} withheld.")
    else:
        verb = "over-withheld" if variance > 0 else "under-withheld"
        msg = (f"Retainage {verb} by ${abs(variance):,.2f} — invoice withheld "
               f"${actual:,.2f}, contract {contract_pct:g}% = ${expected:,.2f}.")
    return RetainageCheck(contract_pct, expected, actual, ok, variance, msg)
