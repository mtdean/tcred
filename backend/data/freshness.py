"""
backend/data/freshness.py — per-series staleness assessment.

For every FRED series we track (cadence is declared in data_sources.yaml), look
up the latest observation date in `metrics` and compare it to a cadence-aware
threshold:

    cadence    fresh           stale           dead
    daily      ≤ 5d            > 5d            > 14d   (weekends + holidays)
    weekly     ≤ 14d           > 14d           > 30d
    monthly    ≤ 60d           > 60d           > 90d
    quarterly  ≤ 130d          > 130d          > 200d

Quarterly thresholds are generous because the major FRED quarterly series
(delinquency, charge-offs, GDP) lag ~60-90 days after the period close —
'fresh' must include that publication delay.

The endpoint also surfaces last-successful run per scheduled job (from
`job_runs`) so the dashboard can flag jobs that haven't completed lately even
when the data they fetch hasn't changed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from cache import db
from config import load_data_sources


_THRESHOLDS = {
    "daily":     {"stale": 5,   "dead": 14},
    "weekly":    {"stale": 14,  "dead": 30},
    "monthly":   {"stale": 60,  "dead": 90},
    "quarterly": {"stale": 130, "dead": 200},
    "annual":    {"stale": 500, "dead": 750},
}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _status_for(days_since: int, frequency: str) -> str:
    """fresh | stale | dead — based on the cadence-specific threshold table."""
    th = _THRESHOLDS.get(frequency, _THRESHOLDS["monthly"])
    if days_since > th["dead"]:
        return "dead"
    if days_since > th["stale"]:
        return "stale"
    return "fresh"


def _latest_date(series_id: str) -> Optional[str]:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM metrics WHERE series_id = ? AND value IS NOT NULL",
            (series_id,),
        ).fetchone()
    return row["d"] if row and row["d"] else None


def fred_series_freshness() -> list[dict]:
    """One row per configured FRED series: latest_date, days_since, status."""
    cfg = load_data_sources()
    out: list[dict] = []
    today = _today()
    for s in cfg.get("fred_series", []):
        series_id = s["id"]
        frequency = (s.get("frequency") or "monthly").lower()
        latest = _latest_date(series_id)
        if latest is None:
            out.append({
                "series_id": series_id,
                "label": s.get("label"),
                "category": s.get("category"),
                "frequency": frequency,
                "latest_date": None,
                "days_since": None,
                "status": "missing",
            })
            continue
        try:
            days_since = (today - date.fromisoformat(latest)).days
        except ValueError:
            days_since = None

        out.append({
            "series_id": series_id,
            "label": s.get("label"),
            "category": s.get("category"),
            "frequency": frequency,
            "latest_date": latest,
            "days_since": days_since,
            "status": _status_for(days_since, frequency) if days_since is not None else "missing",
        })
    return out


def _summarize(rows: list[dict]) -> dict:
    """Compact rollup of statuses, suitable for the status bar."""
    counts = {"fresh": 0, "stale": 0, "dead": 0, "missing": 0}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts


def job_freshness() -> list[dict]:
    """Last run per scheduled job_id (latest_at, status, hours_since)."""
    latest = db.get_latest_job_runs()
    now = datetime.now(timezone.utc)
    out = []
    for r in latest:
        started_at = r.get("started_at")
        hours_since = None
        if started_at:
            try:
                started = datetime.fromisoformat(started_at.rstrip("Z")).replace(tzinfo=timezone.utc)
                hours_since = round((now - started).total_seconds() / 3600.0, 2)
            except ValueError:
                pass
        out.append({
            "job_id": r["job_id"],
            "status": r.get("status"),
            "started_at": started_at,
            "ended_at": r.get("ended_at"),
            "duration_ms": r.get("duration_ms"),
            "rows_ingested": r.get("rows_ingested"),
            "hours_since": hours_since,
            "error": r.get("error"),
        })
    return out


def freshness_report() -> dict:
    """Full freshness snapshot: per-series rows + summary + last-run-per-job."""
    series = fred_series_freshness()
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "series": series,
        "summary": _summarize(series),
        "jobs": job_freshness(),
    }
