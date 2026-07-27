"""Thin DB-access helpers for the UI. Each call opens and closes its own SQLite
connection (open-per-call), which sidesteps cross-thread connection issues from
Streamlit's shared cache_resource without needing a long-lived connection."""
from __future__ import annotations

import json

from recon.models import Flag, ReconRow, Severity, TemplateProfile, UoM
from recon.persistence import Database


def load_profile(contractor: str) -> TemplateProfile | None:
    if not contractor:
        return None
    db = Database()
    try:
        return db.load_template_profile(contractor)
    finally:
        db.close()


def save_profile(profile: TemplateProfile, actor: str | None = None) -> None:
    db = Database()
    try:
        db.save_template_profile(profile, actor=actor)
    finally:
        db.close()


def list_profiles() -> list[str]:
    db = Database()
    try:
        return db.list_template_profiles()
    finally:
        db.close()


def delete_profile(contractor: str) -> None:
    db = Database()
    try:
        db.delete_template_profile(contractor)
    finally:
        db.close()


def save_cycle(**kwargs) -> tuple[int, int]:
    db = Database()
    try:
        return db.save_cycle_snapshot(**kwargs)
    finally:
        db.close()


def portfolio() -> list[dict]:
    """Per-project stats for the Home page (most recently created first)."""
    db = Database()
    try:
        return db.project_stats()
    finally:
        db.close()


def project_overview(project_id: int) -> dict | None:
    """A project's header, contractors, saved-cycle summaries, and built-vs-billed
    trend — everything the read-only project detail page renders."""
    db = Database()
    try:
        p = db.project_by_id(project_id)
        if p is None:
            return None
        summaries = db.cycle_summaries(project_id)
        return {
            "id": p["id"], "name": p["name"], "area": p["area"],
            "status": p["status"] or "active",
            "contractors": [c["name"] for c in db.project_contractors(project_id)],
            "cycles": summaries,
            "trend": db.trend(project_id),
            "billed": sum(s["billed"] for s in summaries),
            "built": sum(s["expected"] for s in summaries),
            "flagged": sum(s["flagged"] for s in summaries),
        }
    finally:
        db.close()


# --- projects & contractors (Home create/attach flows) -------------------- #
def project_exists(name: str) -> bool:
    db = Database()
    try:
        return db.project_by_name(name) is not None
    finally:
        db.close()


def create_project(name: str, area: str | None = None,
                   contractor_names: list[str] | None = None) -> int:
    """Create a project and register + attach its contractors. Returns the id."""
    contractor_names = [n.strip() for n in (contractor_names or []) if n.strip()]
    db = Database()
    try:
        pid = db.get_or_create_project(name, contractor_names[0]
                                       if contractor_names else None, area)
        for nm in contractor_names:
            db.link_contractor(pid, db.get_or_create_contractor(nm))
        db.log(None, "create_project", "project", pid,
               {"name": name, "contractors": contractor_names})
        return pid
    finally:
        db.close()


def attach_contractors(project_id: int, names: list[str]) -> None:
    names = [n.strip() for n in names if n.strip()]
    db = Database()
    try:
        for nm in names:
            db.link_contractor(project_id, db.get_or_create_contractor(nm))
        if names:
            db.log(None, "attach_contractors", "project", project_id,
                   {"contractors": names})
    finally:
        db.close()


def create_contractor(name: str, company: str | None = None) -> int:
    db = Database()
    try:
        return db.get_or_create_contractor(name.strip(), company or None)
    finally:
        db.close()


def contractor_names() -> list[str]:
    db = Database()
    try:
        return [c["name"] for c in db.list_contractors()]
    finally:
        db.close()


def contractors_overview() -> list[dict]:
    """Every contractor with the projects they're attached to (Contractors page)."""
    db = Database()
    try:
        projects = db.project_stats()
        return [{"name": c["name"], "company": c["company"],
                 "projects": [p["name"] for p in projects
                              if c["name"] in p["contractors"]]}
                for c in db.list_contractors()]
    finally:
        db.close()


def next_cycle_no(project_id: int, contractor_name: str | None = None) -> int:
    """The next cycle number for a contractor on a project (1 if none saved yet)."""
    db = Database()
    try:
        cid = None
        if contractor_name:
            c = db.contractor_by_name(contractor_name)
            if c is None:
                return 1
            cid = c["id"]
        cycles = db.list_cycles(project_id, cid)
        return (max(c["cycle_no"] for c in cycles) + 1) if cycles else 1
    finally:
        db.close()


def load_saved_contract(project_id: int):
    """The project's saved bid schedule — reused to seed the next cycle."""
    db = Database()
    try:
        return db.load_contract(project_id)
    finally:
        db.close()


def cycle_detail(cycle_id: int) -> dict | None:
    """Rebuild a saved cycle's full reconciliation (rows + reviewer decisions) for
    the read-only drill-in. Source-file feeds aren't persisted, so those stay empty;
    everything else — quantities, variance, findings, sign-off — is faithful."""
    db = Database()
    try:
        bc = db.get_cycle(cycle_id)
        if bc is None:
            return None
        rows, resolutions = [], {}
        for r in db.load_results(cycle_id):
            flags = [Flag(f["rule"], Severity(f["severity"]), f["message"])
                     for f in (json.loads(r["flags_json"]) if r["flags_json"] else [])]
            rr = ReconRow(
                code=r["code"], description=r["description"],
                uom=UoM.from_str(r["uom"]) or UoM.EA,
                built_qty=r["built_qty"] or 0.0, billed_qty=r["billed_qty"] or 0.0,
                contract_price=r["contract_price"], billed_price=r["billed_price"] or 0.0,
                est_qty=r["est_qty"], flags=flags)
            rows.append(rr)
            if r["resolution"]:
                resolutions[rr.code or rr.description] = {
                    "status": r["resolution"], "note": r["resolution_note"] or "",
                    "by": r["resolved_by"] or "", "at": r["resolved_at"] or ""}
        return {
            "cycle_id": cycle_id, "project_id": bc["project_id"],
            "project_name": bc["project_name"], "contractor": bc["contractor_name"],
            "cycle_no": bc["cycle_no"], "period_label": bc["period_label"],
            "billing_mode": bc["billing_mode"],
            "retainage_pct": bc["retainage_pct"] or 0.0,
            "rows": rows, "resolutions": resolutions,
        }
    finally:
        db.close()


def cycle_history(project_name: str) -> list[dict]:
    """Saved-cycle summaries for a project (empty if it has none yet)."""
    if not project_name:
        return []
    db = Database()
    try:
        proj = db.project_by_name(project_name)
        return db.cycle_summaries(proj["id"]) if proj else []
    finally:
        db.close()


def log_action(action: str, entity: str, *, actor: str | None = None,
               entity_id: int | None = None, detail: dict | None = None) -> None:
    """Append an entry to the audit log (FR-17)."""
    db = Database()
    try:
        db.log(actor, action, entity, entity_id, detail)
    finally:
        db.close()


def audit_entries(limit: int = 100) -> list[dict]:
    """Most recent audit-log entries, newest first."""
    db = Database()
    try:
        return [dict(r) for r in db.audit_trail(limit)]
    finally:
        db.close()


# --- global crosswalk aliases (FR-7: confirmed mappings persist across jobs) --- #
def load_aliases():
    """The learned alias store, shared across every project."""
    db = Database()
    try:
        return db.load_alias_store()
    finally:
        db.close()


def confirm_alias(raw_desc: str, code: str, *, actor: str | None = None) -> None:
    db = Database()
    try:
        db.confirm_alias(raw_desc, code, actor=actor)
    finally:
        db.close()


def delete_alias(raw_desc: str, *, actor: str | None = None) -> None:
    db = Database()
    try:
        db.delete_alias(raw_desc, actor=actor)
    finally:
        db.close()


def prior_billed(project_name: str, before_cycle_no: int,
                 contractor_name: str | None = None) -> dict[str, float]:
    """Per-unit billed-to-date from this contractor's most recent cycle before this
    one. Scoped to the contractor so a project's contractors don't cross-contaminate
    each other's cumulative prior."""
    if not project_name:
        return {}
    db = Database()
    try:
        proj = db.project_by_name(project_name)
        if not proj:
            return {}
        cid = None
        if contractor_name:
            c = db.contractor_by_name(contractor_name)
            cid = c["id"] if c else None
        return db.prior_billed_by_code(proj["id"], before_cycle_no, cid)
    finally:
        db.close()
