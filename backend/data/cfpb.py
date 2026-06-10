"""
backend/data/cfpb.py — CFPB consumer-complaint volume by product.

Monthly complaint counts from the CFPB Consumer Complaint Database trends API
(free, no key). Complaint volume in card / auto / personal-loan products is a
real-time consumer-stress read that leads charged-off receivables. CFPB has
renamed products over the years (e.g. "Credit card" → "Credit card or prepaid
card" → "Credit card"), so each series ORs all historical names.

Stored in `metrics` (serves through /api/fred/history/*), category
`consumer_stress`. The current calendar month is always partial — it's
dropped so the chart tail doesn't fake a collapse in complaints.
"""

import logging
from datetime import datetime, timezone

import requests

from cache.db import upsert_metric

logger = logging.getLogger(__name__)

TRENDS_URL = (
    "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/trends/"
)
START_DATE = "2015-01-01"

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


def _monthly_counts(products: list[str]) -> list[tuple[str, int]]:
    """[(YYYY-MM-01, count)] for the OR of the given product names."""
    params: list[tuple[str, str]] = [
        ("lens", "overview"),
        ("trend_interval", "month"),
        ("date_received_min", START_DATE),
        ("date_received_max", datetime.now(timezone.utc).date().isoformat()),
    ]
    params += [("product", p) for p in products]
    resp = requests.get(TRENDS_URL, params=params, timeout=60)
    resp.raise_for_status()
    area = resp.json().get("aggregations", {}).get("dateRangeArea", {})
    # The histogram nests one level deeper when filters are applied.
    buckets = area.get("dateRangeArea", area).get("buckets", [])
    out = []
    for b in buckets:
        date = str(b.get("key_as_string", ""))[:10]
        if date:
            out.append((date, int(b.get("doc_count", 0))))
    return out


def fetch_cfpb_complaints() -> int:
    """Pull monthly complaint counts per product family. Returns rows written."""
    now = datetime.now(timezone.utc)
    current_month = now.date().replace(day=1).isoformat()
    fetched_at = now.isoformat()

    count = 0
    for s in SERIES:
        try:
            months = _monthly_counts(s["products"])
        except Exception as e:
            logger.error("CFPB [%s] error: %s", s["series_id"], e)
            continue
        stored = 0
        for date, n in months:
            if date >= current_month:  # in-progress month is partial
                continue
            upsert_metric(
                {
                    "series_id": s["series_id"],
                    "label": s["label"],
                    "category": "consumer_stress",
                    "date": date,
                    "value": float(n),
                    "fetched_at": fetched_at,
                }
            )
            stored += 1
        logger.info("CFPB [%s] — %d months stored", s["series_id"], stored)
        count += stored

    return count
