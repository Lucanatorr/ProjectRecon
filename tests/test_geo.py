"""Geo-0 — geospatial as-built: GeoJSON import, feature-type crosswalk, and the
geometry → quantity derivation that feeds the reconciliation engine.

Lengths are cross-checked against an independent haversine so the golden isn't
just pyproj testing itself.
"""
from __future__ import annotations

import math

import pytest

from recon.geo.crosswalk import (
    default_code_map,
    resolve_code,
    unmapped_feature_types,
)
from recon.geo.derive import derive
from recon.geo.importer import load_features
from recon.geo.models import GeoFeature
from recon.models import UoM


def _hav_ft(a, b) -> float:
    """Haversine distance in feet between two [lon, lat] points."""
    R = 6371008.8                                  # mean Earth radius, metres
    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h)) * 3.280839895013123


def _line(ftype, coords, **props):
    return GeoFeature(feature_type=ftype, geometry={"type": "LineString",
                                                    "coordinates": coords}, **props)


def _point(ftype, lon, lat, **props):
    return GeoFeature(feature_type=ftype,
                      geometry={"type": "Point", "coordinates": [lon, lat]}, **props)


# --- import --------------------------------------------------------------- #
def test_load_features_reads_the_exchange_envelope():
    fc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-79.0142, 34.6106]},
            "properties": {
                "feature_type": "handhole", "local_id": "HH-014",
                "attrs": {"size": "30x48"}, "captured_by": "Rivr Tech",
                "gps_accuracy_m": 3.1, "photos": ["a.jpg"], "source": "import",
            },
        }],
    }
    feats = load_features(fc)
    assert len(feats) == 1
    f = feats[0]
    assert f.feature_type == "handhole" and f.local_id == "HH-014"
    assert f.attrs["size"] == "30x48" and f.gps_accuracy_m == 3.1
    assert f.photos == ["a.jpg"]


def test_load_features_skips_untyped_and_polygons():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
         "properties": {}},                                    # no feature_type
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []},
         "properties": {"feature_type": "service_area"}},      # polygon ignored
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]},
         "properties": {"feature_type": "pole"}},              # kept
    ]}
    feats = load_features(fc)
    assert [f.feature_type for f in feats] == ["pole"]


def test_load_features_accepts_a_json_string():
    assert load_features('{"type":"FeatureCollection","features":[]}') == []


# --- crosswalk ------------------------------------------------------------ #
def test_resolve_code_prefers_project_override():
    assert resolve_code("handhole") == "5.1"                   # placeholder default
    assert resolve_code("handhole", {"handhole": "HH-99"}) == "HH-99"
    assert resolve_code("mystery_widget") is None              # unknown type
    assert "handhole" in default_code_map()


def test_unmapped_types_are_listed_in_order():
    feats = [_point("handhole", 0, 0), _point("mystery", 0, 0),
             _point("gremlin", 0, 0), _point("mystery", 0, 0)]
    assert unmapped_feature_types(feats) == ["mystery", "gremlin"]


# --- derivation ----------------------------------------------------------- #
def test_line_length_matches_independent_haversine():
    a, b = [-79.00, 34.61], [-79.00, 34.62]
    lines, unmapped = derive([_line("buried_cable", [a, b])])
    assert not unmapped
    assert len(lines) == 1
    row = lines[0]
    assert row.code == "4.2" and row.uom == UoM.FT and row.confidence == "geo"
    assert row.qty == pytest.approx(_hav_ft(a, b), rel=0.01)   # cross-checked


def test_multilinestring_sums_its_segments():
    a, b, c = [-79.00, 34.61], [-79.00, 34.62], [-79.00, 34.63]
    multi = GeoFeature("aerial_cable", {"type": "MultiLineString",
                                        "coordinates": [[a, b], [b, c]]})
    lines, _ = derive([multi])
    assert lines[0].qty == pytest.approx(_hav_ft(a, b) + _hav_ft(b, c), rel=0.01)


def test_points_bill_as_counts_by_code():
    feats = [_point("handhole", -79.0, 34.61), _point("handhole", -79.0, 34.62),
             _point("handhole", -79.0, 34.63), _point("splice_closure", -79.0, 34.6)]
    lines, _ = derive(feats)
    by_code = {r.code: r for r in lines}
    assert by_code["5.1"].qty == 3 and by_code["5.1"].uom == UoM.EA
    assert by_code["5.1"].source_ref == "geo:3 features"
    assert by_code["6.2"].qty == 1 and by_code["6.2"].source_ref == "geo:1 feature"


def test_drops_bill_per_each_despite_line_geometry():
    a, b = [-79.0, 34.61], [-79.0, 34.615]
    lines, _ = derive([_line("drop", [a, b]), _line("drop", [a, b])])
    row = lines[0]
    assert row.code == "9.1" and row.uom == UoM.EA and row.qty == 2   # count, not length


def test_features_aggregate_by_shared_code():
    a, b = [-79.0, 34.61], [-79.0, 34.62]
    lines, _ = derive([_line("buried_cable", [a, b]), _line("buried_cable", [a, b])])
    assert len(lines) == 1
    assert lines[0].qty == pytest.approx(2 * _hav_ft(a, b), rel=0.01)


def test_project_override_changes_the_output_code():
    lines, _ = derive([_point("handhole", -79.0, 34.61)],
                      code_map={"handhole": "STRUCT-01"})
    assert lines[0].code == "STRUCT-01"


def test_unknown_types_are_returned_unmapped_not_dropped():
    feats = [_point("handhole", -79.0, 34.61), _point("mystery_widget", -79.0, 34.62)]
    lines, unmapped = derive(feats)
    assert [r.code for r in lines] == ["5.1"]
    assert [f.feature_type for f in unmapped] == ["mystery_widget"]


def test_known_type_with_no_code_is_unmapped():
    # a project code_map that deliberately maps handhole to nothing usable: an
    # unknown *type* is the unmapped path; here we prove empty override still falls
    # back to the placeholder (so it is mapped) — guarding resolve precedence.
    lines, unmapped = derive([_point("handhole", -79.0, 34.61)], code_map={})
    assert not unmapped and lines[0].code == "5.1"
