"""
backend/data/backups.py — nightly SQLite backups with rolling retention.

Uses `sqlite3.Connection.backup()` (the C-level online backup API) so the file
is consistent even while the app is mid-write. A naive `cp` of the .db file
during a write can produce a corrupted snapshot — this API is the supported
way to get a point-in-time copy.

Backups land under `backend/cache/backups/` with one file per day:
    monitor-YYYY-MM-DD.db

Retention: keep the last N (default 14) daily snapshots; delete older ones.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


DEFAULT_RETENTION = 14
DEFAULT_BACKUP_DIR = settings.DB_PATH.parent / "backups"


def backup_database(
    db_path: Path | None = None,
    backup_dir: Path | None = None,
    retention: int = DEFAULT_RETENTION,
    today: datetime | None = None,
) -> dict:
    """Create today's snapshot, prune anything beyond the retention window.

    Returns `{"backup_path": str, "size_bytes": int, "kept": int, "deleted": int}`.
    Idempotent: re-running on the same day overwrites that day's snapshot.
    """
    src = db_path or settings.DB_PATH
    if not src.is_file():
        raise FileNotFoundError(f"DB not found at {src}")

    dest_dir = backup_dir or DEFAULT_BACKUP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    stamp = (today or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    dest = dest_dir / f"monitor-{stamp}.db"

    # Online backup — copies pages while the source is potentially being
    # written to. Safer than shutil.copy for a live SQLite file.
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    kept, deleted = _enforce_retention(dest_dir, retention)
    size = dest.stat().st_size
    logger.info("DB backup: %s (%.1f MB) — kept %d, deleted %d",
                dest.name, size / 1_048_576, kept, deleted)
    return {
        "backup_path": str(dest),
        "size_bytes": size,
        "kept": kept,
        "deleted": deleted,
    }


def _enforce_retention(backup_dir: Path, retention: int) -> tuple[int, int]:
    """Keep the N most-recent `monitor-YYYY-MM-DD.db` files; delete older ones."""
    snapshots = sorted(
        backup_dir.glob("monitor-*.db"),
        key=lambda p: p.name,
        reverse=True,  # newest first
    )
    keep = snapshots[:retention]
    drop = snapshots[retention:]
    for old in drop:
        try:
            old.unlink()
        except OSError as e:
            logger.warning("Could not delete old backup %s: %s", old.name, e)
    return len(keep), len(drop)


def list_backups(backup_dir: Path | None = None) -> list[dict]:
    """All snapshots currently on disk, newest first."""
    dest_dir = backup_dir or DEFAULT_BACKUP_DIR
    if not dest_dir.is_dir():
        return []
    out = []
    for p in sorted(dest_dir.glob("monitor-*.db"), reverse=True):
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append({
            "filename": p.name,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
        })
    return out
