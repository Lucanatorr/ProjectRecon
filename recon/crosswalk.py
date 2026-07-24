"""Crosswalk engine — the automation of manual description matching.

Exact alias (learned) → high-confidence fuzzy auto → below-threshold human review.
Confirmed mappings persist to a global alias store so the tool gets smarter across
every reconciliation. See spec §6 / SDD §5.6.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from config import MATCHING
from recon.ingest.normalize import normalize
from recon.models import ContractItem

_SCORERS = {
    "token_sort_ratio": fuzz.token_sort_ratio,
    "token_set_ratio": fuzz.token_set_ratio,
    "WRatio": fuzz.WRatio,
    "ratio": fuzz.ratio,
}


@dataclass
class Candidate:
    code: str
    description: str
    score: float
    field: str = "description"      # which column produced the score: code | description


@dataclass
class Match:
    """Result of resolving one raw description."""
    code: str | None            # canonical code, or None if needs review
    score: float                # best score (0-100)
    kind: str                   # "alias" | "auto" | "review"
    candidates: list[Candidate] = field(default_factory=list)


class AliasStore:
    """In-memory alias map keyed by normalized description.

    A thin abstraction so persistence.py can back it with SQLite while tests and
    the domain core use a plain dict.
    """

    def __init__(self, mapping: dict[str, str] | None = None):
        self._map: dict[str, str] = dict(mapping or {})

    def get(self, normalized_desc: str) -> str | None:
        return self._map.get(normalized_desc)

    def confirm(self, raw_desc: str, code: str) -> str:
        """Record raw_desc -> code under its normalized key. Returns the key."""
        key = normalize(raw_desc)
        self._map[key] = code
        return key

    def remove(self, raw_desc: str) -> None:
        """Forget a learned mapping (used when a coordinator re-opens an item)."""
        self._map.pop(normalize(raw_desc), None)

    def as_dict(self) -> dict[str, str]:
        return dict(self._map)

    def __contains__(self, normalized_desc: str) -> bool:
        return normalized_desc in self._map

    def __len__(self) -> int:
        return len(self._map)


def resolve(
    raw_desc: str,
    contract_items: list[ContractItem],
    alias_store: AliasStore | None = None,
    *,
    threshold: int | None = None,
    scorer: str | None = None,
    top_n: int | None = None,
) -> Match:
    """Resolve free text to a canonical code, matching on BOTH contract columns.

    Source text is often the contract *code* itself (e.g. "BHF-48", "AFO.SL"), not
    a description, so every contract item is scored against its code and its
    description and keeps the better of the two.

    1. Exact alias hit        -> auto-map (score 100, kind "alias").
    2. Exact code match       -> auto-map (score 100, kind "code").
    3. Best fuzzy >= threshold-> auto-map (kind "auto").
    4. Below threshold        -> kind "review" with top-N candidates for the UI.
    """
    threshold = MATCHING.auto_threshold if threshold is None else threshold
    scorer_name = scorer or MATCHING.scorer
    top_n = MATCHING.top_n_candidates if top_n is None else top_n
    scorer_fn = _SCORERS.get(scorer_name, fuzz.token_sort_ratio)

    key = normalize(raw_desc)

    if alias_store is not None:
        hit = alias_store.get(key)
        if hit is not None:
            return Match(code=hit, score=100.0, kind="alias")

    if not contract_items:
        return Match(code=None, score=0.0, kind="review")

    norm_codes = [normalize(ci.code) for ci in contract_items]
    norm_descs = [normalize(ci.description) for ci in contract_items]

    # A code is a precise identifier — an exact hit settles it outright.
    if key:
        for i, nc in enumerate(norm_codes):
            if nc and nc == key:
                ci = contract_items[i]
                return Match(code=ci.code, score=100.0, kind="code",
                             candidates=[Candidate(ci.code, ci.description, 100.0, "code")])

    # Score every item against both columns, keeping whichever matched better.
    code_scores = {i: s for _, s, i in
                   process.extract(key, norm_codes, scorer=scorer_fn, limit=None)}
    desc_scores = {i: s for _, s, i in
                   process.extract(key, norm_descs, scorer=scorer_fn, limit=None)}

    scored: list[Candidate] = []
    for i, ci in enumerate(contract_items):
        s_code = float(code_scores.get(i, 0.0))
        s_desc = float(desc_scores.get(i, 0.0))
        if s_code >= s_desc:
            scored.append(Candidate(ci.code, ci.description, s_code, "code"))
        else:
            scored.append(Candidate(ci.code, ci.description, s_desc, "description"))
    scored.sort(key=lambda c: -c.score)
    candidates = scored[:top_n]

    if not candidates:
        return Match(code=None, score=0.0, kind="review")

    best = candidates[0]
    if best.score >= threshold:
        return Match(code=best.code, score=best.score, kind="auto", candidates=candidates)
    return Match(code=None, score=best.score, kind="review", candidates=candidates)


def resolve_all(
    raw_descs: list[str],
    contract_items: list[ContractItem],
    alias_store: AliasStore | None = None,
    **kwargs,
) -> dict[str, Match]:
    """Resolve many descriptions; returns raw_desc -> Match. Deduped by raw text."""
    out: dict[str, Match] = {}
    for desc in raw_descs:
        if desc in out:
            continue
        out[desc] = resolve(desc, contract_items, alias_store, **kwargs)
    return out
