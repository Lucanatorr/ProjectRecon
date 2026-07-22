"""Thin DB-access helpers for the UI. Each call opens and closes its own SQLite
connection (open-per-call), which sidesteps cross-thread connection issues from
Streamlit's shared cache_resource without needing a long-lived connection."""
from __future__ import annotations

from recon.models import TemplateProfile
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
