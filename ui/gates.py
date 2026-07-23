"""Pre-export validation gates (SDD §9).

"Validation gates prevent finalizing on bad inputs: unresolved crosswalk items,
unconfirmed OCR rows, or a missing bid schedule each block a clean export (with an
explicit override that is logged)." Critical flags must also carry a reviewer
decision (SDD §7.3).

Pure functions over the wizard state so they can be tested without Streamlit.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Gate:
    key: str
    ok: bool
    label: str
    detail: str
    fix_step: str          # wizard step that resolves this gate


def evaluate_gates(state) -> list[Gate]:
    """Every pre-export check, passing or not, in the order a coordinator meets them."""
    from ui.state import _crosswalk_review_count, unresolved_criticals

    n_units = len(state.contract)
    n_review = _crosswalk_review_count(state)
    unconfirmed = [a for a in state.asbuilt if a.confidence in ("pdf", "ocr")]
    open_criticals = unresolved_criticals(state)

    return [
        Gate(
            "contract", bool(state.contract), "Bid schedule loaded",
            f"{n_units} contract units" if n_units
            else "No bid schedule — dollar variances can't be checked",
            "contract"),
        Gate(
            "confidence", not unconfirmed, "Extracted quantities confirmed",
            "All built quantities confirmed" if not unconfirmed
            else f"{len(unconfirmed)} extracted row(s) not yet confirmed by a human",
            "asbuilt"),
        Gate(
            "crosswalk", n_review == 0, "Crosswalk resolved",
            "Every description is mapped" if n_review == 0
            else f"{n_review} description(s) still need review",
            "crosswalk"),
        Gate(
            "criticals", not open_criticals, "Critical flags decided",
            "No critical items awaiting a decision" if not open_criticals
            else f"{len(open_criticals)} critical item(s) need a hold or approve",
            "reconcile"),
    ]


def blocking(gates: list[Gate]) -> list[Gate]:
    """The gates that are not satisfied — these block a clean export."""
    return [g for g in gates if not g.ok]
