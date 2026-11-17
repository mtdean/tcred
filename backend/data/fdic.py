"""
backend/data/fdic.py — FDIC Call Report industry aggregates (BankFind Suite).

Quarterly bank credit metrics summed across every FDIC-insured institution,
split by bank size, from the free BankFind `financials` endpoint
(https://api.fdic.gov/banks/financials — the old banks.data.fdic.gov/api host
301s there). No key. The endpoint aggregates server-side
(`agg_by=REPDTE&agg_sum_fields=...`), so a full 2000→today pull is one
request per size bucket (~110 rows each), not 4,000 banks × 100 quarters.

Size buckets use each bank's own total assets (ASSET, $000s) at that REPDTE:
  SMALLBANK  assets <  $10bn  (community + regional)
  LARGEBANK  assets >= $10bn
  TOTAL      = SMALLBANK + LARGEBANK (summed locally)
The $10bn line is nominal and fixed, so banks migrate across it over time.

Two sources:

`fdic` — small-business lending, Call Report RC-C Part II:
  LNCI4   C&I loans with original amounts <= $1mm, balance ($000s)
  LNCI4N  ... number of loans
  (LNCI1/2/3 are the <$100k / $100–250k / $250k–1mm sub-buckets; not stored.)
  Reporting frequency reality, verified per bank against the API:
    * through 2009   — June Call Report only. BankFind carries the June value
                       forward into Sep/Dec/next Mar, so those quarters are
                       copies, not observations → only Q2 is stored.
    * 2010 – 2016    — quarterly for every bank.
    * 2017 →         — FFIEC 051 filers (small banks, <$1bn then <$5bn)
                       report Part II only in June and December; Q1/Q3 sums
                       cover just the ~1,000 031/041 filers. So SMALLBANK and
                       TOTAL store Q2/Q4 only; LARGEBANK (never 051-eligible)
                       stays quarterly.
  PPP loans (2020Q2–2022) are inside LNCI4 and produce a hump. They can't be
  netted out: PPPLNBAL also counts PPP loans >$1mm and non-C&I PPP, so
  LNCI4 − PPPLNBAL goes nonsensically low. Read 2020–21 with that in mind.

`fdic_rates` — credit quality FRED lacks by bank size (FRED's Fed release
splits top-100 vs. other banks, and has no auto line):
  NCCI / LNCI                        C&I noncurrent rate (90+ DPD + nonaccrual)
  NTCIQ*4 / avg(LNCI_t, LNCI_t-1)    C&I net charge-off rate, annualized
  NCAUTO / LNAUTO, NTAUTOQ*4 / avg   auto noncurrent + NCO rate (all banks;
                                     the auto split starts 2011Q1)
  Rates are ratios of summed dollars, never averages of bank ratios.

Dates are the first day of the REPDTE quarter (20260630 → 2026-04-01),
matching FRED and data/hhdc.py. Runs are incremental: from REVISE_QUARTERS
before the last stored quarter (amended Call Reports), plus one extra quarter
for the NCO average-loans denominator. Weekly cadence — Call Reports land
~30 days after quarter end and FDIC loads them within weeks.
"""

from __future__ import annotations

import logging
import time
from datetime import date

import requests

from cache.db import get_conn
from data.sources import Source, store_series

logger = logging.getLogger(__name__)

API_URL = "https://api.fdic.gov/banks/financials"
START_REPDTE = "20000101"
REVISE_QUARTERS = 3
LARGE_BANK_ASSETS_K = 10_000_000  # $10bn in the API's $000s
HEADERS = {"User-Agent": "SituationMonitor/0.1 (personal research dashboard)"}
MAX_RETRIES = 3

BUCKET_FILTERS = {
    "SMALLBANK": f"ASSET:[0 TO {LARGE_BANK_ASSETS_K - 1}]",
    "LARGEBANK": f"ASSET:[{LARGE_BANK_ASSETS_K} TO *]",
}
BUCKET_LABELS = {
    "SMALLBANK": "banks < $10bn",
    "LARGEBANK": "banks >= $10bn",
    "TOTAL": "all banks",
}

SBL_FIELDS = ("LNCI4", "LNCI4N")
RATE_FIELDS = ("LNCI", "NCCI", "NTCIQ", "LNAUTO", "NCAUTO", "NTAUTOQ")


# ── Helpers ──────────────────────────────────────────────────────────────────

def repdte_to_date(repdte: str) -> str:
    """'20260630' -> '2026-04-01' (first day of the report quarter)."""
    y, m = int(repdte[:4]), int(repdte[4:6])
    return f"{y:04d}-{(m - 1) // 3 * 3 + 1:02d}-01"


def _date_to_repdte(d: date) -> str:
    """Quarter-start date -> that quarter's REPDTE."""
    end_month = d.month + 2
    last_day = {3: 31, 6: 30, 9: 30, 12: 31}[end_month]
    return f"{d.year:04d}{end_month:02d}{last_day:02d}"


def _shift_quarters(d: date, n: int) -> date:
    m = d.year * 12 + d.month - 1 + 3 * n
    return date(m // 12, m % 12 + 1, 1)


def _start_repdte(anchor_series: str) -> str:
    """Incremental window start: REVISE_QUARTERS (+1 for NCO averages) back."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM metrics WHERE series_id = ?", (anchor_series,)
        ).fetchone()
    if not row or not row[0]:
        return START_REPDTE
    start = _shift_quarters(date.fromisoformat(row[0]), -(REVISE_QUARTERS + 1))
    return max(_date_to_repdte(start), START_REPDTE)


def _fetch_bucket(bucket: str, fields: tuple[str, ...], start_repdte: str) -> dict[str, dict]:
    """Server-side sums of `fields` by REPDTE for one size bucket.

    Returns {REPDTE: {field: sum, ...}}, raw $000s / counts."""
    params = {
        "filters": f"REPDTE:[{start_repdte} TO 99991231] AND {BUCKET_FILTERS[bucket]}",
        "agg_by": "REPDTE",
        "agg_sum_fields": ",".join(fields),
        "agg_limit": 1000,
        "limit": 1000,
    }
    for attempt in range(MAX_RETRIES + 1):
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=90)
        if resp.status_code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
            break
        time.sleep(5 * 2**attempt)
    resp.raise_for_status()
    payload = resp.json()
    out: dict[str, dict] = {}
    for item in payload.get("data") or []:
        row = item.get("data") or {}
        repdte = str(row.get("REPDTE") or "")
        missing = [f for f in fields if f"sum_{f}" not in row]
        if len(repdte) != 8 or missing:
            # Aggregation silently ignored → per-bank rows came back instead.
            raise RuntimeError(
                f"FDIC aggregation response malformed for {bucket}: "
                f"REPDTE={repdte!r} missing {missing}"
            )
        out[repdte] = {f: float(row[f"sum_{f}"] or 0) for f in fields}
    if not out:
        logger.warning("FDIC [%s] — no rows since %s", bucket, start_repdte)
    return out


def _fetch_all_buckets(fields: tuple[str, ...], start_repdte: str) -> dict[str, dict[str, dict]]:
    """{bucket: {REPDTE: sums}} for SMALLBANK, LARGEBANK and derived TOTAL."""
    data = {b: _fetch_bucket(b, fields, start_repdte) for b in BUCKET_FILTERS}
    common = sorted(set(data["SMALLBANK"]) & set(data["LARGEBANK"]))
    data["TOTAL"] = {
        r: {f: data["SMALLBANK"][r][f] + data["LARGEBANK"][r][f] for f in fields}
        for r in common
    }
    return data


def sbl_reported(repdte: str, bucket: str) -> bool:
    """Is RC-C Part II a real, complete observation for this bucket/quarter?"""
    year, month = int(repdte[:4]), int(repdte[4:6])
    if year < 2010:
        return month == 6  # June-only; other quarters are BankFind carry-forwards
    if year >= 2017 and bucket != "LARGEBANK":
        return month in (6, 12)  # FFIEC 051 filers report semiannually
    return True


# ── Small-business lending ──────────────────────────────────────────────────

def fetch_small_business() -> int:
    start = _start_repdte("FDIC_SBL_CI_LARGEBANK")
    data = _fetch_all_buckets(SBL_FIELDS, start)
    n = 0
    for bucket, rows in data.items():
        kept = {r: v for r, v in rows.items() if sbl_reported(r, bucket)}
        dates = sorted(kept)
        who = BUCKET_LABELS[bucket]
        n += store_series(
            f"FDIC_SBL_CI_{bucket}",
            f"FDIC Small-Business C&I Loans <=$1mm — {who} ($bn)",
            [(repdte_to_date(r), kept[r]["LNCI4"] / 1e6) for r in dates
             if kept[r]["LNCI4"] > 0],
            category="small_business",
        )
        n += store_series(
            f"FDIC_SBL_CI_NUM_{bucket}",
            f"FDIC Small-Business C&I Loans <=$1mm — {who} (# loans, mm)",
            [(repdte_to_date(r), kept[r]["LNCI4N"] / 1e6) for r in dates
             if kept[r]["LNCI4N"] > 0],
            category="small_business",
        )
    logger.info("FDIC small-business — %d points since %s", n, start)
    return n


# ── Credit-quality rates ─────────────────────────────────────────────────────

def _rate_points(rows: dict[str, dict], num: str, den: str, *, flow: bool) -> list[tuple[str, float]]:
    """Ratio of summed dollars, in %. Flow numerators (quarterly NCOs) are
    annualized ×4 over average loans (this and prior quarter-end, when the
    prior quarter is in the window; otherwise period-end)."""
    pts = []
    dates = sorted(rows)
    for i, r in enumerate(dates):
        loans = rows[r][den]
        if flow and i > 0 and rows[dates[i - 1]][den] > 0:
            prev = dates[i - 1]
            # only average with the immediately preceding quarter
            if _shift_quarters(date.fromisoformat(repdte_to_date(prev)), 1) == \
                    date.fromisoformat(repdte_to_date(r)):
                loans = (loans + rows[prev][den]) / 2
        if loans <= 0:
            continue
        value = rows[r][num] * (4 if flow else 1) / loans * 100
        pts.append((repdte_to_date(r), round(value, 4)))
    return pts


RATE_SERIES = [
    # (series stem, label, numerator, denominator, flow?, buckets)
    ("FDIC_CI_NCR", "C&I Noncurrent Rate", "NCCI", "LNCI", False, ("SMALLBANK", "LARGEBANK")),
    ("FDIC_CI_NCO", "C&I Net Charge-off Rate (ann.)", "NTCIQ", "LNCI", True, ("SMALLBANK", "LARGEBANK")),
    ("FDIC_AUTO_NCR", "Auto Loan Noncurrent Rate", "NCAUTO", "LNAUTO", False, ("TOTAL",)),
    ("FDIC_AUTO_NCO", "Auto Loan Net Charge-off Rate (ann.)", "NTAUTOQ", "LNAUTO", True, ("TOTAL",)),
]


def _rate_series_id(stem: str, bucket: str) -> str:
    return stem if bucket == "TOTAL" else f"{stem}_{bucket}"


def fetch_rates() -> int:
    start = _start_repdte("FDIC_CI_NCR_LARGEBANK")
    data = _fetch_all_buckets(RATE_FIELDS, start)
    first_quarter = min((r for b in data.values() for r in b), default=None)
    n = 0
    for stem, label, num, den, flow, buckets in RATE_SERIES:
        for bucket in buckets:
            pts = _rate_points(data[bucket], num, den, flow=flow)
            if flow and start != START_REPDTE and first_quarter:
                # The window's first quarter only exists to seed the average.
                pts = [p for p in pts if p[0] != repdte_to_date(first_quarter)]
            n += store_series(
                _rate_series_id(stem, bucket),
                f"FDIC {label} — {BUCKET_LABELS[bucket]} (%)",
                pts,
                category="credit",
            )
    logger.info("FDIC rates — %d points since %s", n, start)
    return n


# Registered with the scheduler via data/sources.py (BUILTIN_MODULES).
SOURCE = Source(
    id="fdic",
    label="FDIC small-business lending",
    fetch=fetch_small_business,
    every_hours=24 * 7,
    description=(
        "Call Report RC-C Part II C&I loans with original amount <= $1mm, "
        "by bank size (<$10bn vs >= $10bn), from FDIC BankFind. June-only "
        "before 2010; small banks semiannual since 2017."
    ),
    # Charted by a hand-built panel in Macro → Business & SMB Credit.
)

SOURCES = [
    Source(
        id="fdic_rates",
        label="FDIC credit quality by bank size",
        fetch=fetch_rates,
        every_hours=24 * 7,
        description=(
            "C&I noncurrent and net charge-off rates for banks <$10bn vs "
            ">= $10bn, plus all-bank auto loan rates, from summed Call Report "
            "dollars (FDIC BankFind)."
        ),
        # Charted by a hand-built panel in Macro → Business & SMB Credit.
    ),
]
