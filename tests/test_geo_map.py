"""Geo-2 — the folium feature-map builder (Streamlit-free, so unit-testable)."""
from __future__ import annotations

import folium

from config import ROOT
from recon.geo.derive import derive
from recon.geo.importer import load_features
from recon.geo.models import GeoFeature
from ui.geo_map import (
    FEATURE_COLORS,
    drawn_to_feature,
    feature_map,
    legend_html,
)

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


# --- on-map drawing ------------------------------------------------------- #
def test_draw_controls_are_added_when_requested():
    plain = feature_map([]).get_root().render()
    drawable = feature_map([], draw=True).get_root().render()
    assert "L.Control.Draw" not in plain
    assert "L.Control.Draw" in drawable                # Leaflet.draw control present


def test_drawn_to_feature_converts_a_leaflet_result():
    point = {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [-79.0, 34.61]}}
    f = drawn_to_feature(point, "handhole", local_id="draw-1")
    assert f.feature_type == "handhole" and f.source == "desk-draw"
    assert f.geometry["type"] == "Point" and f.local_id == "draw-1"

    bare_line = {"type": "LineString", "coordinates": [[-79.0, 34.61], [-79.0, 34.62]]}
    assert drawn_to_feature(bare_line, "buried_cable").geometry["type"] == "LineString"


def test_drawn_to_feature_rejects_unsupported_geometry():
    poly = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}}
    assert drawn_to_feature(poly, "handhole") is None
    assert drawn_to_feature(None, "handhole") is None


def test_drawn_features_merge_into_derived_quantities():
    imported = load_features(SAMPLE.read_text(encoding="utf-8"))
    drawn = drawn_to_feature(
        {"type": "Point", "coordinates": [-79.013, 34.607]}, "handhole", "draw-1")
    lines, _ = derive(imported + [drawn])
    by_code = {ln.code: ln for ln in lines}
    assert by_code["5.1"].qty == 7                     # 6 imported + 1 drawn handhole
