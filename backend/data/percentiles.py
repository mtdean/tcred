"""
backend/data/percentiles.py — historical percentile context for any metric.

A bare number ("HY OAS = 380bps") tells you less than a regime read ("380bps,
62nd percentile of the last 5y"). For every series_id in `metrics`, this
module computes where the most-recent value sits in its own trailing
distribution.

Percentile semantics: fraction of historical observations that are STRICTLY
LESS THAN the current value, expressed 0-100. Tied observations are counted
in the denominator but not the numerator (a value equal to the current one
does not count as "below"). This is the "strict-less-than" percentile —
intuitive when the user asks "how high is this vs history": a value at the
all-time high reads as 100; a value at the all-time low reads as 0; ties
land between those poles.

Window defaults to ~5 years (1825 days). For series with sparse history
(quarterly), the function returns whatever's available and reports `n_obs`
so the caller can decide whether to trust the percentile.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from cache import db


DEFAULT_WINDOW_DAYS = 1825  # ~5 years


def _today() -> date:
    return datetime.now(timezone.utc).date()


def compute_percentile(
    series_id: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Optional[dict]:
    """Where does the latest value sit in the trailing `window_days` history?

    Returns None if the series has no observations. Returns `n_obs: 1` with
    `percentile: 50.0` for a single-observation series (no real distribution).
    """
    cutoff = (_today() - timedelta(days=window_days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics WHERE series_id = ? "
            "AND date >= ? AND value IS NOT NULL ORDER BY date ASC",
            (series_id, cutoff),
        ).fetchall()

    if not rows:
        return None

    values = [float(r["value"]) for r in rows]
    latest_value = values[-1]
    latest_date = rows[-1]["date"]
    n = len(values)

    if n == 1:
        return {
            "series_id": series_id,
            "value": latest_value,
            "as_of": latest_date,
            "window_days": window_days,
            "n_obs": 1,
            "percentile": 50.0,
            "min": latest_value,
            "max": latest_value,
            "median": latest_value,
        }

    below = sum(1 for v in values if v < latest_value)
    percentile = (below / n) * 100.0

    sorted_vals = sorted(values)
    median = (
        sorted_vals[n // 2]
        if n % 2 == 1
        else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    )

    return {
        "series_id": series_id,
        "value": latest_value,
        "as_of": latest_date,
        "window_days": window_days,
        "n_obs": n,
        "percentile": round(percentile, 1),
        "min": min(values),
        "max": max(values),
        "median": median,
    }


def compute_percentiles(
    series_ids: list[str],
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, dict]:
    """Batch compute — keyed by series_id. Missing series are simply absent."""
    out: dict[str, dict] = {}
    for sid in series_ids:
        p = compute_percentile(sid, window_days=window_days)
        if p is not None:
            out[sid] = p
    return out
