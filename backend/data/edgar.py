"""
backend/data/edgar.py — SEC EDGAR filing monitor for ABS/structured finance.

Uses the free EDGAR full-text search API (efts.sec.gov) to find:
  - New ABS-15G filings (rep/warranty reports)
  - ABS-EE (asset data files under Reg AB II)
  - S-3 shelf registrations with ABS keywords
  - 424B5 prospectus supplements (deal pricing)
  - 8-K filings from known ABS issuers

No API key required. Must set a descriptive User-Agent per SEC policy.
Rate limit: 10 req/sec sustained; we stay well under with sleep().
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from config import load_data_sources, settings
from cache.db import upsert_edgar_filing

logger = logging.getLogger(__name__)

BASE_EFTS = "https://efts.sec.gov/LATEST/search-index"
BASE_DATA = "https://data.sec.gov"

HEADERS = {
    "User-Agent": settings.EDGAR_USER_AGENT,
    "Accept": "application/json",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def edgar_index_url(hit_id: str, company_str: str) -> str:
    """
    Build the canonical EDGAR filing-index URL from an EFTS hit id
    ("0001193125-26-232093:doc.htm") and a company string containing "(CIK n)".
    Returns "" if either piece is missing.
    """
    accession_full = (hit_id or "").split(":")[0]
    acc_nodash = accession_full.replace("-", "")
    m = re.search(r"CIK\s+(\d+)", company_str or "")
    if accession_full and m:
        cik = str(int(m.group(1)))  # archives path uses CIK without leading zeros
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{accession_full}-index.htm"
    return ""


def _search_filings(
    form_type: str,
    keywords: list[str],
    days_back: int = 7,
) -> list[dict]:
    """
    Search EDGAR full-text search for recent filings matching form_type
    and any of the provided keywords.
    """
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    results = []

    for keyword in keywords:
        params = {
            "q": f'"{keyword}"',
            "dateRange": "custom",
            "startdt": since,
            "forms": form_type,
            "_source": "file_date,display_names,form_type,file_num,period_of_report",
        }
        try:
            resp = requests.get(
                BASE_EFTS,
                params=params,
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])

            for hit in hits:
                src = hit.get("_source", {})
                hit_id = hit.get("_id", "")
                company = ", ".join(src.get("display_names", []))
                results.append(
                    {
                        "accession_no": hit_id,
                        "company_name": company,
                        "form_type": form_type,
                        "filed_at": src.get("file_date", ""),
                        "description": f'Keyword match: "{keyword}"',
                        "url": edgar_index_url(hit_id, company),
                        "asset_class": keyword,
                        "fetched_at": _now(),
                    }
                )

        except Exception as e:
            logger.error(
                f"EDGAR search error — form={form_type} keyword={keyword}: {e}"
            )

        time.sleep(0.15)  # Stay well under 10 req/sec

    return results


def fetch_abs_filings(days_back: int = 7) -> int:
    """
    Pull recent ABS-related EDGAR filings and store them.
    Returns count inserted.
    """
    cfg = load_data_sources()
    edgar_cfg = cfg.get("edgar", {})
    form_types = edgar_cfg.get("form_types", ["ABS-15G", "ABS-EE"])
    keywords = edgar_cfg.get("asset_class_keywords", [])

    count = 0
    for form_type in form_types:
        filings = _search_filings(form_type, keywords, days_back=days_back)
        for filing in filings:
            try:
                upsert_edgar_filing(filing)
                count += 1
            except Exception as e:
                logger.debug(f"EDGAR insert skip: {e}")

    logger.info(f"EDGAR fetch complete — {count} filings stored")
    return count


def get_recent_filings(
    limit: int = 50,
    offset: int = 0,
    form_type: Optional[str] = None,
    asset_class: Optional[str] = None,
) -> list[dict]:
    """Return ABS-related filings from the DB, newest first, with optional filters."""
    from cache.db import get_conn

    sql = """
        SELECT accession_no, company_name, form_type, filed_at,
               description, url, asset_class
        FROM edgar_filings
        WHERE 1=1
    """
    params: list = []
    if form_type:
        sql += " AND form_type = ?"
        params.append(form_type)
    if asset_class:
        sql += " AND asset_class = ?"
        params.append(asset_class)
    sql += " ORDER BY filed_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_edgar_facets() -> dict:
    """Distinct form types and asset classes present, for filter dropdowns."""
    from cache.db import get_conn

    with get_conn() as conn:
        forms = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT form_type FROM edgar_filings WHERE form_type IS NOT NULL ORDER BY form_type"
            )
        ]
        classes = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT asset_class FROM edgar_filings WHERE asset_class IS NOT NULL ORDER BY asset_class"
            )
        ]
    return {"form_types": forms, "asset_classes": classes}
