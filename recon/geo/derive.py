"""Geometry → billable quantities (spec §5).

Length-billed features contribute their **geodesic** length (pyproj, no projection
step) in feet; count-billed features contribute 1 each. Quantities aggregate by
contract code into ``AsBuiltLine``s — the exact input the reconciliation engine
consumes. Features whose type resolves to no code are returned separately for the
soft gate, never silently dropped.
"""
from __future__ import annotations

from pyproj import Geod

from recon.geo.crosswalk import resolve_code
from recon.geo.models import FEATURE_TYPES, GeoFeature
from recon.models import AsBuiltLine

_GEOD = Geod(ellps="WGS84")
_M_TO_FT = 3.280839895013123


def _line_length_ft(geometry: dict) -> float:
    """Geodesic length of a LineString / MultiLineString, in feet. Coordinates are
    lon, lat (EPSG:4326)."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    parts = ([coords] if gtype == "LineString"
             else coords if gtype == "MultiLineString" else [])
    metres = 0.0
    for part in parts:
        if len(part) < 2:
            continue
        lons = [pt[0] for pt in part]
        lats = [pt[1] for pt in part]
        metres += _GEOD.line_length(lons, lats)
    return metres * _M_TO_FT


def derive(features: list[GeoFeature],
           code_map: dict[str, str] | None = None
           ) -> tuple[list[AsBuiltLine], list[GeoFeature]]:
    """Aggregate features into per-code ``AsBuiltLine``s.

    Returns ``(lines, unmapped)`` — ``unmapped`` are features whose type has no
    code (unknown type, or a known type the project hasn't mapped). Length quantities
    are feet and count quantities are each; the engine normalizes these against
    whatever unit the contract prices the code in (incl. per-100FT).
    """
    agg: dict[str, dict] = {}
    unmapped: list[GeoFeature] = []

    for f in features:
        ftype = FEATURE_TYPES.get(f.feature_type)
        code = resolve_code(f.feature_type, code_map)
        if ftype is None or code is None:
            unmapped.append(f)
            continue
        bucket = agg.setdefault(code, {"qty": 0.0, "label": ftype.label,
                                       "bills": ftype.bills, "uom": ftype.uom,
                                       "n": 0})
        bucket["n"] += 1
        if ftype.bills == "length":
            bucket["qty"] += _line_length_ft(f.geometry)
        else:
            bucket["qty"] += 1

    lines: list[AsBuiltLine] = []
    for code, b in sorted(agg.items()):
        qty = round(b["qty"]) if b["bills"] == "length" else b["qty"]
        plural = "s" if b["n"] != 1 else ""
        lines.append(AsBuiltLine(
            raw_desc=b["label"], qty=float(qty), uom=b["uom"], code=code,
            source_ref=f"geo:{b['n']} feature{plural}", confidence="geo"))
    return lines, unmapped
