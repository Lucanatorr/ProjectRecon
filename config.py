"""Central configuration for Splice.

Tolerances, matching thresholds, retainage defaults, and paths live here so the
domain core stays free of magic numbers. See SDD Appendix B for defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root — used to anchor the SQLite path regardless of cwd.
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"


@dataclass(frozen=True)
class ToleranceConfig:
    """Per-UoM tolerances. Counted units (EA) match exactly; measured units (FT)
    absorb legitimate field-vs-design differences with an absolute or % band."""
    ft_abs: float = 50.0          # feet
    ft_pct: float = 0.02          # 2%
    ea_abs: float = 0.0           # counts must match exactly

    def band_for(self, uom: str, built_qty: float) -> float:
        """Return the allowed +/- band for a unit of the given UoM."""
        if uom == "FT":
            return max(self.ft_abs, abs(built_qty) * self.ft_pct)
        if uom == "100FT":
            # 100FT is stored/compared in FT after normalization; keep the band in FT.
            return max(self.ft_abs, abs(built_qty) * self.ft_pct)
        return self.ea_abs


@dataclass(frozen=True)
class MatchingConfig:
    """Crosswalk fuzzy-matching behavior."""
    auto_threshold: int = 90            # >= this score auto-maps
    # WRatio is rapidfuzz's weighted combination scorer — robust to extra/missing
    # words and word-order differences, which is exactly how real invoice vs bid
    # descriptions differ. Empirically reproduces the mockup's auto/review split.
    scorer: str = "WRatio"
    top_n_candidates: int = 3          # candidates surfaced for manual review


@dataclass(frozen=True)
class ReconConfig:
    """Bundle passed into the reconcile engine."""
    tolerance: ToleranceConfig = field(default_factory=ToleranceConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    retainage_default_pct: float = 10.0
    price_epsilon: float = 1e-6        # float slop when comparing prices
    cumulative: bool = True            # default billing mode (pay apps)


# Module-level singletons for convenient import.
TOLERANCE = ToleranceConfig()
MATCHING = MatchingConfig()
RECON = ReconConfig()

# SQLite location — overridable via env for tests / alt deployments.
DB_PATH = Path(os.environ.get("SPLICE_DB_PATH", str(DATA_DIR / "recon.db")))
