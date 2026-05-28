"""
backend/data/h8.py — Fed H.8 Bank Credit Monitor (computed analytics).

The raw H.8 weekly series (TOTLL, CONSUMER, CCLACBW027SBOG, CILACBW027SBOG,
REALLN, DPSACBW027SBOG) are pulled into the `metrics` table by the existing
FRED fetcher under the `h8_bank_credit` category. This module is read-only
analytics over that cache:

  - compute_h8_metrics():     per-series WoW change, WoW %, 4-week MA, YoY %,
                              plus a 104-week history slice (YoY trail).
  - compute_credit_impulse(): quarterly resampled TOTLL changes scaled by
                              nominal GDP, annualized — a directional read on
                              bank-credit creation.

Style mirrors data/indicators.py (logger, type hints, no surprises).
"""

import logging

import pandas as pd

from cache.db import get_conn

logger = logging.getLogger(__name__)

# Raw H.8 weekly series we report on. Ordering controls the table row order in
# the frontend (TOTLL first so its latest_date drives the panel header).
_H8_SERIES = (
    "TOTLL",
    "CONSUMER",
    "CCLACBW027SBOG",
    "CILACBW027SBOG",
    "REALLN",
    "DPSACBW027SBOG",
)

# Trailing weeks of history to compute on (covers a full YoY window plus
# a little headroom for the 4-week MA at the leading edge).
_LOOKBACK_WEEKS = 104


def _round(v: float | None, digits: int) -> float | None:
    """Round only finite numbers; pass None through unchanged."""
    if v is None or not pd.notna(v):
        return None
    return round(float(v), digits)


def compute_h8_metrics() -> list[dict]:
    """For each H.8 series, return the latest level and WoW/4w-MA/YoY analytics."""
    out: list[dict] = []
    with get_conn() as conn:
        for series_id in _H8_SERIES:
            rows = conn.execute(
                "SELECT series_id, date, value, label FROM metrics "
                "WHERE series_id = ? AND value IS NOT NULL "
                "ORDER BY date DESC LIMIT ?",
                (series_id, _LOOKBACK_WEEKS),
            ).fetchall()
            if not rows:
                continue

            df = pd.DataFrame(
                [(r["date"], float(r["value"])) for r in rows],
                columns=["date", "value"],
            ).sort_values("date").reset_index(drop=True)

            df["wow_chg"] = df["value"].diff()
            df["wow_pct"] = df["value"].pct_change() * 100.0
            df["ma4w"] = df["value"].rolling(window=4, min_periods=1).mean()
            df["yoy_pct"] = df["value"].pct_change(periods=52) * 100.0

            latest = df.iloc[-1]
            label = rows[0]["label"]

            history = [
                {
                    "date": str(r["date"]),
                    "value": _round(r["value"], 2),
                    "yoy_pct": _round(r["yoy_pct"], 2),
                }
                for _, r in df.iterrows()
            ]

            out.append(
                {
                    "series_id": series_id,
                    "label": label,
                    "latest_date": str(latest["date"]),
                    "latest_value": _round(latest["value"], 2),
                    "wow_change": _round(latest["wow_chg"], 2),
                    "wow_pct": _round(latest["wow_pct"], 3),
                    "yoy_pct": _round(latest["yoy_pct"], 3),
                    "ma4w": _round(latest["ma4w"], 2),
                    "history": history,
                }
            )

    logger.info("H.8 metrics: computed %d series", len(out))
    return out


def compute_credit_impulse() -> list[dict]:
    """Quarterly credit impulse: ΔQ(TOTLL) / GDP × 400 (annualized %)."""
    with get_conn() as conn:
        def _series(sid: str) -> pd.DataFrame:
            rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id = ? AND value IS NOT NULL ORDER BY date",
                (sid,),
            ).fetchall()
            if not rows:
                return pd.DataFrame(columns=["date", "value"])
            return pd.DataFrame(
                [(r["date"], float(r["value"])) for r in rows],
                columns=["date", "value"],
            )

        totll = _series("TOTLL")
        gdp = _series("GDP")

    if totll.empty or gdp.empty:
        logger.warning("H.8 credit impulse: need TOTLL and GDP in metrics first")
        return []

    # Quarterly mean of the weekly loan stock; quarter-end labels so the index
    # aligns with FRED's GDP quarter-start dates after we shift.
    totll["date"] = pd.to_datetime(totll["date"])
    totll_q = (
        totll.set_index("date")["value"]
        .resample("QE")
        .mean()
        .to_frame("total_loans")
    )
    totll_q["qoq_change"] = totll_q["total_loans"].diff()

    # GDP is quarterly already; forward-fill onto the quarter-end grid so each
    # TOTLL quarter has a contemporaneous GDP value.
    gdp["date"] = pd.to_datetime(gdp["date"])
    gdp_q = gdp.set_index("date")["value"].resample("QE").mean().ffill()

    df = totll_q.join(gdp_q.rename("gdp_value"), how="left")
    df["gdp_value"] = df["gdp_value"].ffill()

    # ΔTOTLL is in $billions (matching GDP); ×400 annualizes a quarterly fraction.
    df["credit_impulse"] = df["qoq_change"] / df["gdp_value"] * 400.0

    tail = df.tail(20)
    out: list[dict] = []
    for idx, row in tail.iterrows():
        out.append(
            {
                "date": idx.strftime("%Y-%m-%d"),
                "total_loans": _round(row["total_loans"], 2),
                "qoq_change": _round(row["qoq_change"], 2),
                "credit_impulse": _round(row["credit_impulse"], 3),
            }
        )

    logger.info("H.8 credit impulse: computed %d quarterly points", len(out))
    return out
