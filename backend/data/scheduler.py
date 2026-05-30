"""
backend/data/scheduler.py — APScheduler background jobs.

Job cadence (configurable in data_sources.yaml):
  - Feed fetch:     every 30 min
  - Classifier:     every 30 min (after feed fetch)
  - Market data:    every 15 min
  - FRED data:      every 6 hours
  - EDGAR filings:  every 4 hours
  - Feed health:    once at startup, then every 12 hours

Every job run is recorded in the `job_runs` table (start, end, status,
duration, rows ingested, error) so the dashboard can report freshness and
catch silent failures without tailing logs.
"""

import asyncio
import inspect
import logging
import traceback
from functools import wraps
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from cache.db import finish_job_run, start_job_run
from config import load_data_sources

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


# ── Job-run recording wrapper ────────────────────────────────────────────────
def _instrument(job_id: str, fn: Callable, triggered_by: str = "scheduler"):
    """Wrap a job function so each invocation is captured in `job_runs`.

    The wrapped fn may return an int (treated as rows ingested) or None.
    Async fns are awaited; sync fns are called directly. Exceptions are caught,
    logged, and recorded — the scheduler thread/loop must never die from a job.
    """
    is_async = inspect.iscoroutinefunction(fn)

    @wraps(fn)
    async def _async_wrapper():
        run_id = start_job_run(job_id, triggered_by=triggered_by)
        try:
            result = await fn()
            rows = result if isinstance(result, int) else None
            finish_job_run(run_id, "success", rows_ingested=rows)
            return result
        except Exception as e:  # noqa: BLE001 — record-and-continue
            finish_job_run(
                run_id, "error",
                error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}",
            )
            logger.error("Scheduler: %s job error: %s", job_id, e)
            return None

    @wraps(fn)
    def _sync_wrapper():
        run_id = start_job_run(job_id, triggered_by=triggered_by)
        try:
            result = fn()
            rows = result if isinstance(result, int) else None
            finish_job_run(run_id, "success", rows_ingested=rows)
            return result
        except Exception as e:  # noqa: BLE001
            finish_job_run(
                run_id, "error",
                error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}",
            )
            logger.error("Scheduler: %s job error: %s", job_id, e)
            return None

    return _async_wrapper if is_async else _sync_wrapper


# ── Inner job bodies (return the row count, no try/except) ───────────────────

async def _feeds_inner() -> int:
    from data.feeds import fetch_all_feeds
    from data.classifier import classify_articles
    n = await fetch_all_feeds()
    logger.info(f"Scheduler: feed fetch — {n} articles")
    scored = await classify_articles(batch_size=50)
    logger.info(f"Scheduler: classified {scored} articles")
    return n


def _market_inner() -> int:
    from data.market import fetch_market_data
    n = fetch_market_data()
    logger.info(f"Scheduler: market data — {n} rows")
    return n


def _fred_inner() -> int:
    from data.fred import fetch_fred_series
    from data.indicators import compute_all_indicators
    n = fetch_fred_series()
    # Derived indicators run last: the recession probit reads T10Y3M out of
    # the DB, so the raw FRED pull must land first.
    m = compute_all_indicators()
    logger.info(f"Scheduler: FRED — {n} rows, indicators — {m} rows")
    return n + m


def _edgar_inner() -> int:
    from data.edgar import fetch_abs_filings
    from data.abs_pricing import fetch_abs_pricing
    from data.abs_parser import fetch_and_parse_abs_424b5
    n = fetch_abs_filings(days_back=2)
    p = fetch_abs_pricing(days_back=10)
    n4 = fetch_and_parse_abs_424b5(days_back=2)
    logger.info(
        f"Scheduler: EDGAR — {n} filings, ABS pricing — {p} tranches, "
        f"424B5 — {n4} tranches"
    )
    return n + p + n4


def _hhdc_inner() -> int:
    from data.hhdc import fetch_hhdc_transitions
    from data.indicators import compute_cfsi
    n = fetch_hhdc_transitions()
    # CFSI consumes the transition flows, so refresh it once they land.
    c = compute_cfsi()
    logger.info(f"Scheduler: HHDC transition rates — {n} rows, CFSI — {c} rows")
    return n + c


async def _health_inner() -> int:
    from data.feeds import run_health_checks
    summary = await run_health_checks()
    logger.info(f"Scheduler: health check — {summary['live']} live feeds")
    return summary.get("total", 0)


# Phase 7 — token-free ingest only.
def _bdc_inner() -> int:
    """Monthly SEC BDC bulk-dataset pull (token-free)."""
    from data.bdc import fetch_bdc_data
    n = fetch_bdc_data()
    logger.info(f"Scheduler: BDC — {n} holdings")
    return n


def _regulatory_inner() -> int:
    """Daily Federal Register + agency RSS pull (token-free; scoring is manual)."""
    from data.regulatory import fetch_regulatory_actions
    n = fetch_regulatory_actions(days_back=2)
    logger.info(f"Scheduler: Regulatory — {n} actions")
    return n


def _backup_inner() -> int:
    """Nightly SQLite snapshot of monitor.db with rolling retention."""
    from data.backups import backup_database
    result = backup_database()
    logger.info(
        "Scheduler: DB backup — %s (%.1f MB)",
        result["backup_path"], result["size_bytes"] / 1_048_576,
    )
    return result["kept"]


def _article_dedup_inner() -> int:
    """Cluster + tag recent articles by title similarity (token-set Jaccard)."""
    from data.article_dedup import dedup_recent_articles
    result = dedup_recent_articles(window_hours=72)
    logger.info(
        "Scheduler: article dedup — %d articles → %d clusters (%d duplicates)",
        result["processed"], result["clusters"], result["duplicates"],
    )
    return result["duplicates"]


# Instrumented entry points the scheduler / initial fetch invoke.
_job_feeds = _instrument("feeds", _feeds_inner)
_job_market = _instrument("market", _market_inner)
_job_fred = _instrument("fred", _fred_inner)
_job_edgar = _instrument("edgar", _edgar_inner)
_job_hhdc = _instrument("hhdc", _hhdc_inner)
_job_health = _instrument("health", _health_inner)
_job_bdc = _instrument("bdc", _bdc_inner)
_job_regulatory = _instrument("regulatory", _regulatory_inner)
_job_backup = _instrument("backup", _backup_inner)
_job_article_dedup = _instrument("article_dedup", _article_dedup_inner)


async def start_scheduler():
    global _scheduler
    cfg = load_data_sources()
    intervals = cfg.get("refresh_intervals", {})

    _scheduler = AsyncIOScheduler()

    # News fetch+classify is the ONLY job that spends Claude tokens. When
    # news_feeds_minutes <= 0 it is disabled entirely and runs only on demand
    # via the REFRESH button (POST /api/articles/refresh). All other jobs below
    # are token-free.
    news_minutes = intervals.get("news_feeds_minutes", 0)
    auto_news = news_minutes and news_minutes > 0
    if auto_news:
        _scheduler.add_job(
            _job_feeds,
            IntervalTrigger(minutes=news_minutes),
            id="feeds",
            max_instances=1,
            replace_existing=True,
        )

    _scheduler.add_job(
        _job_market,
        IntervalTrigger(minutes=intervals.get("market_data_minutes", 15)),
        id="market",
        max_instances=1,
        replace_existing=True,
    )
    _scheduler.add_job(
        _job_fred,
        IntervalTrigger(hours=intervals.get("fred_data_hours", 6)),
        id="fred",
        max_instances=1,
        replace_existing=True,
    )
    _scheduler.add_job(
        _job_edgar,
        IntervalTrigger(hours=intervals.get("edgar_filings_hours", 4)),
        id="edgar",
        max_instances=1,
        replace_existing=True,
    )
    _scheduler.add_job(
        _job_health,
        IntervalTrigger(hours=12),
        id="health",
        max_instances=1,
        replace_existing=True,
    )
    # NY Fed HHDC is quarterly data; a daily check is ample to catch new releases.
    _scheduler.add_job(
        _job_hhdc,
        IntervalTrigger(hours=24),
        id="hhdc",
        max_instances=1,
        replace_existing=True,
    )

    # Phase 7: BDC bulk dataset is published monthly. Daily check is cheap and
    # picks up the release within hours of SEC posting.
    _scheduler.add_job(
        _job_bdc,
        IntervalTrigger(hours=24),
        id="bdc",
        max_instances=1,
        replace_existing=True,
    )

    # Phase 7: Federal Register + agency RSS updates daily. Token-free.
    _scheduler.add_job(
        _job_regulatory,
        IntervalTrigger(hours=12),
        id="regulatory",
        max_instances=1,
        replace_existing=True,
    )

    # Nightly DB backup with rolling retention. SQLite's online backup API is
    # safe against concurrent writes — no shutdown needed.
    _scheduler.add_job(
        _job_backup,
        IntervalTrigger(hours=24),
        id="backup",
        max_instances=1,
        replace_existing=True,
    )

    # Re-cluster articles every 30m so a story posted at t=29m gets matched
    # against a story posted at t=0 once dedup next runs.
    _scheduler.add_job(
        _job_article_dedup,
        IntervalTrigger(minutes=30),
        id="article_dedup",
        max_instances=1,
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started (auto news fetch/classify: %s)",
        "every %dm" % news_minutes if auto_news else "OFF — manual REFRESH only",
    )

    # Kick off the initial data load in the background so the HTTP port opens
    # immediately. start_scheduler() therefore returns at once and the app is
    # serving within moments; the (token-free) data fills in shortly after.
    asyncio.create_task(_initial_fetch(auto_news))


async def _initial_fetch(auto_news: bool) -> None:
    """One-shot startup data load, run off the critical path.

    Blocking fetchers (market/FRED/EDGAR use requests/yfinance) run in worker
    threads so they never stall the event loop that's now serving requests.
    """
    logger.info("Initial data fetch started (background)")
    tasks = [
        _job_health(),                       # async (aiohttp)
        asyncio.to_thread(_job_market),       # blocking → thread
        asyncio.to_thread(_job_fred),         # blocking → thread
        asyncio.to_thread(_job_edgar),        # blocking → thread
        asyncio.to_thread(_job_hhdc),         # blocking → thread
        asyncio.to_thread(_job_regulatory),   # blocking → thread (Phase 7)
        asyncio.to_thread(_job_bdc),          # blocking → thread (Phase 7, ~50MB ZIP)
    ]
    if auto_news:
        tasks.append(_job_feeds())       # async
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Initial fetch error: {e}")
    logger.info("Initial data fetch complete")


async def shutdown_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
