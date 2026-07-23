"""SQLite persistence — projects, contracts, cycles, the global alias crosswalk,
results, and audit log. Schema per SDD §6.2.

The domain core stays UI-free; this module is the only place that touches sqlite3.
The alias table is global (spans projects) so the crosswalk improves across jobs.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import DB_PATH
from recon.crosswalk import AliasStore
from recon.ingest.normalize import normalize
from recon.models import ContractItem, ReconRow, TemplateProfile, UoM

SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  contractor    TEXT,
  area          TEXT,
  status        TEXT DEFAULT 'active',
  created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contract_item (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id),
  code          TEXT NOT NULL,
  description   TEXT NOT NULL,
  uom           TEXT NOT NULL,
  unit_price    REAL NOT NULL,
  est_qty       REAL,
  is_change_order INTEGER DEFAULT 0,
  effective_date TEXT,
  UNIQUE(project_id, code, effective_date)
);

CREATE TABLE IF NOT EXISTS billing_cycle (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id),
  cycle_no      INTEGER NOT NULL,
  period_label  TEXT,
  billing_mode  TEXT CHECK(billing_mode IN ('cumulative','discrete')),
  retainage_pct REAL DEFAULT 0,
  prior_billed_to_date REAL DEFAULT 0,
  status        TEXT DEFAULT 'open',
  created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS asbuilt_line (
  id            INTEGER PRIMARY KEY,
  cycle_id      INTEGER NOT NULL REFERENCES billing_cycle(id),
  raw_desc      TEXT NOT NULL,
  qty           REAL NOT NULL,
  uom           TEXT,
  segment       TEXT,
  code          TEXT,
  source_file   TEXT,
  source_ref    TEXT,
  confidence    TEXT DEFAULT 'sum'
);

CREATE TABLE IF NOT EXISTS invoice_line (
  id            INTEGER PRIMARY KEY,
  cycle_id      INTEGER NOT NULL REFERENCES billing_cycle(id),
  invoice_id    TEXT,
  raw_desc      TEXT NOT NULL,
  qty           REAL NOT NULL,
  unit_price    REAL,
  amount        REAL,
  period        TEXT,
  is_cumulative INTEGER DEFAULT 1,
  code          TEXT,
  source_file   TEXT,
  line_ref      TEXT
);

CREATE TABLE IF NOT EXISTS alias (
  id            INTEGER PRIMARY KEY,
  normalized_desc TEXT UNIQUE NOT NULL,
  code          TEXT NOT NULL,
  confidence    REAL,
  confirmed_by  TEXT,
  confirmed_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recon_result (
  id            INTEGER PRIMARY KEY,
  cycle_id      INTEGER NOT NULL REFERENCES billing_cycle(id),
  code          TEXT,
  description   TEXT,
  uom           TEXT,
  built_qty     REAL,
  billed_qty    REAL,
  contract_price REAL,
  billed_price  REAL,
  est_qty       REAL,
  billed_amount REAL,
  expected_amount REAL,
  variance      REAL,
  severity      TEXT,
  flags_json    TEXT,
  resolution    TEXT,                 -- hold | approve | note
  resolution_note TEXT,               -- reviewer's reason / annotation
  resolved_by   TEXT,
  resolved_at   TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id        INTEGER PRIMARY KEY,
  ts        TEXT DEFAULT (datetime('now')),
  actor     TEXT,
  action    TEXT,
  entity    TEXT,
  entity_id INTEGER,
  detail_json TEXT
);

CREATE TABLE IF NOT EXISTS template_profile (   -- per-contractor PDF invoice layout
  id            INTEGER PRIMARY KEY,
  contractor    TEXT UNIQUE NOT NULL,
  header_row    INTEGER DEFAULT 0,
  table_index   INTEGER DEFAULT 0,
  columns_json  TEXT NOT NULL,
  updated_at    TEXT DEFAULT (datetime('now'))
);
"""


class Database:
    """Thin transactional wrapper over a SQLite file."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DB_PATH
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    # Lightweight forward migrations: CREATE TABLE IF NOT EXISTS won't add columns
    # to a database created by an earlier version, so top them up here.
    _ADDED_COLUMNS = {
        "recon_result": {"resolution_note": "TEXT"},
    }

    def _migrate(self) -> None:
        for table, columns in self._ADDED_COLUMNS.items():
            existing = {r["name"] for r in
                        self._conn.execute(f"PRAGMA table_info({table})")}
            if not existing:                     # table absent entirely
                continue
            for col, decl in columns.items():
                if col not in existing:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    @contextmanager
    def tx(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()

    # --- projects ---
    def create_project(self, name: str, contractor: str | None = None,
                       area: str | None = None) -> int:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO project(name, contractor, area) VALUES (?,?,?)",
                (name, contractor, area),
            )
            return cur.lastrowid

    def list_projects(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM project ORDER BY created_at DESC").fetchall()

    def project_by_name(self, name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM project WHERE name=? ORDER BY id DESC LIMIT 1",
            (name,)).fetchone()

    def get_or_create_project(self, name: str, contractor: str | None = None,
                             area: str | None = None) -> int:
        row = self.project_by_name(name)
        return row["id"] if row else self.create_project(name, contractor, area)

    # --- contract items ---
    def save_contract(self, project_id: int, items: list[ContractItem]) -> None:
        with self.tx() as cur:
            cur.execute("DELETE FROM contract_item WHERE project_id=?", (project_id,))
            for ci in items:
                cur.execute(
                    """INSERT OR REPLACE INTO contract_item
                       (project_id, code, description, uom, unit_price, est_qty,
                        is_change_order, effective_date)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (project_id, ci.code, ci.description, ci.uom.value,
                     ci.unit_price, ci.est_qty, int(ci.is_change_order),
                     ci.effective_date),
                )

    def load_contract(self, project_id: int) -> list[ContractItem]:
        rows = self._conn.execute(
            "SELECT * FROM contract_item WHERE project_id=? ORDER BY code",
            (project_id,)).fetchall()
        return [
            ContractItem(
                code=r["code"], description=r["description"],
                uom=UoM.from_str(r["uom"]) or UoM.EA,
                unit_price=r["unit_price"], est_qty=r["est_qty"] or 0.0,
                is_change_order=bool(r["is_change_order"]),
                effective_date=r["effective_date"],
            ) for r in rows
        ]

    # --- billing cycles ---
    def create_cycle(self, project_id: int, cycle_no: int, *, period_label: str | None,
                    billing_mode: str, retainage_pct: float,
                    prior_billed_to_date: float = 0.0) -> int:
        with self.tx() as cur:
            cur.execute(
                """INSERT INTO billing_cycle
                   (project_id, cycle_no, period_label, billing_mode,
                    retainage_pct, prior_billed_to_date)
                   VALUES (?,?,?,?,?,?)""",
                (project_id, cycle_no, period_label, billing_mode,
                 retainage_pct, prior_billed_to_date),
            )
            return cur.lastrowid

    def list_cycles(self, project_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM billing_cycle WHERE project_id=? ORDER BY cycle_no",
            (project_id,)).fetchall()

    def get_or_create_cycle(self, project_id: int, cycle_no: int, *,
                           period_label: str | None, billing_mode: str,
                           retainage_pct: float,
                           prior_billed_to_date: float = 0.0) -> int:
        """Return the cycle id for (project, cycle_no), updating its metadata if it
        already exists so re-saving a cycle overwrites rather than duplicates."""
        row = self._conn.execute(
            "SELECT id FROM billing_cycle WHERE project_id=? AND cycle_no=?",
            (project_id, cycle_no)).fetchone()
        if row is None:
            return self.create_cycle(
                project_id, cycle_no, period_label=period_label,
                billing_mode=billing_mode, retainage_pct=retainage_pct,
                prior_billed_to_date=prior_billed_to_date)
        with self.tx() as cur:
            cur.execute(
                """UPDATE billing_cycle SET period_label=?, billing_mode=?,
                       retainage_pct=?, prior_billed_to_date=? WHERE id=?""",
                (period_label, billing_mode, retainage_pct, prior_billed_to_date,
                 row["id"]))
        return row["id"]

    # --- alias crosswalk (global) ---
    def load_alias_store(self) -> AliasStore:
        rows = self._conn.execute(
            "SELECT normalized_desc, code FROM alias").fetchall()
        return AliasStore({r["normalized_desc"]: r["code"] for r in rows})

    def confirm_alias(self, raw_desc: str, code: str, *,
                     confidence: float | None = None, actor: str | None = None) -> None:
        key = normalize(raw_desc)
        with self.tx() as cur:
            cur.execute(
                """INSERT INTO alias(normalized_desc, code, confidence, confirmed_by)
                   VALUES (?,?,?,?)
                   ON CONFLICT(normalized_desc)
                   DO UPDATE SET code=excluded.code, confidence=excluded.confidence,
                                 confirmed_by=excluded.confirmed_by,
                                 confirmed_at=datetime('now')""",
                (key, code, confidence, actor),
            )
        self.log(actor, "confirm_alias", "alias", detail={"desc": raw_desc, "code": code})

    def delete_alias(self, raw_desc: str, actor: str | None = None) -> None:
        """Forget a learned mapping (the coordinator re-opened it for review)."""
        key = normalize(raw_desc)
        with self.tx() as cur:
            cur.execute("DELETE FROM alias WHERE normalized_desc=?", (key,))
        self.log(actor, "delete_alias", "alias", detail={"desc": raw_desc})

    # --- template profiles (global, per contractor) ---
    def save_template_profile(self, profile: TemplateProfile,
                             actor: str | None = None) -> None:
        with self.tx() as cur:
            cur.execute(
                """INSERT INTO template_profile
                       (contractor, header_row, table_index, columns_json, updated_at)
                   VALUES (?,?,?,?,datetime('now'))
                   ON CONFLICT(contractor) DO UPDATE SET
                       header_row=excluded.header_row,
                       table_index=excluded.table_index,
                       columns_json=excluded.columns_json,
                       updated_at=datetime('now')""",
                (profile.contractor, profile.header_row, profile.table_index,
                 json.dumps(profile.columns)),
            )
        self.log(actor, "save_template_profile", "template_profile",
                 detail={"contractor": profile.contractor})

    def load_template_profile(self, contractor: str) -> TemplateProfile | None:
        if not contractor:
            return None
        row = self._conn.execute(
            "SELECT * FROM template_profile WHERE contractor=?", (contractor,)).fetchone()
        if row is None:
            return None
        return TemplateProfile(
            contractor=row["contractor"],
            columns={k: int(v) for k, v in json.loads(row["columns_json"]).items()
                     if v is not None},
            header_row=row["header_row"] or 0,
            table_index=row["table_index"] or 0,
        )

    def list_template_profiles(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT contractor FROM template_profile ORDER BY contractor").fetchall()
        return [r["contractor"] for r in rows]

    def delete_template_profile(self, contractor: str) -> None:
        with self.tx() as cur:
            cur.execute("DELETE FROM template_profile WHERE contractor=?", (contractor,))
        self.log(None, "delete_template_profile", "template_profile",
                 detail={"contractor": contractor})

    # --- results ---
    def save_results(self, cycle_id: int, rows: list[ReconRow],
                    resolutions: dict | None = None) -> None:
        """Persist the cycle's rows. ``resolutions`` maps row key (code, or the
        description for unmatched rows) → {status, note, by, at}."""
        resolutions = resolutions or {}
        with self.tx() as cur:
            cur.execute("DELETE FROM recon_result WHERE cycle_id=?", (cycle_id,))
            for r in rows:
                res = resolutions.get(r.code or r.description) or {}
                cur.execute(
                    """INSERT INTO recon_result
                       (cycle_id, code, description, uom, built_qty, billed_qty,
                        contract_price, billed_price, est_qty, billed_amount,
                        expected_amount, variance, severity, flags_json,
                        resolution, resolution_note, resolved_by, resolved_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cycle_id, r.code, r.description, r.uom.value, r.built_qty,
                     r.billed_qty, r.contract_price, r.billed_price, r.est_qty,
                     r.billed_amount, r.expected_amount, r.amount_variance,
                     r.severity.value,
                     json.dumps([{"rule": f.rule, "severity": f.severity.value,
                                  "message": f.message} for f in r.flags]),
                     res.get("status"), res.get("note") or None,
                     res.get("by") or None, res.get("at")),
                )

    def load_results(self, cycle_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM recon_result WHERE cycle_id=? ORDER BY code",
            (cycle_id,)).fetchall()

    def cycle_summary(self, cycle_id: int) -> dict:
        """Headline numbers for a saved cycle, recomputed from its stored results."""
        bc = self._conn.execute(
            "SELECT * FROM billing_cycle WHERE id=?", (cycle_id,)).fetchone()
        rows = self.load_results(cycle_id)
        billed = sum((r["billed_amount"] or 0) for r in rows)
        expected = sum((r["expected_amount"] or 0) for r in rows)
        flagged = sum((r["variance"] or 0) for r in rows
                      if r["severity"] == "critical" and (r["variance"] or 0) > 0)
        n_crit = sum(1 for r in rows if r["severity"] == "critical")
        pct = bc["retainage_pct"] or 0
        retainage = billed * pct / 100.0
        return {
            "cycle_id": cycle_id, "cycle_no": bc["cycle_no"],
            "period_label": bc["period_label"], "billing_mode": bc["billing_mode"],
            "billed": billed, "expected": expected, "flagged": flagged,
            "n_critical": n_crit, "retainage": retainage,
            "net": billed - flagged - retainage, "n_units": len(rows),
            "created_at": bc["created_at"],
        }

    def cycle_summaries(self, project_id: int) -> list[dict]:
        return [self.cycle_summary(c["id"]) for c in self.list_cycles(project_id)]

    def prior_billed_by_code(self, project_id: int, before_cycle_no: int) -> dict[str, float]:
        """Per-unit billed-to-date from the most recent saved cycle before
        ``before_cycle_no`` — the prior cumulative for the current-vs-prior check."""
        row = self._conn.execute(
            """SELECT id FROM billing_cycle WHERE project_id=? AND cycle_no < ?
               ORDER BY cycle_no DESC LIMIT 1""",
            (project_id, before_cycle_no)).fetchone()
        if row is None:
            return {}
        return {r["code"]: (r["billed_qty"] or 0.0)
                for r in self.load_results(row["id"]) if r["code"]}

    def save_cycle_snapshot(self, *, project_name: str, contractor: str | None,
                           area: str | None, cycle_no: int, period_label: str | None,
                           billing_mode: str, retainage_pct: float,
                           prior_billed: float, contract_items: list[ContractItem],
                           rows: list[ReconRow], actor: str | None = None,
                           resolutions: dict | None = None) -> tuple[int, int]:
        """Persist a finalized cycle: project + contract + cycle metadata + results.
        Idempotent per (project, cycle_no) — re-saving overwrites."""
        pid = self.get_or_create_project(project_name, contractor, area)
        self.save_contract(pid, contract_items)
        cid = self.get_or_create_cycle(
            pid, cycle_no, period_label=period_label, billing_mode=billing_mode,
            retainage_pct=retainage_pct, prior_billed_to_date=prior_billed)
        self.save_results(cid, rows, resolutions)
        self.log(actor, "save_cycle", "billing_cycle", cid,
                 {"project": project_name, "cycle_no": cycle_no, "n_units": len(rows),
                  "n_resolved": len(resolutions or {})})
        return pid, cid

    def trend(self, project_id: int) -> list[dict]:
        """Built-to-date vs billed-to-date per cycle for the trend view."""
        rows = self._conn.execute(
            """SELECT bc.cycle_no, bc.period_label,
                      SUM(rr.expected_amount) AS built_value,
                      SUM(rr.billed_amount)   AS billed_value
               FROM billing_cycle bc
               LEFT JOIN recon_result rr ON rr.cycle_id = bc.id
               WHERE bc.project_id=?
               GROUP BY bc.id ORDER BY bc.cycle_no""",
            (project_id,)).fetchall()
        return [dict(r) for r in rows]

    # --- audit ---
    def log(self, actor: str | None, action: str, entity: str,
           entity_id: int | None = None, detail: dict | None = None) -> None:
        with self.tx() as cur:
            cur.execute(
                """INSERT INTO audit_log(actor, action, entity, entity_id, detail_json)
                   VALUES (?,?,?,?,?)""",
                (actor, action, entity, entity_id,
                 json.dumps(detail) if detail else None),
            )

    def audit_trail(self, limit: int = 200) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
