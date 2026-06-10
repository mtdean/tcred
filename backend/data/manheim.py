"""
backend/data/manheim.py — Manheim Used Vehicle Value Index (Cox Automotive).

Wholesale used-vehicle values drive auto-ABS recovery rates / loss severity —
nothing else in the stack covers collateral values. Cox publishes a monthly
XLSX (full history to 1/1997) linked from the Manheim consulting page; the
file URL is re-dated every month, so we scrape the page for the current link
rather than hard-coding it. The public file can lag the press release by a
few weeks.

Stored in `metrics` (so it serves through /api/fred/history/*):
  MANHEIM_UVVI      — seasonally-adjusted index (Jan 1997 = 100)
  MANHEIM_UVVI_YOY  — SA index, % change year-over-year
"""

import io
import logging
import re
from datetime import datetime, timezone

import pandas as pd
import requests

from cache.db import upsert_metric

logger = logging.getLogger(__name__)

PAGE_URL = "https://site.manheim.com/en/services/consulting/used-vehicle-value-index.html"
_XLSX_RE = re.compile(r'href="(https://[^"]*?Manheim[^"]*?Index[^"]*?\.xlsx)"', re.I)
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _find_xlsx_url() -> str | None:
    resp = requests.get(PAGE_URL, headers=_UA, timeout=30)
    resp.raise_for_status()
    m = _XLSX_RE.search(resp.text)
    return m.group(1) if m else None


def _pick_col(cols: list[str], *needles: str) -> str | None:
    for c in cols:
        lc = str(c).lower()
        if all(n in lc for n in needles):
            return c
    return None


def parse_manheim_xlsx(content: bytes) -> list[tuple[str, float, float | None]]:
    """Return [(date, sa_index, yoy_pct_or_None)] from the DATA sheet."""
    df = pd.read_excel(io.BytesIO(content), sheet_name="DATA")
    cols = list(df.columns)
    date_col = cols[0]
    idx_col = _pick_col(cols, "index", "100")
    yoy_col = _pick_col(cols, "index", "yoy")
    if idx_col is None:
        raise ValueError(f"Manheim XLSX: no index column in {cols}")

    out: list[tuple[str, float, float | None]] = []
    for _, row in df.iterrows():
        try:
            date = pd.Timestamp(row[date_col]).date().isoformat()
        except (ValueError, TypeError):
            continue
        idx = row[idx_col]
        if pd.isna(idx):
            continue
        yoy = row[yoy_col] if yoy_col is not None and pd.notna(row[yoy_col]) else None
        # The file stores YoY as a fraction (0.0286 = 2.86%).
        out.append((date, float(idx), float(yoy) * 100 if yoy is not None else None))
    return out


def fetch_manheim_index() -> int:
    """Scrape the current XLSX link, parse, store. Returns rows written."""
    url = _find_xlsx_url()
    if not url:
        logger.error("Manheim: no XLSX link found on %s", PAGE_URL)
        return 0

    resp = requests.get(url, headers=_UA, timeout=60)
    resp.raise_for_status()
    rows = parse_manheim_xlsx(resp.content)

    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for date, idx, yoy in rows:
        upsert_metric(
            {
                "series_id": "MANHEIM_UVVI",
                "label": "Manheim Used Vehicle Value Index (SA)",
                "category": "auto",
                "date": date,
                "value": idx,
                "fetched_at": now,
            }
        )
        count += 1
        if yoy is not None:
            upsert_metric(
                {
                    "series_id": "MANHEIM_UVVI_YOY",
                    "label": "Manheim Used Vehicle Value Index (YoY %)",
                    "category": "auto",
                    "date": date,
                    "value": yoy,
                    "fetched_at": now,
                }
            )
            count += 1

    last = rows[-1][0] if rows else "—"
    logger.info("Manheim: %d rows stored (latest %s) from %s", count, last, url)
    return count
