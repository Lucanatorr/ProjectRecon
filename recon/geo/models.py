"""Geo domain model: the feature-type registry and the captured feature.

The registry knows each fiber feature's geometry kind, unit of measure, and how it
bills (by length or by count). It does NOT own your real contract codes — the
``default_code`` values here are **placeholders**. Real codes differ per job and
change over time, so the authoritative mapping is per-project, editable data (the
crosswalk); these defaults only seed a new project and demos. See the spec §3–§4.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from recon.models import UoM


@dataclass(frozen=True)
class FeatureType:
    """A fiber feature the map tool can record."""
    type_id: str          # stable key used in GeoJSON `feature_type`
    label: str            # human label; becomes the AsBuiltLine description
    geometry: str         # 'point' | 'line'
    bills: str            # 'count' | 'length'
    uom: UoM
    default_code: str | None    # PLACEHOLDER contract code — override per project


# Placeholder contract codes (edit per project via the crosswalk). Chosen to match
# the demo bid schedule so the tool works out of the box; they are NOT assumed to
# be any real job's codes.
FEATURE_TYPES: dict[str, FeatureType] = {
    "aerial_cable":     FeatureType("aerial_cable", "Place 144ct ADSS aerial",
                                    "line", "length", UoM.FT, "3.1"),
    "aerial_cable_288": FeatureType("aerial_cable_288", "Place 288ct ADSS aerial",
                                    "line", "length", UoM.FT, "3.2"),
    "buried_cable":     FeatureType("buried_cable", "Trench / plow fiber",
                                    "line", "length", UoM.FT, "4.2"),
    "conduit_bore":     FeatureType("conduit_bore", "Directional bore 2\"",
                                    "line", "length", UoM.FT, "4.1"),
    "drop":             FeatureType("drop", "Drop placement",
                                    "line", "count", UoM.EA, "9.1"),
    "handhole":         FeatureType("handhole", "Handhole 30x48",
                                    "point", "count", UoM.EA, "5.1"),
    "pedestal":         FeatureType("pedestal", "Fiber pedestal",
                                    "point", "count", UoM.EA, "5.2"),
    "splice_closure":   FeatureType("splice_closure", "Splice closure",
                                    "point", "count", UoM.EA, "6.2"),
    "pole":             FeatureType("pole", "Pole make-ready",
                                    "point", "count", UoM.EA, "8.1"),
}


@dataclass
class GeoFeature:
    """One captured feature. ``geometry`` is a GeoJSON geometry dict (Point,
    LineString, or MultiLineString) with coordinates in lon, lat (EPSG:4326)."""
    feature_type: str
    geometry: dict
    attrs: dict = field(default_factory=dict)
    local_id: str | None = None
    captured_by: str | None = None
    captured_at: str | None = None
    source: str | None = None          # 'field-pwa' | 'desk-draw' | 'import'
    gps_accuracy_m: float | None = None
    photos: list[str] = field(default_factory=list)
