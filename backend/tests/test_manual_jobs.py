"""
Cover data/manual_jobs.py — background refresh jobs behind POST /api/jobs/run.
"""

from __future__ import annotations

import threading
import time

import pytest

from cache import db
from data import manual_jobs


def _wait_for(predicate, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _latest_run(job_id: str):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


class TestStartBackgroundJob:
    def test_unknown_job_raises(self, fresh_db):
        with pytest.raises(KeyError):
            manual_jobs.start_background_job("nope")

    def test_sync_job_runs_and_records(self, fresh_db, monkeypatch):
        monkeypatch.setitem(manual_jobs.MANUAL_JOBS, "market", lambda: 42)
        out = manual_jobs.start_background_job("market")
        assert out["job_id"] == "market"
        assert out["already_running"] is False
        assert _wait_for(
            lambda: (_latest_run("market") or {}).get("status") == "success"
        )
        run = _latest_run("market")
        assert run["rows_ingested"] == 42
        assert run["triggered_by"] == "manual"

    def test_success_stamps_meta_key(self, fresh_db, monkeypatch):
        monkeypatch.setitem(manual_jobs.MANUAL_JOBS, "market", lambda: 7)
        manual_jobs.start_background_job("market")
        assert _wait_for(lambda: db.get_meta("last_market_refresh") is not None)

    def test_error_recorded_and_no_meta(self, fresh_db, monkeypatch):
        def _boom():
            raise RuntimeError("upstream down")
        monkeypatch.setitem(manual_jobs.MANUAL_JOBS, "market", _boom)
        manual_jobs.start_background_job("market")
        assert _wait_for(
            lambda: (_latest_run("market") or {}).get("status") == "error"
        )
        run = _latest_run("market")
        assert "upstream down" in run["error"]
        assert db.get_meta("last_market_refresh") is None

    def test_async_job_supported(self, fresh_db, monkeypatch):
        async def _async_job():
            return 5
        monkeypatch.setitem(manual_jobs.MANUAL_JOBS, "feeds", _async_job)
        manual_jobs.start_background_job("feeds")
        assert _wait_for(
            lambda: (_latest_run("feeds") or {}).get("status") == "success"
        )
        assert _latest_run("feeds")["rows_ingested"] == 5

    def test_second_start_attaches_to_running_job(self, fresh_db, monkeypatch):
        release = threading.Event()

        def _slow():
            release.wait(5)
            return 1
        monkeypatch.setitem(manual_jobs.MANUAL_JOBS, "market", _slow)
        first = manual_jobs.start_background_job("market")
        second = manual_jobs.start_background_job("market")
        release.set()
        assert second["already_running"] is True
        assert second["run_id"] == first["run_id"]
        assert _wait_for(
            lambda: (_latest_run("market") or {}).get("status") == "success"
        )


class TestGetJobRun:
    def test_missing_returns_none(self, fresh_db):
        assert manual_jobs.get_job_run(99999) is None

    def test_returns_row(self, fresh_db):
        run_id = db.start_job_run("market", triggered_by="manual")
        db.finish_job_run(run_id, "success", rows_ingested=3)
        run = manual_jobs.get_job_run(run_id)
        assert run["status"] == "success"
        assert run["rows_ingested"] == 3
