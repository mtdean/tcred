"""Cover data/backups.py + the /api/backups endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cache import db
from data import backups


def _seed_something(value=1):
    db.upsert_metric({
        "series_id": "X", "label": "x", "category": "x",
        "date": "2026-05-29", "value": value,
        "fetched_at": "2026-05-29T00:00:00+00:00",
    })


class TestBackupDatabase:
    def test_creates_file_for_today(self, fresh_db, tmp_path):
        _seed_something()
        result = backups.backup_database(
            db_path=db.DB_PATH, backup_dir=tmp_path, retention=14,
        )
        path = Path(result["backup_path"])
        assert path.is_file()
        assert path.name.startswith("monitor-") and path.name.endswith(".db")
        assert result["size_bytes"] > 0

    def test_idempotent_on_same_day(self, fresh_db, tmp_path):
        _seed_something()
        backups.backup_database(db_path=db.DB_PATH, backup_dir=tmp_path)
        backups.backup_database(db_path=db.DB_PATH, backup_dir=tmp_path)
        # Same day → same filename → one file.
        files = list(tmp_path.glob("monitor-*.db"))
        assert len(files) == 1

    def test_retention_prunes_older_snapshots(self, fresh_db, tmp_path):
        # Plant 20 fake snapshots dated across the past 30 days.
        base = datetime(2026, 5, 1, tzinfo=timezone.utc)
        for i in range(20):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            (tmp_path / f"monitor-{d}.db").write_bytes(b"x")

        _seed_something()
        # Run today's backup with retention=5 — only the 5 most-recent kept.
        result = backups.backup_database(
            db_path=db.DB_PATH, backup_dir=tmp_path, retention=5,
        )
        files = sorted(p.name for p in tmp_path.glob("monitor-*.db"))
        assert len(files) == 5
        assert result["kept"] == 5
        assert result["deleted"] >= 1

    def test_missing_source_raises(self, fresh_db, tmp_path):
        with pytest.raises(FileNotFoundError):
            backups.backup_database(
                db_path=tmp_path / "does-not-exist.db",
                backup_dir=tmp_path,
            )


class TestListBackups:
    def test_returns_newest_first(self, fresh_db, tmp_path):
        for d in ("2026-05-27", "2026-05-29", "2026-05-28"):
            (tmp_path / f"monitor-{d}.db").write_bytes(b"x")
        out = backups.list_backups(backup_dir=tmp_path)
        names = [b["filename"] for b in out]
        assert names == [
            "monitor-2026-05-29.db",
            "monitor-2026-05-28.db",
            "monitor-2026-05-27.db",
        ]

    def test_missing_dir_returns_empty(self, fresh_db, tmp_path):
        assert backups.list_backups(backup_dir=tmp_path / "missing") == []


class TestBackupRoutes:
    def test_list_returns_payload(self, api_client, fresh_db):
        resp = api_client.get("/api/backups")
        assert resp.status_code == 200
        assert "backups" in resp.json()

    def test_run_endpoint_writes_file(self, api_client, fresh_db, monkeypatch):
        # Redirect the backup dir so the test doesn't touch cache/backups/.
        import data.backups as bmod
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="bkp-test-"))
        monkeypatch.setattr(bmod, "DEFAULT_BACKUP_DIR", tmp)
        resp = api_client.post("/api/backups/run")
        assert resp.status_code == 200
        body = resp.json()
        assert Path(body["backup_path"]).is_file()
