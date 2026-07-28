"""GeoJSON in → billable quantities out, in one call. Thin composition of the
importer and the derivation for the recon integration seam."""
from __future__ import annotations

from recon.geo.derive import derive
from recon.geo.importer import load_features
from recon.geo.models import GeoFeature
from recon.models import AsBuiltLine


def derive_geojson(geojson: dict | str, code_map: dict[str, str] | None = None
                   ) -> tuple[list[AsBuiltLine], list[GeoFeature]]:
    """Parse an As-Built Feature GeoJSON and derive quantities. Returns
    ``(lines, unmapped)`` — the AsBuiltLines the engine consumes and the features
    whose type resolved to no code (surfaced by the soft gate)."""
    return derive(load_features(geojson), code_map)
