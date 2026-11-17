"""
backend/data/edgar_distress.py — weekly/monthly counts of distress 8-Ks.

Counts original Form 8-K filings that report either of two distress items, a
free and fast leading indicator of corporate defaults:

  * Item 2.04  Triggering Events That Accelerate or Increase a Direct
               Financial Obligation (covenant breach, acceleration,
               cross-default)
  * Item 1.03  Bankruptcy or Receivership

Series (metrics table, category 'edgar_distress'):
  EDGAR_8K_204_WEEKLY / EDGAR_8K_103_WEEKLY    date = Monday of the week
  EDGAR_8K_204_MONTHLY / EDGAR_8K_103_MONTHLY  date = 1st of the month
Weeks/months with no filings are stored as 0. The current (incomplete) week
and month are never stored.

Method (validated against exhaustive paging, see below):
  EDGAR full-text search (efts.sec.gov/LATEST/search-index) has no filter on
  8-K item numbers — `q` only becomes `match_phrase` clauses on the document
  text. But every hit's `_source.items` carries the filing-header item list.
  So per item we run two phrase queries per calendar-month window
  ("Item 2.04" + "Triggering Events"; "Item 1.03" + "Bankruptcy or
  Receivership"), keep hits whose `_source.items` contains the item and whose
  `_source.form` is exactly "8-K" (8-K/A amendments excluded — they restate an
  earlier event), and dedup by accession number (`adsh`; one filing yields a
  hit per matching document/exhibit). The header items field is the truth;
  the text queries just narrow the candidate set to ~1 page.

  Checked against paging *every* 8-K (no `q`, which returns one hit per
  filing) for 2005-06, 2009-03, 2016-02, 2020-04 and 2026-09: the two-query
  union matched exactly except one 1.03 filing in each of 2005-06 and 2009-03,
  both paper filings (accession 9999999997-…) with no indexed text. So counts
  undercount by ~0-2% (paper filings, essentially nil after ~2010).

  Exhaustive paging would be ground truth but costs ~40-90 requests per month
  (~20k for the full history); the phrase approach is ~4-5 per month.

History starts 2004-08-23 (a Monday), the effective date of the current 8-K
item scheme — earlier 8-Ks use the old numbering (e.g. "3" = bankruptcy) and
have no 2.04 equivalent, so the series are not stitched back to 2001. The
first monthly point is 2004-09.

EFTS serves 100 hits per page (`from` offset, `size` fixed at 100) and caps a
query at 10k hits; a one-month window for these phrases is a few dozen hits.
SEC asks for <= 10 req/s and a declared User-Agent.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import requests

from cache.db import get_conn
from config import settings
from data.sources import Source, store_series

logger = logging.getLogger(__name__)

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
PAGE_SIZE = 100            # EFTS's fixed page size (the ES `size` it builds)
MAX_FROM = 10_000          # EFTS/ES result-window cap
PACE_S = 0.15              # ~6 req/s, under SEC's 10 req/s
HISTORY_START = date(2004, 8, 23)   # current 8-K item scheme effective (Monday)
REVISION_DAYS = 56                  # re-pull ~8 weeks: late filings + EFTS lag
CATEGORY = "edgar_distress"

# item -> (series prefix, label, phrase queries)
ITEMS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "2.04": (
        "EDGAR_8K_204",
        "8-K Item 2.04 (acceleration / triggering events)",
        ('"Item 2.04"', '"Triggering Events"'),
    ),
    "1.03": (
        "EDGAR_8K_103",
        "8-K Item 1.03 (bankruptcy / receivership)",
        ('"Item 1.03"', '"Bankruptcy or Receivership"'),
    ),
}


# ── Dates ────────────────────────────────────────────────────────────────────

def _today() -> date:
    return datetime.now(timezone.utc).date()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Calendar-month query windows covering [start, end] (inclusive)."""
    out = []
    cur = start
    while cur <= end:
        stop = min(_next_month(cur) - timedelta(days=1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


# ── EFTS ─────────────────────────────────────────────────────────────────────

def _get(params: dict, session: requests.Session) -> dict:
    last: Exception | None = None
    for attempt in range(5):
        try:
            resp = session.get(
                EFTS_URL,
                params=params,
                headers={"User-Agent": settings.EDGAR_USER_AGENT},
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json()
            # EFTS throws sporadic 500s on phrase queries; 429 = slow down.
            last = RuntimeError(f"EFTS HTTP {resp.status_code}")
        except (requests.RequestException, ValueError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"EFTS request failed after retries ({params}): {last}")


def search_hits(q: str, start: date, end: date, session: requests.Session) -> list[dict]:
    """All hits for a phrase query on 8-K-family filings in [start, end]."""
    hits: list[dict] = []
    offset = 0
    while True:
        data = _get(
            {
                "q": q,
                "forms": "8-K",  # matches root_forms, so includes 8-K/A
                "dateRange": "custom",
                "startdt": start.isoformat(),
                "enddt": end.isoformat(),
                "from": offset,
            },
            session,
        )
        time.sleep(PACE_S)
        page = (data.get("hits") or {}).get("hits") or []
        hits.extend(page)
        if offset == 0:
            total = ((data.get("hits") or {}).get("total") or {}).get("value", 0)
            if total >= MAX_FROM:
                logger.warning("EFTS %s %s..%s hit the %d cap", q, start, end, MAX_FROM)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if offset >= MAX_FROM:
            break
    return hits


def filter_filings(hits: Iterable[dict], item: str) -> dict[str, str]:
    """{accession: file_date} for original 8-Ks whose header lists `item`.

    Drops 8-K/A amendments and hits that only *mention* the item text (the
    header items list is authoritative), and collapses exhibit hits onto
    their filing's accession.
    """
    out: dict[str, str] = {}
    for h in hits:
        src = h.get("_source") or {}
        if src.get("form") != "8-K":
            continue
        if item not in (src.get("items") or []):
            continue
        adsh = src.get("adsh") or (h.get("_id") or "").split(":")[0]
        fdate = src.get("file_date")
        if adsh and fdate:
            out.setdefault(adsh, fdate)
    return out


def collect_filings(item: str, start: date, end: date, session: requests.Session) -> dict[str, str]:
    _, _, queries = ITEMS[item]
    found: dict[str, str] = {}
    for w_start, w_end in month_windows(start, end):
        for q in queries:
            for adsh, fdate in filter_filings(search_hits(q, w_start, w_end, session), item).items():
                found.setdefault(adsh, fdate)
    return found


# ── Bucketing ────────────────────────────────────────────────────────────────

def weekly_counts(file_dates: Iterable[str], start: date, today: date) -> list[tuple[str, int]]:
    """Counts per Monday-start week, for every *complete* week from the first
    Monday on/after `start` through the week before `today`'s. Zero-fills."""
    c = Counter(week_start(date.fromisoformat(d[:10])) for d in file_dates)
    wk = week_start(start)
    if wk < start:
        wk += timedelta(days=7)
    current = week_start(today)
    out = []
    while wk < current:
        out.append((wk.isoformat(), c.get(wk, 0)))
        wk += timedelta(days=7)
    return out


def monthly_counts(file_dates: Iterable[str], start: date, today: date) -> list[tuple[str, int]]:
    """Counts per calendar month for every *complete* month starting on/after
    `start` through the month before `today`'s. Zero-fills."""
    c = Counter(month_start(date.fromisoformat(d[:10])) for d in file_dates)
    m = month_start(start)
    if m < start:
        m = _next_month(m)
    current = month_start(today)
    out = []
    while m < current:
        out.append((m.isoformat(), c.get(m, 0)))
        m = _next_month(m)
    return out


# ── Fetch ────────────────────────────────────────────────────────────────────

def _last_stored(series_id: str) -> date | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM metrics WHERE series_id = ?", (series_id,)
        ).fetchone()
    return date.fromisoformat(row[0][:10]) if row and row[0] else None


def fetch_start(today: date) -> date:
    """Empty DB -> full history; else the 1st of the month ~8 weeks back (so
    the re-pulled months are complete), or earlier if the store has a gap."""
    lasts = [_last_stored(f"{prefix}_WEEKLY") for prefix, _, _ in ITEMS.values()]
    if any(d is None for d in lasts):
        return HISTORY_START
    start = month_start(today - timedelta(days=REVISION_DAYS))
    return max(HISTORY_START, min(start, month_start(min(lasts))))


def fetch(today: date | None = None) -> int:
    today = today or _today()
    start = fetch_start(today)
    end = today - timedelta(days=1)
    logger.info("EDGAR distress 8-Ks: pulling %s..%s", start, end)
    stored = 0
    with requests.Session() as session:
        for item, (prefix, label, _) in ITEMS.items():
            filings = collect_filings(item, start, end, session)
            dates = list(filings.values())
            stored += store_series(
                f"{prefix}_WEEKLY", f"{label}, weekly count",
                weekly_counts(dates, start, today), category=CATEGORY,
            )
            stored += store_series(
                f"{prefix}_MONTHLY", f"{label}, monthly count",
                monthly_counts(dates, start, today), category=CATEGORY,
            )
            logger.info("EDGAR distress 8-Ks: item %s — %d filings since %s", item, len(filings), start)
    return stored


SOURCE = Source(
    id="edgar_distress",
    label="EDGAR 8-K distress filings",
    description=(
        "Weekly/monthly counts of original 8-Ks reporting Item 2.04 "
        "(debt acceleration / triggering events) and Item 1.03 (bankruptcy), "
        "via EDGAR full-text search. Since Aug 2004."
    ),
    fetch=fetch,
    every_hours=24,
    # Charted by a hand-built panel in Macro → Corporate & Leveraged.
)
