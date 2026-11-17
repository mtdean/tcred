"""
backend/data/philly_cards.py — Philadelphia Fed large-bank credit card data.

The Philly Fed aggregates FR Y-14M loan-level card data from the largest bank
holding companies (roughly 70% of US card balances) and publishes a quarterly
report with two CSVs behind it:

  * YYQn-CreditCardBalances.csv    — the stock of accounts: payment behaviour,
    delinquency, charge-offs, utilization, APR, line management (2012Q3→)
  * YYQn-CreditCardOriginations.csv — new accounts: score mix, limits, APR

The files live under /-/media/FRBP/Assets/Surveys-And-Data/Y14/<YYYY>/Q<n>/
and are re-dated each quarter, so we scrape the landing page for the current
links. Each file carries full history, so every pull rewrites the whole
series (picking up any revisions). Columns are matched by header text, not
position, so a reordered or widened file still parses.

Percent values are stored as percents (12.3, not 0.123), matching sce.py and
the "pct" panel unit. Quarterly dates use the first day of the quarter.
"""

from __future__ import annotations

import io
import logging
import re
from urllib.parse import urljoin

import pandas as pd
import requests

from data.sources import Source, store_series

logger = logging.getLogger(__name__)

PAGE_URL = (
    "https://www.philadelphiafed.org/surveys-and-data/"
    "large-bank-credit-card-and-mortgage-data"
)
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )
}
_BAL_RE = re.compile(r'href="([^"]*CreditCardBalances\.csv[^"]*)"', re.I)
_ORIG_RE = re.compile(r'href="([^"]*CreditCardOriginations\.csv[^"]*)"', re.I)
_QTR_RE = re.compile(r"^\s*(\d{4})\s*Q([1-4])\s*$", re.I)

CATEGORY = "consumer_credit"

# (series_id, label, header text in the CSV — matched case/space-insensitively)
BALANCE_SERIES = [
    ("PHIL_CC_MIN_PAY", "Philly Fed Y-14M: Share of Accounts Paying Only the Minimum (%)",
     "Share of Accounts Making the Minimum Payment"),
    ("PHIL_CC_FULL_PAY", "Philly Fed Y-14M: Share of Accounts Paying Full Balance (%)",
     "Share of Accounts Making the Full Balance Payment"),
    ("PHIL_CC_DPD30", "Philly Fed Y-14M: 30+ DPD Rate, Accounts (%)",
     "30+ Days Past Due Rates: Accounts Based"),
    ("PHIL_CC_DPD90", "Philly Fed Y-14M: 90+ DPD Rate, Accounts (%)",
     "90+ Days Past Due Rates: Accounts Based"),
    ("PHIL_CC_DPD30_BAL", "Philly Fed Y-14M: 30+ DPD Rate, Balances (%)",
     "30+ Days Past Due Rates: Balances Based"),
    ("PHIL_CC_NCO", "Philly Fed Y-14M: Net Charge-Off Rate (%)",
     "Net Charge-Off Rate"),
    ("PHIL_CC_UTIL_MED", "Philly Fed Y-14M: Median Utilization, Active Accounts (%)",
     "Utilization (Active Accounts Only) (50th percentile)"),
    ("PHIL_CC_LINE_DECR", "Philly Fed Y-14M: Accounts with Credit Line Decrease (%)",
     "Percentage of Accounts with Credit Line Decrease"),
    ("PHIL_CC_APR_GP", "Philly Fed Y-14M: Avg Purchase APR, General Purpose (%)",
     "Average Purchase APR: General Purpose"),
    ("PHIL_CC_BALANCES", "Philly Fed Y-14M: Total Card Balances ($B)",
     "Total Balances ($Billions)"),
]
ORIGINATION_SERIES = [
    ("PHIL_CC_ORIG_SUBPRIME", "Philly Fed Y-14M: New Accounts with Score < 660 (%)",
     "Percentage of New Accounts with < 660 Credit Score"),
    ("PHIL_CC_ORIG_APR_GP", "Philly Fed Y-14M: Avg Original Purchase APR, General Purpose (%)",
     "Average Original Purchase APR: General Purpose"),
]


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).replace("﻿", "")).strip().lower()


def quarter_date(label) -> str | None:
    """'2026Q2' -> '2026-04-01'; anything else (footer rows) -> None."""
    m = _QTR_RE.match(str(label))
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    return f"{year}-{3 * (q - 1) + 1:02d}-01"


def parse_value(raw) -> float | None:
    """'$1,234.5' / '12.3 %' / '517.47' -> float; 'null' / '' -> None."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip().replace("$", "").replace(",", "").replace("%", "").strip()
    if not s or s.lower() in {"null", "na", "n/a", "nan", "-"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_csv(content: bytes, wanted: list[tuple[str, str, str]]) -> dict[str, list[tuple[str, float]]]:
    """Return {series_id: [(date, value), ...]} for each wanted column found."""
    df = pd.read_csv(io.BytesIO(content), dtype=str, encoding="utf-8-sig")
    cols = {_norm(c): c for c in df.columns}
    if "yrqtr" not in cols:
        raise ValueError(f"Philly Fed card CSV: no YRQTR column in {list(df.columns)[:5]}")
    qcol = cols["yrqtr"]

    out: dict[str, list[tuple[str, float]]] = {}
    for sid, _label, header in wanted:
        col = cols.get(_norm(header))
        if col is None:
            logger.warning("Philly Fed cards: column %r missing — %s skipped", header, sid)
            continue
        pts = []
        for q, raw in zip(df[qcol], df[col]):
            d = quarter_date(q)
            v = parse_value(raw)
            if d is not None and v is not None:
                pts.append((d, v))
        out[sid] = pts
    return out


def find_csv_urls(html: str) -> tuple[str | None, str | None]:
    """Absolute URLs of the balances and originations CSVs on the landing page."""
    bal = _BAL_RE.search(html)
    orig = _ORIG_RE.search(html)
    return (
        urljoin(PAGE_URL, bal.group(1)) if bal else None,
        urljoin(PAGE_URL, orig.group(1)) if orig else None,
    )


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=_UA, timeout=60)
    resp.raise_for_status()
    return resp


def _store_all(content: bytes, wanted: list[tuple[str, str, str]]) -> int:
    labels = {sid: label for sid, label, _ in wanted}
    n = 0
    for sid, pts in parse_csv(content, wanted).items():
        n += store_series(sid, labels[sid], pts, category=CATEGORY)
    return n


def fetch_philly_cards() -> int:
    """Scrape the current CSV links, parse, store. Returns rows written."""
    bal_url, orig_url = find_csv_urls(_get(PAGE_URL).text)
    if not bal_url:
        # The balances file carries the headline series — treat as a failure.
        raise RuntimeError(f"Philly Fed cards: no CreditCardBalances.csv link on {PAGE_URL}")

    n = _store_all(_get(bal_url).content, BALANCE_SERIES)
    if orig_url:
        try:
            n += _store_all(_get(orig_url).content, ORIGINATION_SERIES)
        except Exception as e:  # noqa: BLE001 — balances already stored
            logger.error("Philly Fed cards: originations fetch/parse failed: %s", e)
    else:
        logger.warning("Philly Fed cards: no CreditCardOriginations.csv link found")

    logger.info("Philly Fed cards: %d rows stored from %s", n, bal_url)
    return n


# Quarterly release (~2 quarters after quarter end); a weekly check catches it.
SOURCE = Source(
    id="philly_cards",
    label="Philly Fed Large-Bank Credit Cards (Y-14M)",
    fetch=fetch_philly_cards,
    every_hours=24 * 7,
    description=(
        "Quarterly FR Y-14M card aggregates from the Philadelphia Fed: minimum-"
        "payment / full-payer shares, delinquency, charge-offs, utilization, APR."
    ),
    # Charted by the hand-built CardPaymentBehaviorPanel in Consumer Health.
)
