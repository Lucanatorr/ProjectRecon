"""Sprint 4.6 — database backup / restore (SDD §12 operations)."""
from __future__ import annotations

import sqlite3

import pytest

from recon.models import AsBuiltLine, ContractItem, InvoiceLine, UoM
from recon.persistence import Database
from recon.reconcile import reconcile
from tools import backup as backup_tool


def _seed(path, project="Job", cycle_no=1):
    db = Database(path)
    try:
        contract = [ContractItem("A", "Unit A", UoM.EA, 10.0, 100)]
        rows = reconcile([AsBuiltLine("A", 10, UoM.EA, code="A")],
                         [InvoiceLine("INV", "A", 10, 10.0, 100.0, code="A")], contract)
        db.save_cycle_snapshot(
            project_name=project, contractor=None, area=None, cycle_no=cycle_no,
            period_label="Jan", billing_mode="cumulative", retainage_pct=10.0,
            prior_billed=0.0, contract_items=contract, rows=rows)
    finally:
        db.close()


def test_backup_produces_a_readable_copy(tmp_path):
    live = tmp_path / "recon.db"
    _seed(live)
    out = backup_tool.backup(source=live, out=tmp_path / "snap.db")

    assert out.exists() and out.stat().st_size > 0
    db = Database(out)
    try:
        assert db.project_by_name("Job") is not None      # data survived intact
    finally:
        db.close()


def test_backup_works_while_a_connection_is_open(tmp_path):
    """The online-backup API must cope with the app still holding the database."""
    live = tmp_path / "recon.db"
    _seed(live)
    holder = sqlite3.connect(str(live))
    try:
        out = backup_tool.backup(source=live, out=tmp_path / "hot.db")
        assert out.exists()
    finally:
        holder.close()


def test_backup_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_tool.backup(source=tmp_path / "nope.db", out=tmp_path / "x.db")


def test_restore_replaces_target_and_keeps_a_safety_copy(tmp_path):
    live = tmp_path / "recon.db"
    _seed(live, project="Original")
    snapshot = backup_tool.backup(source=live, out=tmp_path / "snap.db")

    # move the live database on to a different state
    _seed(live, project="Later", cycle_no=2)
    db = Database(live)
    try:
        assert db.project_by_name("Later") is not None
    finally:
        db.close()

    backup_tool.restore(snapshot, target=live)

    db = Database(live)
    try:
        assert db.project_by_name("Original") is not None
        assert db.project_by_name("Later") is None        # snapshot won
    finally:
        db.close()
    # the pre-restore state was preserved rather than destroyed
    assert live.with_suffix(".pre-restore.db").exists()


def test_restore_missing_snapshot_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_tool.restore(tmp_path / "nope.db", target=tmp_path / "recon.db")
