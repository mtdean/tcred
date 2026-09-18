"""
backend/data/sba.py — SBA 7(a) / 504 monthly lending activity ingest.

One stable public XLSX (SBA open data, updated monthly) carries monthly
approval counts and dollars for both programs back to FY1991 — real-time
small-business credit formation, the demand-side complement to the SLOOS
small-firm tightening series.

Source: https://data.sba.gov/dataset/7a-504-activity-reports-current-month
Everything lands in `metrics` (category 'sba').
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from cache.db import upsert_metric

logger = logging.getLogger(__name__)

XLSX_URL = (
    "https://sba-llms-prd-public.sbalenderportal.com/"
    "SBA-Monthly-MonthlyYearlyActivity7a504.xlsx"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (SituationMonitor personal dashboard)"}


def _date(yyyymm) -> str | None:
    s = str(yyyymm).strip().split(".")[0]
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-01"
    return None


def fetch_sba_activity() -> int:
    """Download + parse the Monthly sheet. Returns rows stored."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.get(XLSX_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        # Layout (header rows 0-3, data from 4): 7(a) block in cols 0-5,
        # 504 block in cols 7-11; Approval Month = YYYYMM.
        df = pd.read_excel(io.BytesIO(resp.content), "Monthly", header=None, skiprows=4)
    except Exception as e:
        logger.error("SBA activity fetch/parse error: %s", e)
        return 0

    series = [
        # (series_id, label, month_col, value_col, scale)
        ("SBA_7A_LOANS", "SBA 7(a) Approved Loans (Monthly Count)", 1, 3, 1.0),
        ("SBA_7A_DOLLARS", "SBA 7(a) Approved Dollars (Monthly, $mm)", 1, 4, 1e-6),
        ("SBA_504_LOANS", "SBA 504 Approved Loans (Monthly Count)", 8, 10, 1.0),
        ("SBA_504_DOLLARS", "SBA 504 Approved Dollars (Monthly, $mm)", 8, 11, 1e-6),
    ]

    stored = 0
    for series_id, label, mcol, vcol, scale in series:
        for _, row in df.iterrows():
            d = _date(row.iloc[mcol]) if mcol < len(row) else None
            v = row.iloc[vcol] if vcol < len(row) else None
            if d is None or v is None or pd.isna(v):
                continue
            try:
                value = float(v) * scale
            except (TypeError, ValueError):
                continue
            upsert_metric({
                "series_id": series_id,
                "label": label,
                "category": "sba",
                "date": d,
                "value": value,
                "fetched_at": now,
            })
            stored += 1

    logger.info("SBA activity ingest complete — %d rows", stored)
    return stored
