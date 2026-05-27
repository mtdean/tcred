"""
backend/data/scheduler.py — APScheduler background jobs.

Job cadence (configurable in data_sources.yaml):
  - Feed fetch:     every 30 min
  - Classifier:     every 30 min (after feed fetch)
  - Market data:    every 15 min
  - FRED data:      every 6 hours
  - EDGAR filings:  every 4 hours
  - Feed health:    once at startup, then every 12 hours
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import load_data_sources

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


async def _job_feeds():
    from data.feeds import fetch_all_feeds
    from data.classifier import classify_articles
    try:
        n = await fetch_all_feeds()
        logger.info(f"Scheduler: feed fetch — {n} articles")
        scored = await classify_articles(batch_size=50)
        logger.info(f"Scheduler: classified {scored} articles")
    except Exception as e:
        logger.error(f"Scheduler: feed job error: {e}")


def _job_market():
    from data.market import fetch_market_data
    try:
        n = fetch_market_data()
        logger.info(f"Scheduler: market data — {n} rows")
    except Exception as e:
        logger.error(f"Scheduler: market job error: {e}")


def _job_fred():
    from data.fred import fetch_fred_series
    from data.indicators import compute_all_indicators
    try:
        n = fetch_fred_series()
        # Derived indicators run last: the recession probit reads T10Y3M out of
        # the DB, so the raw FRED pull must land first.
        m = compute_all_indicators()
        logger.info(f"Scheduler: FRED — {n} rows, indicators — {m} rows")
    except Exception as e:
        logger.error(f"Scheduler: FRED job error: {e}")


def _job_edgar():
    from data.edgar import fetch_abs_filings
    from data.abs_pricing import fetch_abs_pricing
    try:
        n = fetch_abs_filings(days_back=2)
        # New-issue spread tracker shares EDGAR's cadence (token-free, regex parse).
        p = fetch_abs_pricing(days_back=10)
        logger.info(f"Scheduler: EDGAR — {n} filings, ABS pricing — {p} tranches")
    except Exception as e:
        logger.error(f"Scheduler: EDGAR job error: {e}")


def _job_hhdc():
    from data.hhdc import fetch_hhdc_transitions
    from data.indicators import compute_cfsi
    try:
        n = fetch_hhdc_transitions()
        # CFSI consumes the transition flows, so refresh it once they land.
        c = compute_cfsi()
        logger.info(f"Scheduler: HHDC transition rates — {n} rows, CFSI — {c} rows")
    except Exception as e:
        logger.error(f"Scheduler: HHDC job error: {e}")


async def _job_health():
    from data.feeds import run_health_checks
    try:
        summary = await run_health_checks()
        logger.info(f"Scheduler: health check — {summary['live']} live feeds")
    except Exception as e:
        logger.error(f"Scheduler: health check error: {e}")


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
        _job_health(),                  # async (aiohttp)
        asyncio.to_thread(_job_market),  # blocking → thread
        asyncio.to_thread(_job_fred),    # blocking → thread
        asyncio.to_thread(_job_edgar),   # blocking → thread
        asyncio.to_thread(_job_hhdc),    # blocking → thread
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
