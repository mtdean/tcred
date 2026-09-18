"""
backend/data/sce.py — NY Fed Survey of Consumer Expectations ingest.

Two public XLSX files, no key required:
  * frbny-sce-data.xlsx — monthly core survey (2013→). We take the
    forward-looking consumer-stress series: mean probability of missing a
    minimum debt payment, mean probability of losing a job, year-ahead
    "credit will be harder to get" share, and year-ahead "financially worse
    off" share.
  * frbny-sce-credit-access-data.xlsx — tri-annual (Feb/Jun/Oct) credit
    access survey: application rejection rate and discouraged-borrower share.

Everything lands in `metrics` (category 'sce') so the FRED history endpoint,
panels, scorecard, and Analyst tools see them like any other series.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from cache.db import upsert_metric

logger = logging.getLogger(__name__)

BASE = "https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/data"
CORE_URL = f"{BASE}/frbny-sce-data.xlsx"
CREDIT_URL = f"{BASE}/frbny-sce-credit-access-data.xlsx"
HEADERS = {"User-Agent": "Mozilla/5.0 (SituationMonitor personal dashboard)"}


def _date(yyyymm) -> str | None:
    s = str(yyyymm).strip().split(".")[0]
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-01"
    return None


def _store(series_id: str, label: str, points: list[tuple[str, float]], now: str) -> int:
    n = 0
    for date, value in points:
        if value is None or pd.isna(value):
            continue
        upsert_metric({
            "series_id": series_id,
            "label": label,
            "category": "sce",
            "date": date,
            "value": float(value),
            "fetched_at": now,
        })
        n += 1
    return n


def _col_points(df: pd.DataFrame, col: int) -> list[tuple[str, float]]:
    out = []
    for _, row in df.iterrows():
        d = _date(row.iloc[0])
        if d is not None:
            out.append((d, row.iloc[col]))
    return out


def _sum_cols(df: pd.DataFrame, cols: list[int]) -> list[tuple[str, float]]:
    out = []
    for _, row in df.iterrows():
        d = _date(row.iloc[0])
        if d is None:
            continue
        vals = [row.iloc[c] for c in cols]
        if any(pd.isna(v) for v in vals):
            continue
        out.append((d, float(sum(vals))))
    return out


def fetch_sce() -> int:
    """Download + parse both SCE files. Returns rows stored."""
    now = datetime.now(timezone.utc).isoformat()
    stored = 0

    try:
        resp = requests.get(CORE_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        xl = pd.ExcelFile(io.BytesIO(resp.content))

        # Sheet layouts: row 0-3 headers, data from row 4; col 0 = YYYYMM.
        delinq = pd.read_excel(xl, "Delinquency expectations", header=None, skiprows=4)
        stored += _store(
            "SCE_MISS_PAYMENT_PROB",
            "SCE: Prob. of Missing Debt Payment (Mean %)",
            _col_points(delinq, 1), now,
        )

        jobsep = pd.read_excel(xl, "Job separation expectation", header=None, skiprows=4)
        stored += _store(
            "SCE_JOB_LOSS_PROB",
            "SCE: Prob. of Losing Job (Mean %)",
            _col_points(jobsep, 1), now,
        )

        # Credit availability: cols 6-7 = year-ahead "much/somewhat harder".
        credit = pd.read_excel(xl, "Credit availability", header=None, skiprows=4)
        stored += _store(
            "SCE_CREDIT_HARDER_YA",
            "SCE: Credit Harder Year-Ahead (% of HHs)",
            _sum_cols(credit, [6, 7]), now,
        )

        # Household financial situation: cols 6-7 = year-ahead "worse off".
        finsit = pd.read_excel(xl, "Household financial situation", header=None, skiprows=4)
        stored += _store(
            "SCE_FIN_WORSE_YA",
            "SCE: Financially Worse Off Year-Ahead (% of HHs)",
            _sum_cols(finsit, [6, 7]), now,
        )
    except Exception as e:
        logger.error("SCE core fetch/parse error: %s", e)

    try:
        resp = requests.get(CREDIT_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        # 'overall' sheet: row 1 is the header, data from row 2.
        df = pd.read_excel(io.BytesIO(resp.content), "overall", header=1)
        df = df[df.get("group").astype(str).str.lower() == "all"] if "group" in df else df
        rej = [( _date(r["date"]), r.get("Applied_Rejected")) for _, r in df.iterrows()]
        disc = [(_date(r["date"]), r.get("Discouraged")) for _, r in df.iterrows()]
        stored += _store(
            "SCE_REJECTION_RATE",
            "SCE: Credit Application Rejection Rate (%)",
            [(d, v) for d, v in rej if d], now,
        )
        stored += _store(
            "SCE_DISCOURAGED_RATE",
            "SCE: Discouraged Borrower Share (%)",
            [(d, v) for d, v in disc if d], now,
        )
    except Exception as e:
        logger.error("SCE credit-access fetch/parse error: %s", e)

    logger.info("SCE ingest complete — %d rows", stored)
    return stored
