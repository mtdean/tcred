"""
backend/data/nfib.py — NFIB Small Business Economic Trends (SBET) ingest.

NFIB publishes SBET as a monthly PDF only (the second Tuesday; no CSV/API),
but each report carries multi-year monthly tables, so parsing ONE current
PDF backfills ~5 years of history. pypdf's text extraction preserves the
tables as clean `YYYY v1 … v12` rows.

Series stored (metrics, category 'nfib'):
  * NFIB_OPTIMISM          — Small Business Optimism Index (SA, 1986=100)
  * NFIB_LOAN_AVAILABILITY — Availability of Loans net % ("easier" − "harder",
                             regular borrowers; more negative = tighter)

URL pattern: the report for month M is uploaded under the following month's
wp-content directory. Discovery tries the last few months and takes the
first 200.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timezone

import requests

from cache.db import upsert_metric

logger = logging.getLogger(__name__)

URL_TMPL = (
    "https://www.nfib.com/wp-content/uploads/{uy}/{um:02d}/"
    "NFIB-SBET-Report-{month_name}-{ry}.pdf"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (SituationMonitor personal dashboard)"}

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

_HEADER_RE = re.compile(r"Jan\s+Feb\s+Mar\s+Apr\s+May\s+Jun\s+Jul\s+Aug\s+Sep\s+Oct\s+Nov\s+Dec")
_YEAR_ROW_RE = re.compile(r"^\s*(20\d\d)((?:\s+-?\d+(?:\.\d+)?)+)\s*$", re.M)


def _candidate_urls(today: date | None = None) -> list[str]:
    today = today or date.today()
    urls = []
    y, m = today.year, today.month
    for _ in range(4):  # report month = upload month - 1; walk back 4 months
        ry, rm = (y, m - 1) if m > 1 else (y - 1, 12)
        urls.append(URL_TMPL.format(uy=y, um=m, month_name=_MONTHS[rm - 1], ry=ry))
        y, m = ry, rm
    return urls


def _parse_year_block(text: str, start: int) -> list[tuple[str, float]]:
    """Parse consecutive `YYYY v1 … v12` rows starting at `start` (just after
    a months header). Stops at the first non-matching line."""
    out: list[tuple[str, float]] = []
    for line in text[start:start + 1500].splitlines():
        m = _YEAR_ROW_RE.match(line)
        if m is None:
            if out:  # block ended
                break
            continue
        year = int(m.group(1))
        vals = m.group(2).split()
        for i, v in enumerate(vals[:12]):
            out.append((f"{year}-{i + 1:02d}-01", float(v)))
    return out


def parse_sbet_text(text: str) -> dict[str, list[tuple[str, float]]]:
    """Extract the optimism and loan-availability tables from SBET text."""
    series: dict[str, list[tuple[str, float]]] = {}

    # Optimism: first months header after the "OPTIMISM INDEX" anchor.
    i = text.find("OPTIMISM INDEX")
    if i >= 0:
        h = _HEADER_RE.search(text, i)
        if h:
            pts = _parse_year_block(text, h.end())
            if pts:
                series["NFIB_OPTIMISM"] = pts

    # Availability of loans: the page holds TWO tables — the regular-borrower
    # share first, then the net-percent table. Take the block after the
    # SECOND months header following the anchor.
    i = text.find("AVAILABILITY OF LOANS")
    if i >= 0:
        h1 = _HEADER_RE.search(text, i)
        h2 = _HEADER_RE.search(text, h1.end()) if h1 else None
        if h2:
            pts = _parse_year_block(text, h2.end())
            if pts:
                series["NFIB_LOAN_AVAILABILITY"] = pts
    return series


_LABELS = {
    "NFIB_OPTIMISM": "NFIB Small Business Optimism (SA, 1986=100)",
    "NFIB_LOAN_AVAILABILITY": "NFIB Loan Availability (Net % Easier−Harder)",
}


def fetch_nfib() -> int:
    """Find the newest SBET PDF, parse its tables, upsert. Returns rows stored."""
    from pypdf import PdfReader

    pdf_bytes = None
    for url in _candidate_urls():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                pdf_bytes = resp.content
                logger.info("NFIB SBET found: %s", url)
                break
        except Exception as e:
            logger.debug("NFIB probe failed %s: %s", url, e)
    if pdf_bytes is None:
        logger.error("NFIB SBET: no report PDF found at candidate URLs")
        return 0

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        series = parse_sbet_text(text)
    except Exception as e:
        logger.error("NFIB SBET parse error: %s", e)
        return 0

    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    for series_id, points in series.items():
        for d, v in points:
            upsert_metric({
                "series_id": series_id,
                "label": _LABELS[series_id],
                "category": "nfib",
                "date": d,
                "value": v,
                "fetched_at": now,
            })
            stored += 1
    logger.info("NFIB SBET ingest complete — %d rows", stored)
    return stored
