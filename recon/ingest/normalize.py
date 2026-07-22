"""String + UoM normalization.

Canonicalizing free text is what makes fuzzy matching reliable: the tally sheet,
invoice, and bid schedule all describe the same unit differently. See spec §5d.
"""
from __future__ import annotations

import re

from recon.models import UoM

# Abbreviation / synonym expansion applied token-by-token after basic cleanup.
# Keys are matched as whole tokens (word boundaries), values replace them.
_ABBREV = {
    "adss": "adss",              # keep as-is (a distinguishing token)
    "ct": "count",
    "cnt": "count",
    "ug": "underground",
    "u/g": "underground",
    "mr": "makeready",
    "make-ready": "makeready",
    "makeready": "makeready",
    "lf": "ft",
    "ln": "ft",
    "ea": "ea",
    "each": "ea",
    "dir": "directional",
    "hh": "handhole",
    "ped": "pedestal",
    "otdr": "otdr",
    "ont": "ont",
    "nid": "nid",
}

# Fiber-count normalization: "144f" / "144ct" / "144 count" -> "144count"
_FIBER_COUNT = re.compile(r"\b(\d{2,4})\s*(?:f|ct|cnt|count|fiber|fibers|c)\b")

_PUNCT = re.compile(r"[^\w\s]")     # keep word chars + whitespace
_WS = re.compile(r"\s+")
_INCH = re.compile(r"[″”\"']")   # ″ ” " ' -> "in"


def normalize(text: str | None) -> str:
    """Lowercase, trim, collapse whitespace, strip punctuation, expand
    abbreviations. Deterministic and idempotent — the crosswalk key."""
    if text is None:
        return ""
    s = str(text).lower().strip()
    if not s:
        return ""
    # Normalize inch marks to a token before stripping punctuation.
    s = _INCH.sub(" in ", s)
    # Normalize fiber-count expressions early (before punctuation strip splits them).
    s = _FIBER_COUNT.sub(r"\1count", s)
    # Strip remaining punctuation to spaces.
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    # Token-level abbreviation expansion.
    tokens = [_ABBREV.get(tok, tok) for tok in s.split(" ")]
    s = " ".join(t for t in tokens if t)
    s = _WS.sub(" ", s).strip()
    return s


# UoM textual variants -> canonical FT equivalence handled in reconcile via
# conversion; here we just parse to the enum.
def normalize_uom(raw: str | None) -> UoM | None:
    """Map a raw UoM string to the enum. Returns None if unrecognized."""
    return UoM.from_str(raw)


# --- UoM quantity conversion --------------------------------------------------
# Quantities are compared in a canonical base unit per family so 100FT and FT
# reconcile correctly. FT is the base for length; EA and LS are their own base.
def to_base_qty(qty: float, uom: UoM | None) -> float:
    """Convert a quantity to its canonical base unit (100FT -> FT)."""
    if uom == UoM.C_FT:
        return qty * 100.0
    return qty


def base_uom(uom: UoM | None) -> UoM | None:
    """Return the canonical base UoM for comparison (100FT -> FT)."""
    if uom == UoM.C_FT:
        return UoM.FT
    return uom
