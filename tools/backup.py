"""Back up / restore the Splice database.

Uses SQLite's online-backup API rather than copying the file, so a backup taken
while the app is running is always internally consistent (a plain file copy can
capture a half-written transaction).

    python tools/backup.py backup                 # -> backups/recon-YYYYmmdd-HHMMSS.db
    python tools/backup.py backup --out mine.db
    python tools/backup.py restore backups/recon-20260723-2210.db
    python tools/backup.py list
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH  # noqa: E402

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def backup(source: Path = None, out: Path = None) -> Path:
    """Copy the live database to `out` (default: timestamped file in backups/)."""
    source = Path(source) if source else DB_PATH
    if not source.exists():
        raise FileNotFoundError(f"No database at {source}")
    if out is None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        out = BACKUP_DIR / f"recon-{datetime.now():%Y%m%d-%H%M%S}.db"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(out))
    try:
        with dst:
            src.backup(dst)          # consistent even with the app running
    finally:
        src.close()
        dst.close()
    return out


def restore(snapshot: Path, target: Path = None) -> Path:
    """Restore a snapshot over the live database, keeping a safety copy first."""
    snapshot = Path(snapshot)
    if not snapshot.exists():
        raise FileNotFoundError(f"No snapshot at {snapshot}")
    target = Path(target) if target else DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():              # never destroy the current database silently
        safety = backup(target, target.with_suffix(".pre-restore.db"))
        print(f"Current database saved to {safety}")

    src = sqlite3.connect(str(snapshot))
    dst = sqlite3.connect(str(target))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return target


def list_backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("recon-*.db"), reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_backup = sub.add_parser("backup", help="snapshot the database")
    p_backup.add_argument("--out", type=Path, default=None)

    p_restore = sub.add_parser("restore", help="restore a snapshot")
    p_restore.add_argument("snapshot", type=Path)

    sub.add_parser("list", help="list available snapshots")

    args = parser.parse_args()
    if args.command == "backup":
        print(f"Backed up {DB_PATH} -> {backup(out=args.out)}")
    elif args.command == "restore":
        print(f"Restored {args.snapshot} -> {restore(args.snapshot)}")
    else:
        found = list_backups()
        print("\n".join(str(p) for p in found) if found else "No backups yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
