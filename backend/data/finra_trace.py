"""
backend/data/finra_trace.py — TRACE securitized-product secondary volume.

FINRA-ICE Data Services publish a daily Structured Trading Activity Report
(STAR) at a stable CDN URL (re-uploaded each evening, ~8PM ET). The credit
block carries per-asset-class trade counts and dollar volume for ABS,
CBO/CDO/CLO, non-agency CMBS and non-agency CMO (RMBS), split IG vs non-IG.
That's the secondary-market liquidity view that complements the primary
new-issue spread trackers — secondary volume drying up is a stress signal
that precedes primary-market shutdowns.

The file holds ONE day per download, so history accrues from the day this
job first runs (FINRA's historic-reports page exists for manual backfill).
Stored in `metrics` (serves through /api/fred/history/*), volume in $mm,
IG + non-IG summed; suppressed cells ('*', trade count < 5) count as 0.
"""

import io
import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from cache.db import upsert_metric

logger = logging.getLogger(__name__)

STAR_URL = "https://cdn.finra.org/trace/FINRA_IDS_STAR.xlsx"
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Row label in the credit block -> series prefix. CMBS/CMO have a section
# header row whose values live on the following "P&I" row.
_CLASSES = {
    "ABS": "TRACE_ABS",
    "CBO/CDO/CLO": "TRACE_CLO",
    "NON-AGENCY CMBS": "TRACE_CMBS",
    "NON-AGENCY CMO": "TRACE_NA_CMO",
}
_LABELS = {
    "TRACE_ABS": "ABS",
    "TRACE_CLO": "CLO/CDO",
    "TRACE_CMBS": "Non-Agency CMBS",
    "TRACE_NA_CMO": "Non-Agency CMO (RMBS)",
}


def _num(v) -> float:
    """Numeric cell value; suppressed ('*') and blank cells count as 0."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_star_xlsx(content: bytes) -> tuple[str, dict[str, dict[str, float]]]:
    """Return (as_of_date, {series_prefix: {trades, volume_mm}})."""
    df = pd.read_excel(
        io.BytesIO(content), sheet_name="TradingActivity", header=None
    )

    as_of = ""
    for i in range(len(df)):
        row = df.iloc[i]
        for j, cell in enumerate(row):
            if isinstance(cell, str) and "DATA AS OF" in cell.upper():
                try:
                    as_of = pd.Timestamp(row[j + 1]).date().isoformat()
                except (ValueError, TypeError, KeyError):
                    pass
    if not as_of:
        raise ValueError("STAR: no 'DATA AS OF' date found")

    out: dict[str, dict[str, float]] = {}
    for i in range(len(df)):
        label = df.iloc[i, 1]
        if not isinstance(label, str):
            continue
        prefix = _CLASSES.get(label.strip().upper())
        if not prefix:
            continue
        row = df.iloc[i]
        # Section headers (CMBS/CMO) carry no numbers — read the P&I row below.
        if pd.isna(row[2]) or str(row[2]).strip() in ("", "nan"):
            row = df.iloc[i + 1]
        trades = _num(row[2]) + _num(row[5])
        volume_thousands = _num(row[4]) + _num(row[7])
        out[prefix] = {"trades": trades, "volume_mm": volume_thousands / 1000.0}
    return as_of, out


def fetch_trace_volumes() -> int:
    """Download today's STAR file and store the credit-block aggregates."""
    resp = requests.get(STAR_URL, headers=_UA, timeout=60)
    resp.raise_for_status()
    as_of, classes = parse_star_xlsx(resp.content)

    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for prefix, vals in classes.items():
        name = _LABELS[prefix]
        for suffix, label, value in (
            ("_VOLUME", f"TRACE {name} Volume ($mm/day)", vals["volume_mm"]),
            ("_TRADES", f"TRACE {name} Trade Count (daily)", vals["trades"]),
        ):
            upsert_metric(
                {
                    "series_id": prefix + suffix,
                    "label": label,
                    "category": "trace_liquidity",
                    "date": as_of,
                    "value": value,
                    "fetched_at": now,
                }
            )
            count += 1

    logger.info("TRACE STAR: %d rows stored for %s", count, as_of)
    return count
