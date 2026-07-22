# Splice — Contractor Invoice ↔ As-Built Reconciliation Tool

Reconciles fiber-construction contractor invoices against documented as-built
quantities, catching over-billing in **quantity, price, and contract
authorization** before payment goes out. Local-first, single-workstation.

Built from the technical spec (`invoice_reconciliation_spec.md`), the SDD
(`Reconciliation_SDD.md`), and the UI mockup (`reconciliation_mockup.html`).

## Status — Phase 1 MVP

Implemented end-to-end and validated against the mockup's hand-reconciled cycle
(Robeson CAB — PON 5, Cycle 04 → **$35,408 flagged over-billing**, 3 criticals):

- Contract bid-schedule load (xlsx/csv) + change orders
- Tally-sheet (xlsx/csv) as-built ingest with grouping + subtotal-row rejection
- Invoice (xlsx/csv/**zip**) ingest with dedupe on re-upload
- Persistent, learning crosswalk (exact alias → fuzzy `WRatio` → human confirm)
- Reconciliation engine: quantity / price / dollar variance, per-UoM tolerances,
  severity-tagged flags, cumulative-to-date pay-app handling, retainage
- SQLite persistence (projects, contracts, cycles, global aliases, results, audit)
- Excel workbook export (Summary / Flagged / Full detail / Unmatched) + text summary
- Six-step Streamlit wizard mirroring the mockup

**Phase 2 (PDF ingest) — complete, by sprint:**
- ✅ 2.1 Zip ingest for invoices (extract, dispatch, dedupe; skips non-invoice members)
- ✅ 2.2 As-built PDF table extraction (core) — pdfplumber tables → grouped
  `AsBuiltLine`s (`confidence="pdf"`) + `ExtractionReport`; image-only pages flagged
  for OCR (Phase 4), never silently trusted
- ✅ 2.3 PDF as-built → editable grid (UI) — PDF upload dispatches to the extractor,
  low-confidence rows land in an editable review grid with warnings; confirming
  marks them `confidence="confirmed"`; trusted tally keeps the read-only badge table
- ✅ 2.4 PDF invoice parsing (core) — pdfplumber table extraction with column
  auto-detection (amount/price/qty claimed before the generic description so
  "Unit Price" is never mistaken for it); wired into the uploader and zip ingest
- ✅ 2.5 Per-contractor template profiles — `TemplateProfile` (column map + header
  row) persisted per contractor in SQLite; applied automatically to that
  contractor's PDF invoices, with a column-mapping panel to set one up when columns
  don't auto-detect

**Phase 3 (progress billing) — in progress, by sprint:**
- ✅ 3.1 Change orders — a CO schedule extends/revises the contract (`is_change_order`);
  CO-authorized units clear the unauthorized/over-price flags and show a CO marker on
  the reconciliation row
- ✅ 3.2 Retainage — first-class gross → retainage → net; a `check_retainage`
  validates the retainage actually withheld on the invoice against the contract
  rate (over/under-withheld warning), surfaced on the reconcile dashboard and the
  export summary
- ✅ 3.3 Multi-cycle persistence — the Export step saves a finalized cycle (project +
  contract + cycle metadata + per-unit results) to SQLite, idempotent per
  (project, cycle_no); a "Saved cycles" table loads prior cycles back, surviving
  session reloads
- ⬜ 3.4 Current-vs-prior validation (cumulative pay-app check)
- ⬜ 3.5 Built-to-date vs billed-to-date trend

Later phases (OCR for scanned PDFs, PDF approval packet, reviewer sign-off) are
scaffolded in the design but not yet built.

## Architecture

A pure-Python domain core (`recon/`) with **no Streamlit imports** — reusable
from a CLI, tests, or a future service — under a Streamlit presentation layer
(`ui/`, `app.py`) and SQLite persistence.

```
recon/            domain core (pure Python)
  models.py         dataclasses: ContractItem, AsBuiltLine, InvoiceLine, ReconRow, Flag
  ingest/           normalize, tally, invoices  (asbuilt_pdf → Phase 2)
  contract.py       bid schedule + change orders
  crosswalk.py      alias store + rapidfuzz matcher
  reconcile.py      aggregate → deltas → flags → cycle totals
  report.py         Excel / text export
  persistence.py    SQLite CRUD + audit
ui/               Streamlit wizard, one module per step
app.py            entry point + step router
config.py         tolerances, matching threshold, retainage, paths
samples/          demo data generator (the mockup scenario)
tests/            unit + golden-file + integration + UI-flow tests
```

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux
```

## Run

```bash
.venv/Scripts/python.exe -m streamlit run app.py
```

Then click **Load sample** on each of the Contract, As-built, and Invoices steps
to walk the Robeson CAB demo, confirm the two crosswalk items, and review the
reconciliation. Regenerate the sample files with:

```bash
.venv/Scripts/python.exe samples/generate_samples.py
```

## Test

```bash
.venv/Scripts/python.exe -m pytest -q
```

The golden-file test (`tests/test_reconcile_golden.py`) encodes the mockup's
worked example as a known answer and is the primary guard against logic drift.

## Key design decisions

- **WRatio scorer** for the crosswalk — robust to extra/missing words and word
  order, which is how invoice vs bid descriptions actually differ.
- **Unmatched lines stay separate per description** — distinct unauthorized units
  are never merged under a single `None` code (which the cumulative max-qty
  reduction would otherwise silently collapse).
- **Input-fingerprint invalidation** — reconciliation results recompute whenever
  contract, as-built, invoices, or crosswalk mappings change, so the dashboard and
  export are never stale regardless of navigation order.
- **Over-run is flagged whenever qty exceeds the bid estimate** (per SDD §7.3),
  even when quantity and price otherwise reconcile.

## UI fidelity

The interface reproduces `reconciliation_mockup.html` closely: the mockup's CSS is
injected wholesale and Streamlit's chrome is hidden, with the sidebar stepper, top
bar, KPI tiles, filter chips, built-vs-billed rows, cards/tables, crosswalk cards,
and export tabs all rendered as the mockup's own HTML. Reconciliation rows use
native `<details>`/`<summary>` for click-to-expand (Streamlit strips `<script>`).

Navigation is via query-param links (`?step=…`). Because a link click reloads the
page and would wipe `st.session_state`, wizard state is held in a process-global
`st.cache_resource` store keyed by a URL session id (`?sid=…`), so it survives the
reload. `.streamlit/config.toml` sets the theme primary color to the mockup blue so
native widgets (segmented control, focus rings) match.

Append `?demo=1` to the URL to preload the full Robeson CAB walkthrough
(contract + as-built + invoices + confirmed crosswalk) in one shot.
