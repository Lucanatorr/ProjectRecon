"""Geo-2 — the folium feature-map builder (Streamlit-free, so unit-testable)."""
from __future__ import annotations

import folium

from config import ROOT
from recon.geo.importer import load_features
from recon.geo.models import GeoFeature
from ui.geo_map import FEATURE_COLORS, feature_map, legend_html

SAMPLE = ROOT / "samples" / "AsBuilt_PhaseB_map.geojson"


def test_feature_map_builds_from_the_sample():
    feats = load_features(SAMPLE.read_text(encoding="utf-8"))
    m = feature_map(feats)
    assert isinstance(m, folium.Map)
    html = m.get_root().render()
    assert "World_Imagery" in html                     # aerial basemap present
    # one marker per point feature + polylines for the runs → many children
    markers = sum(1 for c in m._children.values()
                  if isinstance(c, folium.CircleMarker))
    assert markers == 14                               # 6 HH + 5 pole + 2 splice + 1 ped


def test_point_and_line_features_map_to_markers_and_polylines():
    feats = [
        GeoFeature("handhole", {"type": "Point", "coordinates": [-79.0, 34.61]}),
        GeoFeature("buried_cable", {"type": "LineString",
                                    "coordinates": [[-79.0, 34.61], [-79.0, 34.62]]}),
    ]
    m = feature_map(feats)
    kinds = [type(c).__name__ for c in m._children.values()]
    assert "CircleMarker" in kinds and "PolyLine" in kinds


def test_legend_lists_present_types_with_colours():
    feats = load_features(SAMPLE.read_text(encoding="utf-8"))
    html = legend_html(feats)
    assert FEATURE_COLORS["handhole"] in html
    assert "Handhole 30x48" in html


def test_unknown_type_uses_the_standout_colour_and_marks_unknown():
    feats = [GeoFeature("mystery", {"type": "Point", "coordinates": [-79.0, 34.61]})]
    html = legend_html(feats)
    assert "mystery · unknown" in html
