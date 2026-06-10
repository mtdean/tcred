"""Shared date-window helpers.

All trailing-window date strings (EDGAR ranges, SQL `filing_date >= ?`
cutoffs, …) are built here so the whole backend uses one clock: UTC.
Naive datetime.now() drifts from the UTC timestamps we store, which skewed
window boundaries near midnight.
"""

from datetime import datetime, timedelta, timezone

DATE_FMT = "%Y-%m-%d"


def utc_today_str() -> str:
    """Today's date (UTC) as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime(DATE_FMT)


def utc_days_ago_str(days: int) -> str:
    """The date `days` ago (UTC) as YYYY-MM-DD."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(DATE_FMT)
