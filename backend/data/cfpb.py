"""
backend/data/cfpb.py — CFPB consumer-complaint volume by product.

Monthly complaint counts from the CFPB Consumer Complaint Database search API
(free, no key). Complaint volume in card / auto / personal-loan products is a
real-time consumer-stress read that leads charged-off receivables. CFPB has
renamed products over the years (e.g. "Credit card" → "Credit card or prepaid
card" → "Credit card"), so each series ORs all historical names.

CFPB retired the /trends/ histogram endpoint in Aug 2026 (it now serves the
site's HTML shell with a 200), so each month is one filtered `size=0` search
reading the exact `hits.total`. That's ~140 calls per series for a backfill
(paced, so a fresh DB takes ~15 min), so runs are incremental: months after the last stored one, plus a trailing
REVISE_MONTHS window because late-filed complaints keep landing in recent
months. date_received_max is inclusive.

Stored in `metrics` (serves through /api/fred/history/*), category
`consumer_stress`. The current calendar month is always partial — it's
never fetched so the chart tail doesn't fake a collapse in complaints.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone

import requests

from cache.db import get_conn, upsert_metric
from data.sources import Source

logger = logging.getLogger(__name__)

SEARCH_URL = (
    "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"
)
START_DATE = "2015-01-01"
REVISE_MONTHS = 3
# A fresh-DB backfill is ~700 calls; unpaced, CFPB starts returning 429s
# within a couple hundred. Pace every call and back off on 429.
REQUEST_INTERVAL_S = 0.5
MAX_RETRIES = 4

SERIES: list[dict] = [
    {
        "series_id": "CFPB_COMPLAINTS_CARD",
        "label": "CFPB Complaints — Credit Card (Monthly)",
        "products": ["Credit card", "Credit card or prepaid card"],
    },
    {
        "series_id": "CFPB_COMPLAINTS_AUTO",
        "label": "CFPB Complaints — Vehicle Loan/Lease (Monthly)",
        "products": ["Vehicle loan or lease"],
    },
    {
        "series_id": "CFPB_COMPLAINTS_PERSONAL",
        "label": "CFPB Complaints — Personal/Payday Loan (Monthly)",
        "products": [
            "Payday loan, title loan, personal loan, or advance loan",
            "Payday loan, title loan, or personal loan",
            "Payday loan",
            "Consumer Loan",
        ],
    },
    {
        "series_id": "CFPB_COMPLAINTS_MORTGAGE",
        "label": "CFPB Complaints — Mortgage (Monthly)",
        "products": ["Mortgage"],
    },
    {
        "series_id": "CFPB_COMPLAINTS_DEBT",
        "label": "CFPB Complaints — Debt Collection (Monthly)",
        "products": ["Debt collection"],
    },
]


def _add_month(d: date, n: int = 1) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _month_count(products: list[str], month: date) -> int:
    """Complaints received in `month` for the OR of the given product names."""
    month_end = _add_month(month) - timedelta(days=1)
    params: list[tuple[str, str]] = [
        ("size", "0"),
        ("no_aggs", "true"),
        ("date_received_min", month.isoformat()),
        ("date_received_max", month_end.isoformat()),
    ]
    params += [("product", p) for p in products]
    for attempt in range(MAX_RETRIES + 1):
        time.sleep(REQUEST_INTERVAL_S)
        resp = requests.get(SEARCH_URL, params=params, timeout=60)
        if resp.status_code != 429 or attempt == MAX_RETRIES:
            break
        retry_after = resp.headers.get("Retry-After", "")
        time.sleep(float(retry_after) if retry_after.isdigit() else 5 * 2**attempt)
    resp.raise_for_status()
    total = resp.json()["hits"]["total"]
    return int(total["value"] if isinstance(total, dict) else total)


def _last_stored_month(series_id: str) -> date | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM metrics WHERE series_id = ?", (series_id,)
        ).fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def fetch_cfpb_complaints() -> int:
    """Pull monthly complaint counts per product family. Returns rows written."""
    now = datetime.now(timezone.utc)
    current_month = now.date().replace(day=1)
    fetched_at = now.isoformat()

    count = 0
    for s in SERIES:
        last = _last_stored_month(s["series_id"])
        backfill = last is None
        month = (
            date.fromisoformat(START_DATE)
            if backfill
            else _add_month(last, -(REVISE_MONTHS - 1))
        )
        stored = 0
        try:
            while month < current_month:  # in-progress month is partial
                n = _month_count(s["products"], month)
                # Products introduced after START_DATE (auto: 2017-04) have
                # leading zero months; the old histogram omitted them.
                if not (backfill and stored == 0 and n == 0):
                    upsert_metric(
                        {
                            "series_id": s["series_id"],
                            "label": s["label"],
                            "category": "consumer_stress",
                            "date": month.isoformat(),
                            "value": float(n),
                            "fetched_at": fetched_at,
                        }
                    )
                    stored += 1
                month = _add_month(month)
        except Exception as e:
            logger.error("CFPB [%s] error at %s: %s", s["series_id"], month, e)
        logger.info("CFPB [%s] — %d months stored", s["series_id"], stored)
        count += stored

    return count


# Registered with the scheduler via data/sources.py (BUILTIN_MODULES).
# CFPB complaint counts settle with a lag; a daily pull keeps the trailing
# months fresh as late-arriving complaints post.
SOURCE = Source(
    id="cfpb",
    label="CFPB complaint volume",
    fetch=fetch_cfpb_complaints,
    every_hours=24,
    description="Monthly CFPB complaint counts by product — consumer-stress lead.",
)
