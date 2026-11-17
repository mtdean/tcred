"""
plugins/_example_treasury_rates.py — template data-source plugin.

Average interest rate the Treasury pays on its marketable debt (bills, notes,
bonds), monthly, from the Treasury Fiscal Data API (free, no key).

This file starts with `_`, so it is NOT loaded. To try it:

    cp plugins/_example_treasury_rates.py plugins/treasury_rates.py
    # restart the server → a "Treasury Avg Interest Rates" chart appears
    # at the bottom of the Macro tab once the first fetch lands.

Everything a plugin needs is in this one file: fetch the data, write it with
`store_series`, and declare a `SOURCE`. See plugins/README.md for the rules.
"""

import requests

from data.sources import Panel, Series, Source, store_series

API_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v2/accounting/od/avg_interest_rates"
)

# API security_desc → our series id (keep ids unique across all sources —
# prefixing with the source name is the easy way).
SERIES = {
    "Treasury Bills": "TSY_AVG_RATE_BILLS",
    "Treasury Notes": "TSY_AVG_RATE_NOTES",
    "Treasury Bonds": "TSY_AVG_RATE_BONDS",
    "Total Marketable": "TSY_AVG_RATE_MARKETABLE",
}


def fetch() -> int:
    """Pull the full monthly history and upsert it. Returns rows written.

    Re-pulling everything is fine here (~600 rows, one request): store_series
    replaces existing (series_id, date) points, so it's idempotent.
    """
    resp = requests.get(
        API_URL,
        params={
            "fields": "record_date,security_desc,avg_interest_rate_amt",
            "filter": "record_date:gte:2015-01-01,security_desc:in:("
            + ",".join(SERIES) + ")",
            "page[size]": "10000",
        },
        timeout=60,
    )
    resp.raise_for_status()  # errors propagate → recorded in job_runs

    points: dict[str, list[tuple[str, float]]] = {sid: [] for sid in SERIES.values()}
    for row in resp.json()["data"]:
        sid = SERIES.get(row["security_desc"])
        if sid and row["avg_interest_rate_amt"] not in (None, "", "null"):
            # Month-end record dates → first of month, the metrics convention
            # for monthly data.
            month = row["record_date"][:8] + "01"
            points[sid].append((month, float(row["avg_interest_rate_amt"])))

    labels = {sid: f"Treasury Avg Interest Rate — {desc}" for desc, sid in SERIES.items()}
    return sum(store_series(sid, labels[sid], pts) for sid, pts in points.items())


SOURCE = Source(
    id="treasury_rates",
    label="Treasury average interest rates",
    description="Avg rate on marketable Treasury debt by security type (Fiscal Data API).",
    fetch=fetch,
    every_hours=24,  # monthly data; a daily check catches each release
    panel=Panel(
        title="Treasury Avg Interest Rates",
        subtitle="% — AVG RATE ON OUTSTANDING MARKETABLE DEBT",
        unit="pct",
        decimals=2,
        frequency="monthly",
        series=(
            Series("TSY_AVG_RATE_MARKETABLE", "TOTAL MARKETABLE"),
            Series("TSY_AVG_RATE_BILLS", "BILLS"),
            Series("TSY_AVG_RATE_NOTES", "NOTES"),
            Series("TSY_AVG_RATE_BONDS", "BONDS"),
        ),
    ),
)
