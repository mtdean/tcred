"""
backend/data/fred.py — FRED API fetcher.

Pulls configured series and stores in the metrics table.
Uses fredapi library; falls back to raw requests if not installed.
"""

import bisect
import logging
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Optional

from config import load_data_sources, settings
from cache.db import upsert_metric

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_fred_series() -> int:
    """Fetch all configured FRED series. Returns count of data points stored."""
    if not settings.FRED_API_KEY:
        logger.warning("FRED_API_KEY not set — skipping FRED fetch")
        return 0

    try:
        from fredapi import Fred
        fred = Fred(api_key=settings.FRED_API_KEY)
    except ImportError:
        logger.error("fredapi not installed — run: pip install fredapi")
        return 0

    cfg = load_data_sources()
    series_list = cfg.get("fred_series", [])
    now = _now()
    count = 0

    for s in series_list:
        series_id = s["id"]
        label = s["label"]
        category = s["category"]

        try:
            data = fred.get_series(series_id, observation_start="2015-01-01")
            data = data.dropna()

            for date, value in data.items():
                upsert_metric(
                    {
                        "series_id": series_id,
                        "label": label,
                        "category": category,
                        "date": str(date.date()),
                        "value": float(value),
                        "fetched_at": now,
                    }
                )
                count += 1

            logger.info(f"FRED [{series_id}] — {len(data)} observations stored")

        except Exception as e:
            logger.error(f"FRED [{series_id}] error: {e}")

    return count


def get_latest_fred_values() -> list[dict]:
    """Return most recent value for each configured FRED series (from DB)."""
    from cache.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT series_id, label, category, date, value
            FROM metrics
            WHERE (series_id, date) IN (
                SELECT series_id, MAX(date)
                FROM metrics
                GROUP BY series_id
            )
            ORDER BY category, label
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_series_history(series_id: str, limit: int = 120) -> list[dict]:
    """Return time series for a specific FRED series."""
    from cache.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT date, value FROM metrics
            WHERE series_id = ?
            ORDER BY date DESC LIMIT ?
            """,
            (series_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# Tenor → FRED series for the Treasury forward-curve panel.
# Order matters: this is the x-axis order on the chart.
FORWARD_CURVE_TENORS: list[tuple[str, str]] = [
    ("3M", "DTB3"),
    ("6M", "DTB6"),
    ("1Y", "DGS1"),
    ("2Y", "DGS2"),
    ("3Y", "DGS3"),
    ("5Y", "DGS5"),
    ("7Y", "DGS7"),
    ("10Y", "DGS10"),
    ("20Y", "DGS20"),
    ("30Y", "DGS30"),
]

# Treasury yields are daily; ~21 trading days/month.
_OBS_6MO = 126
_OBS_1YR = 252


def get_forward_curve() -> dict:
    """
    Build the Treasury yield curve at three snapshots: today, ~6 months ago,
    and ~1 year ago. For each tenor we walk the stored daily observations and
    pick the latest, the ~126th-from-latest, and the ~252nd-from-latest.

    Returns:
      {
        "tenors": ["3M", ...],
        "today":          {"3M": 5.21, ...},
        "six_months_ago": {"3M": 5.05, ...},
        "one_year_ago":   {"3M": 4.80, ...},
        "as_of": {"today": "2026-05-23", "six_months_ago": "...", "one_year_ago": "..."}
      }
    Missing observations are returned as null so the frontend can gap the line.
    """
    from cache.db import get_conn

    today: dict[str, Optional[float]] = {}
    six_mo: dict[str, Optional[float]] = {}
    one_yr: dict[str, Optional[float]] = {}
    as_of: dict[str, Optional[str]] = {"today": None, "six_months_ago": None, "one_year_ago": None}

    with get_conn() as conn:
        for tenor, series_id in FORWARD_CURVE_TENORS:
            rows = conn.execute(
                """
                SELECT date, value FROM metrics
                WHERE series_id = ? AND value IS NOT NULL
                ORDER BY date DESC LIMIT ?
                """,
                (series_id, _OBS_1YR + 1),
            ).fetchall()

            def _pick(idx: int) -> tuple[Optional[float], Optional[str]]:
                if idx < len(rows):
                    return float(rows[idx]["value"]), rows[idx]["date"]
                return None, None

            t_val, t_date = _pick(0)
            s_val, s_date = _pick(_OBS_6MO)
            y_val, y_date = _pick(_OBS_1YR)

            today[tenor] = t_val
            six_mo[tenor] = s_val
            one_yr[tenor] = y_val

            # Use the longest/most-complete tenor's dates as the snapshot labels.
            if t_date and as_of["today"] is None:
                as_of["today"] = t_date
            if s_date and as_of["six_months_ago"] is None:
                as_of["six_months_ago"] = s_date
            if y_date and as_of["one_year_ago"] is None:
                as_of["one_year_ago"] = y_date

    return {
        "tenors": [t for t, _ in FORWARD_CURVE_TENORS],
        "today": today,
        "six_months_ago": six_mo,
        "one_year_ago": one_yr,
        "as_of": as_of,
    }


# SOFR rates panel: 1M / 3M from FRED averages, 1Y computed from the SOFR Index.
_SOFR_1M = "SOFR30DAYAVG"
_SOFR_3M = "SOFR90DAYAVG"
_SOFR_INDEX = "SOFRINDEX"
_SOFR_DAYCOUNT = 360.0  # SOFR averages use an ACT/360 convention.


def _compounded_sofr_1y(
    idx_dates: list[str],
    idx_map: dict[str, float],
    on_date: str,
    end_value: float,
) -> Optional[float]:
    """
    Trailing ~1-year compounded SOFR (in percent) ending on `on_date`, derived
    from the SOFR Index: rate = (idx_end / idx_start - 1) * (360 / days).
    `idx_start` is the most recent index obs on or before on_date − 365 days.
    """
    target = (date_cls.fromisoformat(on_date) - timedelta(days=365)).isoformat()
    pos = bisect.bisect_right(idx_dates, target) - 1  # last obs <= target
    if pos < 0:
        return None
    start_date = idx_dates[pos]
    start_value = idx_map[start_date]
    days = (date_cls.fromisoformat(on_date) - date_cls.fromisoformat(start_date)).days
    if not start_value or days <= 0:
        return None
    return (end_value / start_value - 1.0) * (_SOFR_DAYCOUNT / days) * 100.0


def get_sofr_rates(limit: int = 300) -> list[dict]:
    """
    Return a merged SOFR rate history: 1M and 3M term averages (FRED) plus a
    trailing 1Y compounded rate computed from the SOFR Index.
    Rows: {date, m1, m3, y1} ascending by date, most recent `limit` rows.
    """
    from cache.db import get_conn

    with get_conn() as conn:
        def _fetch(sid: str) -> list[tuple[str, float]]:
            rows = conn.execute(
                "SELECT date, value FROM metrics WHERE series_id = ? AND value IS NOT NULL ORDER BY date ASC",
                (sid,),
            ).fetchall()
            return [(r["date"], float(r["value"])) for r in rows]

        idx = _fetch(_SOFR_INDEX)
        m1 = dict(_fetch(_SOFR_1M))
        m3 = dict(_fetch(_SOFR_3M))

    idx_dates = [d for d, _ in idx]
    idx_map = dict(idx)

    out: list[dict] = []
    for d, ival in idx:
        y1 = _compounded_sofr_1y(idx_dates, idx_map, d, ival)
        row = {
            "date": d,
            "m1": m1.get(d),
            "m3": m3.get(d),
            "y1": round(y1, 4) if y1 is not None else None,
        }
        if row["m1"] is not None or row["m3"] is not None or row["y1"] is not None:
            out.append(row)

    return out[-limit:]
