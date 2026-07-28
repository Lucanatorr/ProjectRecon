"""Render captured features on a folium map, styled by feature type.

Streamlit-free on purpose (only builds the folium object) so it can be unit-tested
headlessly; the As-built step wraps the returned map in st_folium. Feature colors
match the As-Built Mapping mockup.
"""
from __future__ import annotations

import folium

from recon.geo.crosswalk import resolve_code
from recon.geo.models import FEATURE_TYPES, GeoFeature

# feature-type → colour (mirrors the mockup palette)
FEATURE_COLORS: dict[str, str] = {
    "aerial_cable": "#1c5ac4", "aerial_cable_288": "#3b7dd8",
    "buried_cable": "#8a5a34", "conduit_bore": "#e8621e", "drop": "#8592a6",
    "handhole": "#128ea0", "pedestal": "#2e7d57", "splice_closure": "#7c5cd0",
    "pole": "#5c6b80",
}
_UNKNOWN = "#c6362f"        # unknown/unmapped types stand out (they're excluded)
_IMAGERY = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}")


def _latlon(geometry: dict) -> list[tuple[float, float]]:
    """All vertices as (lat, lon) — folium's order (GeoJSON stores lon, lat)."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "Point" and coords:
        return [(coords[1], coords[0])]
    if gtype == "LineString":
        return [(p[1], p[0]) for p in coords]
    if gtype == "MultiLineString":
        return [(p[1], p[0]) for part in coords for p in part]
    return []


def _popup_html(f: GeoFeature, code: str | None) -> str:
    ft = FEATURE_TYPES.get(f.feature_type)
    label = ft.label if ft else f"{f.feature_type} (unknown type)"
    rows = [f'<b>{f.local_id or label}</b>', label,
            f'Contract code: <b>{code or "—"}</b>']
    for k, v in (f.attrs or {}).items():
        rows.append(f'{k}: {v}')
    if f.gps_accuracy_m:
        rows.append(f'GPS ±{f.gps_accuracy_m} m')
    body = "<br>".join(str(r) for r in rows)
    return f'<div style="font-size:12px;line-height:1.5">{body}</div>'


def feature_map(features: list[GeoFeature],
                code_map: dict[str, str] | None = None) -> folium.Map:
    """A folium map of the features over aerial imagery, coloured by type, each with
    a click-to-inspect popup. Bounds fit the data."""
    pts = [p for f in features for p in _latlon(f.geometry)]
    if pts:
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        center = [(min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2]
    else:
        center = [34.61, -79.01]

    m = folium.Map(location=center, zoom_start=15, tiles=None, control_scale=True)
    folium.TileLayer(tiles=_IMAGERY, attr="Esri World Imagery",
                     name="Imagery").add_to(m)

    for f in features:
        code = resolve_code(f.feature_type, code_map)
        colour = FEATURE_COLORS.get(f.feature_type, _UNKNOWN)
        popup = folium.Popup(_popup_html(f, code), max_width=260)
        gtype = f.geometry.get("type")
        latlon = _latlon(f.geometry)
        if gtype == "Point" and latlon:
            folium.CircleMarker(latlon[0], radius=6, color="#ffffff", weight=1.5,
                                fill=True, fill_color=colour, fill_opacity=1.0,
                                popup=popup).add_to(m)
        elif latlon:
            coords = f.geometry.get("coordinates") or []
            parts = [coords] if gtype == "LineString" else coords
            for part in parts:
                folium.PolyLine([(p[1], p[0]) for p in part], color=colour,
                                weight=4, opacity=0.9, popup=popup).add_to(m)

    if len(pts) > 1:
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    return m


def legend_html(features: list[GeoFeature]) -> str:
    """A compact colour legend for the feature types present."""
    chips = []
    for t in dict.fromkeys(f.feature_type for f in features):
        colour = FEATURE_COLORS.get(t, _UNKNOWN)
        ft = FEATURE_TYPES.get(t)
        label = ft.label if ft else f"{t} · unknown"
        chips.append(
            '<span style="display:inline-flex;align-items:center;gap:6px;'
            'font-size:11.5px;color:var(--muted);margin:0 12px 4px 0">'
            f'<span style="width:12px;height:12px;border-radius:3px;'
            f'background:{colour}"></span>{label}</span>')
    return ('<div style="display:flex;flex-wrap:wrap;margin:8px 0 2px">'
            + "".join(chips) + '</div>')
