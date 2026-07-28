# Splice — Field As-Built Mapping (Spec)

Status: **draft for review** · Author: coordinator + Claude · Companion mockup:
the As-Built Mapping artifact.

## 1. Purpose & scope

A second, adjacent tool that records field as-builts as **map geometry** (points
and lines) and derives **billable quantities by contract code**, which feed the
existing reconciliation engine as a cycle's as-built — replacing the tally / PDF /
OCR ingest with structured, location-traceable data.

Two phases, deliberately sequenced:

- **Phase A — Desktop (this spec's target).** Runs locally like the recon app.
  Import existing geodata and/or place features on a map at a desk. Prove the
  geometry → quantity → reconciliation loop on the cheap stack.
- **Phase B — Field (later).** A separate offline PWA on a phone/tablet that
  **emits the exact same exchange format** defined here, so everything downstream
  is already built and trusted. Nothing in Phase A is throwaway.

Non-goals (Phase A): offline map tiles, live GPS capture, survey-grade accuracy,
multi-crew sync. Those belong to Phase B and are explicitly deferred.

The reconciliation engine (`recon/reconcile.py`) is **not touched**. This tool
produces `AsBuiltLine`s — the same contract every other ingest path already emits.

## 2. The durable core: the As-Built Feature exchange format

The one thing that must be right now, because Phase B inherits it. A **GeoJSON
`FeatureCollection`** in **WGS84 / EPSG:4326** (lon, lat — GeoJSON's only CRS).
Every feature carries a small, typed property envelope:

```jsonc
{
  "type": "Feature",
  "geometry": { "type": "Point", "coordinates": [-79.0142, 34.6106] },
  "properties": {
    "feature_type": "handhole",        // enum, drives the code mapping (§3)
    "local_id": "HH-014",              // stable per capturing device
    "attrs": { "size": "30x48", "depth_in": 36 },   // type-specific, free-form
    "captured_by": "Rivr Tech",
    "captured_at": "2026-07-27T14:02:11Z",
    "source": "field-pwa|desk-draw|import",
    "gps_accuracy_m": 3.1,             // nullable (desk-drawn features have none)
    "photos": ["hh-014-a.jpg"]         // refs only; blobs handled separately
  }
}
```

Geometry kinds accepted: `Point` (fixtures) and `LineString` / `MultiLineString`
(runs). `Polygon` is reserved (service areas) and ignored by quantity derivation
for now.

Rules:
- **CRS is always 4326.** Imports in any other CRS are reprojected to 4326 on the
  way in; we never store projected coordinates.
- `feature_type` is the only required property besides geometry. Everything else
  degrades gracefully (missing `attrs`, `gps_accuracy_m`, `photos` are fine).
- `local_id` is unique per device, not globally. Cross-device merge (Phase B) keys
  on `(source_device, local_id)`; Phase A has one device so it's trivially unique.

This profile is the **contract between the two tools**. Phase B's only obligation
is to emit it.

## 3. Feature-type registry

A fixed vocabulary of fiber feature types, each with a geometry kind, a default
unit of measure, and a *default* contract-code mapping (overridable per project in
§4). Codes below match the demo bid schedule.

| `feature_type`     | Geometry | UoM | Default code | Contract description        |
|--------------------|----------|-----|--------------|-----------------------------|
| `aerial_cable`     | line     | FT  | 3.1          | Place 144ct ADSS aerial     |
| `aerial_cable_288` | line     | FT  | 3.2          | Place 288ct ADSS aerial     |
| `buried_cable`     | line     | FT  | 4.2          | Trench / plow fiber         |
| `conduit_bore`     | line     | FT  | 4.1          | Directional bore 2"         |
| `drop`             | line     | EA  | 9.1          | Drop placement              |
| `handhole`         | point    | EA  | 5.1          | Handhole 30×48              |
| `pedestal`         | point    | EA  | 5.2          | Fiber pedestal              |
| `splice_closure`   | point    | EA  | 6.2          | Splice closure              |
| `pole`             | point    | EA  | 8.1          | Pole make-ready             |

Notes:
- `drop` is a line geometry but bills **per each** (count), not by length — the
  registry decides length-vs-count, not the geometry kind.
- The registry ships as data (a table/dict), not hard-coded logic, so adding a
  feature type is a one-line change.

## 4. Type → contract-code crosswalk

The structured analogue of the description crosswalk you already built — but far
easier, because features are *typed*, not fuzzy text.

- The registry's default code is a starting point; the actual mapping is **per
  project** and persisted (a project uses `3.1` where another uses `3.2`).
- A feature whose `feature_type` has **no code mapping** for this project is
  **unmapped** and excluded from quantities until resolved (see §6, Decision D2).
- Imported data with unknown `feature_type` strings surfaces in the same
  unmapped bucket, with a one-click "map this type → code" that persists globally
  (mirrors `alias` / FR-7 in the recon tool).

## 5. Geometry → quantity derivation

The heart of the tool, and the part that must be unit-tested against golden values.

**Lines (length-billed types):**
- Length is **geodesic**, computed with `pyproj.Geod(ellps="WGS84")` directly on
  the 4326 coordinates (no projection step) → meters → **feet** (× 3.280839895).
- `MultiLineString` sums its parts. A feature's length is the sum of its segments.
- Quantities aggregate **by contract code**: all features mapping to `4.2` sum into
  one `AsBuiltLine`.

**Points / count-billed types (incl. `drop`):**
- Quantity = **count** of features mapping to that code.

**Output:** a `list[AsBuiltLine]`, one per code, e.g.
`AsBuiltLine(code="4.2", raw_desc="Trench / plow fiber", qty=2640.0, uom=UoM.FT,
source_ref="geo:12 features", confidence="geo")`.
- `uom` is the contract code's UoM when the project's contract is available
  (`load_saved_contract`), else the registry default. The engine already
  normalizes `100FT`↔`FT`, so emitting feet is safe even for per-100FT codes.
- `confidence="geo"` is a new source tag alongside the existing `sum|pdf|ocr`.
- Displayed quantities are rounded (feet to whole, per existing report style).

**Overlap / double-count (known risk):** two imported layers can cover the same
span. Phase A ships a simple **exact-duplicate drop** (identical geometry) and
flags *near-duplicates* for review rather than silently merging. Topological
de-overlap is deferred.

## 6. Decisions for sign-off

Three flow decisions from the mockup review. My recommendation is first; each is
reversible.

- **D1 — Group derived quantities by contract code (recommended) vs by feature
  type.** Code-first matches the bid schedule and the recon mental model, and it's
  the shape the engine consumes. Feature-type grouping stays available as a
  secondary view. → *Recommend: code-first.*
- **D2 — Unmapped features: soft gate (recommended) vs hard block.** Field data is
  messy; hard-blocking traps the user. But silently dropping features under-counts
  quantities (→ under-billing risk). Recommend a **soft gate**: allow "Send," but
  require an explicit acknowledgment of *"N features excluded"*, logged to the
  audit trail — the same pattern as the export-gate override you already built.
  → *Recommend: soft gate with logged acknowledgment.*
- **D3 — Handoff: file-based exchange (recommended) with an optional direct push.**
  The primary path is a **derived-quantities import** in the recon tool ("As-built
  source: from map / geodata"), fed by a file the geo tool writes. This keeps the
  two tools decoupled and fits the local/cloned/sneakernet model — and it's exactly
  how Phase B (separate device) will hand off. When both run on the same machine,
  a direct "Send to Cycle N" is a convenience wrapper over the same import.
  → *Recommend: build the file-based import seam first; direct push as sugar.*

## 7. Domain module structure

Mirrors `recon/ingest/*` — pure, UI-free, tested. No Streamlit imports.

```
recon/geo/
  models.py      GeoFeature dataclass; FEATURE_TYPES registry (type → kind, UoM, default code)
  importer.py    parse a GeoJSON FeatureCollection → list[GeoFeature]; CRS reproject to 4326
  crosswalk.py   per-project feature_type → code map; unmapped detection
  derive.py      list[GeoFeature] + type→code map (+ optional contract) → list[AsBuiltLine]
```

Persistence additions (existing migration pattern in `recon/persistence.py`):
- `geo_feature(id, project_id, cycle_id?, feature_type, geometry_json, attrs_json,
  captured_by, captured_at, source, gps_accuracy_m, local_id, created_at)`
- `feature_code_map(project_id, feature_type, code, PRIMARY KEY(project_id,
  feature_type))` — the per-project crosswalk.
- Geometry stored as **GeoJSON text** (no SpatiaLite in Phase A); lengths computed
  in Python with `pyproj`.

## 8. Recon integration seam

The as-built step gains a source: **"From map / geodata."** It calls
`derive()` to get `AsBuiltLine`s and drops them into `state.asbuilt`, exactly like
`parse_tally` does today. Everything after — crosswalk (near-automatic here),
reconcile, gates, export — is unchanged. `save_cycle_snapshot` optionally records
the contributing `geo_feature`s against the cycle for traceability.

## 9. Dependencies

Keep the surface thin to preserve "clone and run locally":
- **`shapely`** (geometry) + **`pyproj`** (geodesic length, reprojection) only.
- **Avoid `geopandas` / `fiona` / GDAL** in Phase A — GeoJSON parses with the stdlib
  `json` + `shapely.geometry.shape`. Add heavier format support (GeoPackage,
  Shapefile) only when a real file forces it.
- Map rendering (later sprints): `streamlit-folium` (Leaflet) for view + draw, or
  `pydeck`. Online tiles only in Phase A (desk has internet).

## 10. Sprint plan

- **Geo-0 (domain only, tested).** `GeoFeature` + registry; GeoJSON import →
  features; `derive()` → `AsBuiltLine`s with golden tests (known route → known
  footage; N points → N each; MultiLineString sums; unmapped excluded). No UI.
- **Geo-1 (integration).** Per-project type→code crosswalk + persistence; the
  recon "as-built from geodata" import seam (D3 file path); a basic import +
  derived-quantities review screen. Round-trips a real GeoJSON into a cycle.
- **Geo-2 (map UI).** The mockup's map view: layers, feature select/detail, the
  quantity panel, the unmapped soft-gate (D2), draw/place features.
- **Phase B (separate track, later).** Offline PWA (MapLibre + IndexedDB) emitting
  the §2 format; import merge keyed on `(device, local_id)`.

## 11. Open risks

- **CRS mishaps on import** — the top source of "everything is in the ocean" bugs;
  reproject-to-4326 on ingest and validate coordinate bounds.
- **Overlapping geometry** double-counting footage — mitigated (§5), not solved.
- **Messy real-world schemas** — every crew/tool names layers differently; the
  type→code crosswalk (§4) is the pressure-relief valve.
- **Deferred to Phase B:** offline tiles + imagery licensing, GPS accuracy (relaxed
  for quantity purposes — 3–5 m is fine for counts/lengths), cross-device merge.
