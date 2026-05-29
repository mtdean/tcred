"""
Cover the job-run observability layer:
  * cache.db.start_job_run / finish_job_run / get_latest_job_runs / get_job_run_history
  * data.scheduler._instrument (success + error capture)
  * /api/jobs/status and /api/jobs/history
"""

from __future__ import annotations

import asyncio

import pytest

from cache import db
from data import scheduler


# ─── DB helpers ──────────────────────────────────────────────────────────────
class TestJobRunHelpers:
    def test_start_then_finish_marks_success_and_duration(self, fresh_db):
        run_id = db.start_job_run("market")
        db.finish_job_run(run_id, "success", rows_ingested=42)

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT job_id, status, rows_ingested, duration_ms, error "
                "FROM job_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row["job_id"] == "market"
        assert row["status"] == "success"
        assert row["rows_ingested"] == 42
        assert row["duration_ms"] is not None and row["duration_ms"] >= 0
        assert row["error"] is None

    def test_finish_with_error_stores_truncated_message(self, fresh_db):
        run_id = db.start_job_run("edgar")
        long_err = "boom " * 500  # 2,500 chars
        db.finish_job_run(run_id, "error", error=long_err)

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT status, error FROM job_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row["status"] == "error"
        assert row["error"] is not None and len(row["error"]) <= 500

    def test_get_latest_job_runs_returns_one_per_job(self, fresh_db):
        # Two runs for 'market', one for 'fred' — latest 'market' should win.
        r1 = db.start_job_run("market")
        db.finish_job_run(r1, "success", rows_ingested=1)
        r2 = db.start_job_run("market")
        db.finish_job_run(r2, "error", error="x")
        r3 = db.start_job_run("fred")
        db.finish_job_run(r3, "success", rows_ingested=99)

        latest = db.get_latest_job_runs()
        by_job = {r["job_id"]: r for r in latest}
        assert set(by_job) == {"market", "fred"}
        assert by_job["market"]["status"] == "error"
        assert by_job["fred"]["rows_ingested"] == 99

    def test_get_job_run_history_filters_and_orders(self, fresh_db):
        for job in ("market", "market", "fred"):
            rid = db.start_job_run(job)
            db.finish_job_run(rid, "success")
        history = db.get_job_run_history(job_id="market", limit=10)
        assert len(history) == 2
        assert all(r["job_id"] == "market" for r in history)
        # Newest first.
        assert history[0]["started_at"] >= history[1]["started_at"]


# ─── _instrument wrapper ─────────────────────────────────────────────────────
class TestInstrumentWrapper:
    def test_sync_success_records_rows_from_return_value(self, fresh_db):
        def inner() -> int:
            return 7

        wrapped = scheduler._instrument("test-sync-ok", inner)
        result = wrapped()
        assert result == 7

        runs = db.get_latest_job_runs()
        ours = next(r for r in runs if r["job_id"] == "test-sync-ok")
        assert ours["status"] == "success"
        assert ours["rows_ingested"] == 7
        assert ours["error"] is None

    def test_sync_exception_records_error_and_returns_none(self, fresh_db):
        def boom() -> int:
            raise ValueError("nope")

        wrapped = scheduler._instrument("test-sync-err", boom)
        result = wrapped()
        assert result is None

        runs = db.get_latest_job_runs()
        ours = next(r for r in runs if r["job_id"] == "test-sync-err")
        assert ours["status"] == "error"
        assert "ValueError" in (ours["error"] or "")
        assert "nope" in (ours["error"] or "")

    def test_async_success_records_rows(self, fresh_db):
        async def inner() -> int:
            await asyncio.sleep(0)
            return 3

        wrapped = scheduler._instrument("test-async-ok", inner)
        result = asyncio.get_event_loop().run_until_complete(wrapped()) \
            if False else asyncio.run(wrapped())
        assert result == 3

        runs = db.get_latest_job_runs()
        ours = next(r for r in runs if r["job_id"] == "test-async-ok")
        assert ours["status"] == "success"
        assert ours["rows_ingested"] == 3

    def test_async_exception_does_not_propagate(self, fresh_db):
        async def boom() -> int:
            raise RuntimeError("async-boom")

        wrapped = scheduler._instrument("test-async-err", boom)
        result = asyncio.run(wrapped())
        assert result is None

        runs = db.get_latest_job_runs()
        ours = next(r for r in runs if r["job_id"] == "test-async-err")
        assert ours["status"] == "error"
        assert "RuntimeError" in (ours["error"] or "")

    def test_non_int_return_value_stores_null_rows(self, fresh_db):
        def inner():
            return {"some": "dict"}

        wrapped = scheduler._instrument("test-non-int", inner)
        wrapped()
        runs = db.get_latest_job_runs()
        ours = next(r for r in runs if r["job_id"] == "test-non-int")
        assert ours["status"] == "success"
        assert ours["rows_ingested"] is None


# ─── /api/jobs/* ─────────────────────────────────────────────────────────────
class TestJobsAPI:
    def test_status_endpoint_returns_latest_per_job(self, api_client, fresh_db):
        rid = db.start_job_run("market")
        db.finish_job_run(rid, "success", rows_ingested=5)

        resp = api_client.get("/api/jobs/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        jobs = {j["job_id"]: j for j in data["jobs"]}
        assert "market" in jobs
        assert jobs["market"]["status"] == "success"
        assert jobs["market"]["rows_ingested"] == 5

    def test_history_endpoint_filters_by_job_id(self, api_client, fresh_db):
        for job in ("fred", "fred", "market"):
            rid = db.start_job_run(job)
            db.finish_job_run(rid, "success")

        resp = api_client.get("/api/jobs/history?job_id=fred")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) == 2
        assert all(r["job_id"] == "fred" for r in runs)

    def test_main_status_includes_jobs_summary(self, api_client, fresh_db):
        rid = db.start_job_run("bdc")
        db.finish_job_run(rid, "success", rows_ingested=1234)

        resp = api_client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        bdc_summary = next(j for j in data["jobs"] if j["job_id"] == "bdc")
        assert bdc_summary["rows_ingested"] == 1234
