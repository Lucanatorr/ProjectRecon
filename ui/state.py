"""Wizard session-state helpers. All working data for a reconciliation lives in
st.session_state under a single namespace object."""
from __future__ import annotations

from dataclasses import dataclass, field

import streamlit as st

from recon.crosswalk import AliasStore
from recon.models import AsBuiltLine, ContractItem, InvoiceLine, ReconRow

STEPS = [
    ("contract", "Contract", "Bid schedule"),
    ("asbuilt", "As-built", "Built quantities"),
    ("invoices", "Invoices", "This cycle's billing"),
    ("crosswalk", "Crosswalk", "Map descriptions to units"),
    ("reconcile", "Reconciliation", "Built vs billed"),
    ("export", "Export", "Report & sign-off"),
]


@dataclass
class WizardState:
    project_name: str = "New project"
    contractor: str = ""
    area: str = ""
    cycle_no: int = 1
    period_label: str = ""

    contract: list[ContractItem] = field(default_factory=list)
    asbuilt: list[AsBuiltLine] = field(default_factory=list)
    invoices: list[InvoiceLine] = field(default_factory=list)

    # source filenames, for the card headers ("Source: …")
    contract_source: str = ""
    asbuilt_source: str = ""
    asbuilt_warnings: list[str] = field(default_factory=list)   # from PDF extraction
    invoice_files: list[str] = field(default_factory=list)
    # most recent PDF invoice bytes, kept so the column-mapping UI can re-read it
    pending_pdf: bytes | None = None
    pending_pdf_name: str = ""
    invoice_profile_note: str = ""      # e.g. "Applied saved template for <contractor>"

    billing_mode: str = "cumulative"       # or "discrete"
    retainage_pct: float = 10.0            # contract-required retainage
    prior_billed: float = 0.0
    actual_retainage: float | None = None  # withheld on the invoice (for verification)

    # crosswalk: raw_desc -> code (confirmed this session), plus not-in-contract set
    resolved: dict[str, str] = field(default_factory=dict)
    not_in_contract: set[str] = field(default_factory=set)
    aliases: AliasStore = field(default_factory=AliasStore)

    results: list[ReconRow] = field(default_factory=list)
    results_fp: str = ""                  # fingerprint of inputs behind `results`
    prior_billed_by_code: dict[str, float] = field(default_factory=dict)  # prior cycle
    # reviewer decisions on flagged rows: row key -> {status, note, by, at}
    resolutions: dict[str, dict] = field(default_factory=dict)
    reviewer: str = ""                    # who is signing off this cycle
    # explicit, logged override of the pre-export gates: {reason, by, at}
    override: dict | None = None
    current: str = "contract"
    flash: str = ""                       # one-shot success message after an ingest

    # step completion flags
    done: set[str] = field(default_factory=set)


@st.cache_resource
def _state_store() -> dict:
    """Process-global map of session-id → WizardState.

    Navigation uses query-param <a> links, which reload the page and would wipe
    st.session_state. Holding state here (server memory, keyed by a URL session
    id) makes it survive reloads so the wizard keeps its data across steps."""
    return {}


def get_sid() -> str:
    """The session id from ?sid=; minted on first visit and pinned to the URL."""
    sid = st.query_params.get("sid")
    if not sid:
        import uuid
        sid = uuid.uuid4().hex[:12]
        st.query_params["sid"] = sid
    return sid


def get_state() -> WizardState:
    store = _state_store()
    sid = get_sid()
    if sid not in store:
        ws = WizardState()
        # seed the crosswalk with every mapping confirmed on past jobs (FR-7)
        from ui.db import load_aliases
        ws.aliases = load_aliases()
        store[sid] = ws
    return store[sid]


def load_demo(state: WizardState) -> None:
    """Populate the whole Robeson CAB — PON 5 demo (contract, as-built, invoices,
    confirmed crosswalk) in one shot. Triggered by ?demo=1 for quick walkthroughs."""
    from config import ROOT
    from recon.contract import load_bid_schedule
    from recon.crosswalk import resolve
    from recon.ingest.invoices import parse_invoices
    from recon.ingest.tally import parse_tally

    samples = ROOT / "samples"
    state.contract = load_bid_schedule(samples / "Fiber_Build_2025_BidSchedule.xlsx")
    state.contract_source = "Fiber_Build_2025_BidSchedule.xlsx"
    state.asbuilt = parse_tally(samples / "AsBuilt_PhaseB_Tally.xlsx")
    state.asbuilt_source = "AsBuilt_PhaseB_Tally.xlsx"
    inv = samples / "Invoice_2025-06_PhaseB.xlsx"
    state.invoices = parse_invoices([inv], is_cumulative=True)
    state.invoice_files = [inv.name]
    state.project_name = "Robeson CAB — PON 5"
    state.contractor = "Rivr Tech"
    state.cycle_no = 4
    state.period_label = "Jul 2026"
    # auto + confirm crosswalk (directional → 4.1; traffic → not in contract)
    for ln in list(state.asbuilt) + list(state.invoices):
        m = resolve(ln.raw_desc, state.contract, state.aliases)
        if m.code is not None:
            state.resolved[ln.raw_desc] = m.code
    state.resolved["Directional Drilling 2 inch"] = "4.1"
    state.not_in_contract.add("Traffic Control / Flagging (day)")
    for k in ("contract", "asbuilt", "invoices", "crosswalk"):
        state.done.add(k)


def goto(step: str) -> None:
    get_state().current = step
    st.rerun()


def step_meta(state: WizardState) -> list[dict]:
    """Per-step title, live description, and status for the sidebar stepper.
    Descriptions mirror the mockup's dynamic captions ("21 mapped", "3 critical
    flags", …). Status is 'done' | 'active' | 'pending'."""
    from recon.models import Severity

    n_units = len(state.contract)
    n_ab = len(state.asbuilt)
    n_inv = len(state.invoices)
    n_review = _crosswalk_review_count(state)
    n_mapped = len(state.resolved) + len(state.not_in_contract)
    n_crit = sum(1 for r in state.results if r.severity == Severity.CRITICAL)

    descs = {
        "contract": f"{n_units} units loaded" if n_units else "Bid schedule",
        "asbuilt": f"{n_ab} units" if n_ab else "Built quantities",
        "invoices": (f"{n_inv} lines · {state.billing_mode}" if n_inv
                     else "This cycle's billing"),
        "crosswalk": (f"{n_review} to review" if n_review
                      else (f"{n_mapped} mapped" if n_mapped else "Map to units")),
        "reconcile": (f"{n_crit} critical flags" if state.results
                      else "Built vs billed"),
        "export": "Report & sign-off",
    }
    done = _done_steps(state)
    out = []
    for i, (key, title, _default) in enumerate(STEPS):
        if key == state.current:
            status = "active"
        elif key in done:
            status = "done"
        else:
            status = "pending"
        out.append({"key": key, "num": str(i + 1), "title": title,
                    "desc": descs[key], "status": status})
    return out


def _crosswalk_review_count(state: WizardState) -> int:
    """How many descriptions still need human review (not yet auto/confirmed)."""
    from recon.crosswalk import resolve

    if not state.contract:
        return 0
    seen, n = set(), 0
    for ln in list(state.asbuilt) + list(state.invoices):
        d = ln.raw_desc
        if d in seen:
            continue
        seen.add(d)
        if d in state.resolved or d in state.not_in_contract:
            continue
        if resolve(d, state.contract, state.aliases).code is None:
            n += 1
    return n


def _done_steps(state: WizardState) -> set[str]:
    """Derive completion from actual data, so the stepper is always truthful."""
    done = set(state.done)
    if state.contract:
        done.add("contract")
    if state.asbuilt:
        done.add("asbuilt")
    if state.invoices:
        done.add("invoices")
    if state.contract and (state.asbuilt or state.invoices) and _crosswalk_review_count(state) == 0:
        done.add("crosswalk")
    if state.results:
        done.add("reconcile")
    return done


def contract_index(state: WizardState) -> dict[str, ContractItem]:
    return {ci.code: ci for ci in state.contract}


# --- reviewer resolutions on flagged rows (FR-14) --------------------------- #
RESOLUTION_STATUSES = ("hold", "approve", "note")


def row_key(row) -> str:
    """Stable identity for a reconciliation row. Unmatched rows have no code, so
    they fall back to their description."""
    return row.code or row.description


def set_resolution(state: WizardState, key: str, status: str, *,
                  note: str | None = None, by: str = "") -> None:
    """Record a reviewer decision (hold / approve / note) against a row.

    ``note=None`` keeps any existing note; ``note=""`` clears it.
    """
    from datetime import datetime
    existing = state.resolutions.get(key, {})
    state.resolutions[key] = {
        "status": status,
        "note": existing.get("note", "") if note is None else note,
        "by": by or state.reviewer or "",
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def clear_resolution(state: WizardState, key: str) -> None:
    state.resolutions.pop(key, None)


def unresolved_criticals(state: WizardState) -> list:
    """Critical rows with no reviewer decision yet — what blocks a clean sign-off."""
    from recon.models import Severity
    return [r for r in state.results
            if r.severity == Severity.CRITICAL
            and not state.resolutions.get(row_key(r))]


def inputs_fingerprint(state: WizardState) -> str:
    """Stable hash of everything that affects reconciliation output. When this
    changes, cached `results` are stale and must be recomputed."""
    import hashlib

    parts = [
        state.billing_mode, f"{state.retainage_pct}",
        *(f"{c.code}|{c.unit_price}|{c.est_qty}|{c.uom.value}|{int(c.is_change_order)}"
          for c in state.contract),
        *(f"{a.raw_desc}|{a.qty}|{a.uom.value if a.uom else ''}" for a in state.asbuilt),
        *(f"{i.invoice_id}|{i.raw_desc}|{i.qty}|{i.unit_price}" for i in state.invoices),
        *(f"{k}->{v}" for k, v in sorted(state.resolved.items())),
        *(f"nic:{d}" for d in sorted(state.not_in_contract)),
        *(f"prior:{k}={v}" for k, v in sorted(state.prior_billed_by_code.items())),
    ]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()
