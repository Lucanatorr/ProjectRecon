"""Feature-type → contract-code mapping.

The structured cousin of the description crosswalk: features are typed, so the
mapping is a plain lookup. A per-project ``code_map`` (editable, persisted in a
later sprint) overrides the registry's placeholder defaults. A feature whose type
resolves to no code is *unmapped* and left out of derived quantities (spec §4, D2).
"""
from __future__ import annotations

from recon.geo.models import FEATURE_TYPES, GeoFeature


def default_code_map() -> dict[str, str]:
    """The registry's placeholder mappings — a starting point a project edits."""
    return {tid: ft.default_code for tid, ft in FEATURE_TYPES.items()
            if ft.default_code}


def resolve_code(feature_type: str,
                 code_map: dict[str, str] | None = None) -> str | None:
    """The contract code for a feature type: the project override if present, else
    the registry placeholder, else None (unknown/unmapped type)."""
    if code_map and feature_type in code_map:
        return code_map[feature_type]
    ft = FEATURE_TYPES.get(feature_type)
    return ft.default_code if ft else None


def unmapped_feature_types(features: list[GeoFeature],
                           code_map: dict[str, str] | None = None) -> list[str]:
    """Distinct feature-type strings present in ``features`` that resolve to no
    code — what the soft gate surfaces before a send (preserves first-seen order)."""
    out: list[str] = []
    for f in features:
        if resolve_code(f.feature_type, code_map) is None \
                and f.feature_type not in out:
            out.append(f.feature_type)
    return out
