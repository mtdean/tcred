"""
backend/data/daily_brief.py — scheduled morning digest + ntfy push.

Composes the pieces that already exist: data/digest.generate_digest writes
the same AM digest the Home panel's GENERATE button produces, cache/db
persists it, and data/alerts.send_push delivers it to the phone. Scheduled
via a cron trigger in data/scheduler.py (config: data_sources.yaml
`daily_brief:`); the push is skipped without NTFY_TOPIC but the digest
still generates and appears in the app.

The push goes through send_push directly, NOT the alert engine — the brief
is requested daily content, so it neither consumes the alerts max_per_day
budget nor records into alert_events.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from cache.db import save_digest
from config import load_data_sources

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def _persist(result: dict) -> None:
    """Bucket by US/Eastern day + AM/PM and upsert — same shape as the
    POST /api/digest route, so the Home panel picks it up unchanged."""
    et = datetime.fromisoformat(result["generated_at"]).astimezone(_ET)
    save_digest({
        "date": et.date().isoformat(),
        "session": "AM" if et.hour < 12 else "PM",
        "summary": result["summary"],
        "article_count": result["article_count"],
        "hours_back": result["hours_back"],
        "min_score": result["min_score"],
        "date_from": result["date_range"]["from"],
        "date_to": result["date_range"]["to"],
        "model": result["model"],
        "generated_at": result["generated_at"],
    })


def run_morning_brief() -> int:
    """Generate + persist the digest; push it if configured.

    Returns the number of articles covered (job_runs rows_ingested).
    A morning with no scored articles is a quiet success (0), not an error.
    """
    from data.digest import DigestError, generate_digest

    cfg = load_data_sources().get("daily_brief") or {}

    try:
        result = generate_digest(hours_back=24, min_score=4)
    except DigestError as e:
        logger.info("Morning brief skipped: %s", e)
        return 0

    _persist(result)

    if cfg.get("push", True):
        from data.alerts import send_push

        et = datetime.fromisoformat(result["generated_at"]).astimezone(_ET)
        title = f"TCRED Morning Brief · {et.strftime('%b %-d')}"
        sent = send_push(title, result["summary"], priority="default")
        logger.info(
            "Morning brief: %d articles, push %s",
            result["article_count"], "sent" if sent else "skipped/failed",
        )
    else:
        logger.info("Morning brief: %d articles (push disabled)", result["article_count"])

    return int(result["article_count"])
