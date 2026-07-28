"""Read the As-Built Feature exchange format (spec §2) into GeoFeatures.

Phase A accepts a GeoJSON FeatureCollection already in EPSG:4326 (GeoJSON's
default and only standard CRS). Reprojection of non-4326 imports lands with real
file support in Geo-1; here we assume/require 4326.
"""
from __future__ import annotations

import json

from recon.geo.models import GeoFeature

_GEOM_TYPES = {"Point", "LineString", "MultiLineString"}


def load_features(geojson: dict | str) -> list[GeoFeature]:
    """Parse a FeatureCollection (dict or JSON string) into GeoFeatures. Features
    without a geometry or a ``feature_type`` are skipped; polygons are ignored."""
    data = json.loads(geojson) if isinstance(geojson, str) else geojson
    if not isinstance(data, dict):
        raise ValueError("GeoJSON must be an object")
    features = data.get("features") if data.get("type") == "FeatureCollection" \
        else [data] if data.get("type") == "Feature" else None
    if features is None:
        raise ValueError("expected a GeoJSON FeatureCollection or Feature")

    out: list[GeoFeature] = []
    for f in features:
        props = f.get("properties") or {}
        geom = f.get("geometry") or {}
        ftype = props.get("feature_type")
        if not ftype or geom.get("type") not in _GEOM_TYPES:
            continue
        out.append(GeoFeature(
            feature_type=ftype, geometry=geom,
            attrs=props.get("attrs") or {},
            local_id=props.get("local_id"),
            captured_by=props.get("captured_by"),
            captured_at=props.get("captured_at"),
            source=props.get("source"),
            gps_accuracy_m=props.get("gps_accuracy_m"),
            photos=props.get("photos") or [],
        ))
    return out
