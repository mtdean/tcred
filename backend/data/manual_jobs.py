"""
backend/data/manual_jobs.py — run data-pull jobs in background threads.

Backs the REFRESH buttons / pull-to-refresh: POST /api/jobs/run/{job_id}
starts the same instrumented job body the scheduler uses (recorded in
`job_runs` with triggered_by='manual') and returns immediately with the
run_id; the frontend polls GET /api/jobs/run/{run_id} until it finishes.

A job_id with a run already in 'running' state is NOT started twice — the
caller gets the existing run_id back with already_running=True, so a second
pull while a refresh is in flight just attaches to it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from cache.db import get_conn, set_meta
from data import scheduler as sched

logger = logging.getLogger(__name__)

# job_id -> inner body (the un-instrumented callables in data/scheduler.py).
# Only jobs wired to a tab's REFRESH control are exposed.
MANUAL_JOBS = {
    "feeds": sched._feeds_inner,
    "market": sched._market_inner,
    "fred": sched._fred_inner,
    "edgar": sched._edgar_inner,
    "bdc": sched._bdc_inner,
    "regulatory": sched._regulatory_inner,
}

# Successful manual runs stamp the same /api/status meta keys the legacy
# sync refresh endpoints set, so the TopBar "last refresh" readout keeps working.
_META_KEYS = {
    "feeds": "last_news_refresh",
    "market": "last_market_refresh",
    "fred": "last_fred_refresh",
    "bdc": "last_bdc_refresh",
    "regulatory": "last_regulatory_refresh",
}

# Consider a 'running' row stale after this long — a crashed process can
# leave one behind and reap_stale_job_runs only fires at startup.
_RUNNING_STALE_MINUTES = 30


def _find_running(job_id: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id FROM job_runs
            WHERE job_id = ? AND status = 'running'
              AND started_at >= datetime('now', ?)
            ORDER BY started_at DESC LIMIT 1
            """,
            (job_id, f"-{_RUNNING_STALE_MINUTES} minutes"),
        ).fetchone()
    return int(row["id"]) if row else None


def start_background_job(job_id: str) -> dict:
    """Start `job_id` in a daemon thread. Returns {run_id, already_running}.

    The thread runs the scheduler's instrumented wrapper, so start/finish/
    error land in job_runs exactly like a scheduled run (triggered_by='manual').
    """
    if job_id not in MANUAL_JOBS:
        raise KeyError(job_id)

    existing = _find_running(job_id)
    if existing is not None:
        return {"run_id": existing, "job_id": job_id, "already_running": True}

    wrapper = sched._instrument(job_id, MANUAL_JOBS[job_id], triggered_by="manual")

    def _runner():
        try:
            result = wrapper()
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            # _instrument returns None when the job errored; only stamp the
            # last-refresh meta key on success.
            meta_key = _META_KEYS.get(job_id)
            if meta_key and result is not None:
                from datetime import datetime, timezone
                set_meta(meta_key, datetime.now(timezone.utc).isoformat())
        except Exception as e:  # _instrument already records the failure
            logger.error("Manual job %s thread error: %s", job_id, e)

    threading.Thread(target=_runner, name=f"manual-{job_id}", daemon=True).start()

    # The wrapper inserts its own job_runs row; poll briefly for it so we can
    # hand the caller a run_id (the insert happens within milliseconds).
    for _ in range(50):
        run_id = _find_running(job_id)
        if run_id is not None:
            return {"run_id": run_id, "job_id": job_id, "already_running": False}
        time.sleep(0.05)

    # Very fast jobs can finish before we ever see 'running' — fall back to
    # the newest manual run for this job.
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    return {
        "run_id": int(row["id"]) if row else -1,
        "job_id": job_id,
        "already_running": False,
    }


def get_job_run(run_id: int) -> dict | None:
    """One job_runs row by id, for the frontend's completion polling."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, job_id, started_at, ended_at, status, duration_ms, "
            "rows_ingested, error, triggered_by FROM job_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return dict(row) if row else None
