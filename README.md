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
- Persistent, learning crosswalk matching on **both contract columns** — exact alias
  → exact code → best fuzzy (`WRatio`) of code vs description → human confirm
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

**Phase 3 (progress billing) — complete, by sprint:**
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
- ✅ 3.4 Current-vs-prior validation — in cumulative mode the engine loads the prior
  saved cycle's per-unit billed-to-date, computes each unit's current-period quantity
  (to-date − prior), and warns when billed-to-date falls below the prior cycle (a
  cumulative pay app should not decrease); shown per-row in the drill-down
- ✅ 3.5 Built-to-date vs billed-to-date trend — the Export step charts each saved
  cycle with the same dual-bar language as the reconciliation rows, so a widening
  gap (billing outpacing documented work) is visible across the job

**Phase 4 (hardening) — in progress, by sprint:**
- ✅ 4.1 PDF approval summary — a real one-page branded PDF (reportlab): headline
  metrics, payment recommendation, flagged-items table, and a sign-off block
- ✅ 4.2 Flag resolution & reviewer sign-off — hold / approve / annotate any flagged
  row from its drill-down; decisions are stamped with reviewer + time, gate the
  sign-off ("N critical still need a decision"), persist with the cycle, and appear
  in the PDF packet
- ✅ 4.3 Validation gates + logged override — a pre-export checklist (bid schedule,
  extracted quantities confirmed, crosswalk resolved, critical flags decided) gates
  the downloads; an explicit override requires a reason, is written to the audit log,
  and is stamped on both the PDF and the workbook
- ✅ 4.4 Audit trail — every ingest, mapping, resolution, override, and export is
  recorded with actor + timestamp and surfaced in an Export-step viewer. Also
  completes FR-7: confirmed crosswalk mappings now persist **globally** in SQLite,
  so the crosswalk gets smarter across jobs instead of forgetting on restart
- ✅ 4.5 OCR for scanned PDFs — image-only pages are rendered and OCR'd, the
  recognised words are reassembled into a table, and rows land as
  `confidence="ocr"` so a human must confirm them. Needs the Tesseract binary; when
  it's missing the extractor degrades gracefully with an install hint (see below)
- ✅ 4.6 Packaging & docs — one-command launchers (`run.ps1` / `run.sh`), a pinned
  `requirements.lock.txt`, a safe backup/restore tool, and configuration,
  operations, and troubleshooting documentation

## Scanned PDFs (OCR)

Text-based PDFs need nothing extra. **Scanned/image-only** as-builts additionally
need the Tesseract binary (page rendering uses `pypdfium2`, which ships with
pdfplumber — no Poppler required).

```powershell
winget install UB-Mannheim.TesseractOCR
```

Then reopen your terminal and check it's on PATH:

```powershell
tesseract --version
```

If PATH didn't pick it up, either add `C:\Program Files\Tesseract-OCR` to PATH, or
point the app at the binary in `recon/ingest/ocr.py`:

```python
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

Verify with the bundled scanned fixture — "Load scan" on the As-built step, or:

```bash
.venv/Scripts/python.exe -m pytest tests/test_ocr.py -v
```

The two OCR tests skip without Tesseract and run once it's installed. OCR output is
always tagged `OCR · verify` and routed through the editable grid — never trusted
without a human confirming it.

## Architecture

A pure-Python domain core (`recon/`) with **no Streamlit imports** — reusable
from a CLI, tests, or a future service — under a Streamlit presentation layer
(`ui/`, `app.py`) and SQLite persistence.

```
recon/                  domain core (pure Python, no Streamlit)
  models.py               ContractItem, AsBuiltLine, InvoiceLine, ReconRow, Flag,
                          TemplateProfile, UoM, Severity
  contract.py             bid schedule + change orders
  crosswalk.py            alias store + rapidfuzz matcher (code AND description)
  reconcile.py            aggregate → deltas → flags → totals → retainage check
  report.py               Excel workbook + PDF approval summary
  persistence.py          SQLite CRUD, cycles, aliases, audit, migrations
  ingest/
    normalize.py            string + UoM canonicalization
    tally.py                xlsx/csv as-built
    asbuilt_pdf.py          pdfplumber extraction + ExtractionReport
    invoices.py             xlsx/csv/pdf/zip dispatch + dedupe
    invoice_pdf.py          PDF invoices + per-contractor templates
    ocr.py                  scanned-page OCR fallback
ui/                     Streamlit presentation layer
  state.py                wizard state, resolutions, fingerprint
  gates.py                pre-export validation gates
  theme.py                mockup CSS + HTML builders
  db.py  progress.py  uploads.py
  step_*.py               one module per wizard step
app.py                  entry point + step router
config.py               tolerances, matching, retainage, OCR, paths
tools/backup.py         database backup / restore
samples/                demo + fixture generator
tests/                  unit · golden-file · integration · UI-flow
```

## Quick start

The launcher creates the virtual environment, installs dependencies, and starts
the app — nothing else to set up:

```powershell
.\run.ps1
```

```bash
./run.sh
```

Then open **http://localhost:8501**. Append `?demo=1` to preload the full Robeson
CAB walkthrough, or click **Load sample** on the Contract, As-built, and Invoices
steps and work through it yourself.

<details>
<summary>Manual setup, if you'd rather not use the launcher</summary>

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt      # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux
.venv/Scripts/python.exe -m streamlit run app.py
```
</details>

`requirements.txt` holds the supported ranges; `requirements.lock.txt` pins the
exact versions this build was verified against — install from the lock file for a
reproducible deployment.

## Test

```powershell
.\run.ps1 -Test
```

```bash
.venv/Scripts/python.exe -m pytest -q
```

The golden-file test (`tests/test_reconcile_golden.py`) encodes the mockup's
worked example as a known answer and is the primary guard against logic drift.
Regenerate the sample/fixture files with `python samples/generate_samples.py`.

## Configuration

Tuning lives in `config.py`:

| Setting | Default | Effect |
|---|---|---|
| `ToleranceConfig.ft_abs` / `ft_pct` | 50 ft / 2% | Measured-unit tolerance — `max(abs, pct × built)` |
| `ToleranceConfig.ea_abs` | 0 | Counted units must match exactly |
| `MatchingConfig.auto_threshold` | 90 | Fuzzy score at or above which the crosswalk auto-maps |
| `MatchingConfig.scorer` | `WRatio` | rapidfuzz scorer |
| `ReconConfig.retainage_default_pct` | 10 | Default contract retainage |
| `OcrConfig.enabled` / `resolution` / `min_confidence` | true / 300 / 40 | Scanned-PDF OCR |

Environment:

| Variable | Default | Effect |
|---|---|---|
| `SPLICE_DB_PATH` | `./data/recon.db` | Database location — point it at backed-up storage |

Theme and server options are in `.streamlit/config.toml`.

## Operations

**Back up the database.** Everything durable — projects, contracts, saved cycles,
results, the global crosswalk aliases, per-contractor templates, and the audit log
— lives in `data/recon.db`. Snapshot it on your normal backup schedule:

```bash
python tools/backup.py backup            # -> backups/recon-YYYYmmdd-HHMMSS.db
python tools/backup.py list
python tools/backup.py restore backups/recon-20260723-191209.db
```

This uses SQLite's online-backup API, so a snapshot taken while the app is running
is still internally consistent — safer than copying the file. A restore keeps the
database it replaces as `data/recon.pre-restore.db` before overwriting.

**Upgrades are code-only.** Pull, reinstall dependencies (`.\run.ps1 -Update`), and
restart. The schema self-migrates on open: missing columns are added forward, so an
older database keeps working. Back up first anyway.

**Routine tasks.** Update a contractor's invoice template when their format changes
(Invoices → *Column mapping*); the audit log grows slowly and can be trimmed by
deleting old `audit_log` rows if it ever needs it.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Scanned PDF reads as 0 rows | Tesseract isn't installed — see [Scanned PDFs (OCR)](#scanned-pdfs-ocr). The app says so in a warning. |
| Downloads are greyed out on Export | Pre-export checks haven't passed. Fix the listed items, or record an explicit override. |
| A description keeps coming back for review | Below the auto-map threshold. Confirm it once — it's saved globally and reused on later jobs. |
| Port 8501 already in use | `.\run.ps1 -Port 8502` (or `./run.sh --port=8502`). |
| Numbers look stale after editing inputs | Results recompute from an input fingerprint; if in doubt, revisit Reconciliation. |

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
