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
from datetime import datetime, timezone
from data.dates import utc_days_ago_str, utc_today_str
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


# Company-name → asset_class rules, evaluated in order. The first regex that
# matches wins. Each entry is (regex, asset_class). Earlier text-keyword
# matching (e.g. q="royalty") was indiscriminate — CMBS deals that mention
# "royalty payments" in boilerplate were getting tagged 'royalty'. Name-based
# matching is far more reliable because deal trusts encode the asset type in
# their name (e.g. "Wells Fargo Commercial Mortgage Trust 2026-5C9").
_NAME_CLASS_RULES: list[tuple[re.Pattern, str]] = [
    # Mortgage / RMBS / CMBS — added because these were miscategorized as
    # "royalty" before. "BANK 20XX-BNKn" is the common name for CMBS conduit
    # deals jointly sponsored by Wells / BAML / JPM / Morgan Stanley.
    (re.compile(r"\b(?:commercial|residential|multifamily)\s+mortgage\b", re.I), "mortgage"),
    (re.compile(r"\bmortgage trust\b", re.I), "mortgage"),
    (re.compile(r"\b(?:RMBS|CMBS|MBS)\b"), "mortgage"),
    (re.compile(r"^BANK\s+20\d{2}-\w+", re.I), "mortgage"),
    (re.compile(r"\bFEDERAL HOME LOAN MORTGAGE\b", re.I), "mortgage"),
    (re.compile(r"\bFANNIE MAE\b|\bFREDDIE MAC\b", re.I), "mortgage"),
    (re.compile(r"\bground lease\b|\bland lease\b", re.I), "ground lease"),
    # Royalty — narrow to the actual royalty securitization patterns. Drug
    # royalty LPs, music royalty trusts, Royalty Pharma, etc. Otherwise the
    # mere word "royalty" anywhere in a CMBS doc would tag the deal as one.
    (re.compile(r"\bdrug royalty\b|\bmusic royalty\b|\broyalty pharma\b", re.I), "royalty"),
    (re.compile(r"\broyalty rights?\b|\broyalty trust\b|\broyalty receivables?\b", re.I), "royalty"),
    # CLO
    (re.compile(r"\b(?:CLO|collateralized loan obligation)\b", re.I), "CLO"),
    # Auto
    (re.compile(r"\bauto (?:receivables|owner trust|lease)\b", re.I), "auto loan"),
    (re.compile(r"\bvehicle owner trust\b", re.I), "auto loan"),
    # Credit card
    (re.compile(r"\bcredit card\b|\bcard funding\b|\bcard issuance\b", re.I), "credit card"),
    # Equipment
    (re.compile(r"\bequipment (?:finance|trust|collateral|leasing)\b", re.I), "equipment finance"),
    # Aircraft / aviation
    (re.compile(r"\baircraft\b|\baviation\b", re.I), "aircraft"),
    # Student loan
    (re.compile(r"\bstudent loan\b|\beducation loan\b", re.I), "student loan"),
    # Marketplace / consumer
    (re.compile(r"\bmarketplace\b", re.I), "marketplace lending"),
    (re.compile(r"\bconsumer (?:receivables|loan)\b|\bpersonal loan\b", re.I), "consumer loan"),
    # Solar / specialty
    (re.compile(r"\bsolar\b", re.I), "solar"),
    (re.compile(r"\bdata center\b", re.I), "data center"),
    (re.compile(r"\bcontainer (?:lease|trust)\b|\bmarine container\b", re.I), "container"),
    (re.compile(r"\bfiber|tower\b|\bwireless\b|\bspectrum\b", re.I), "wireless"),
    (re.compile(r"\bwhole business\b", re.I), "whole business"),
    (re.compile(r"\bfloorplan\b|\bfloor plan\b", re.I), "floorplan"),
]


def _classify_asset_class(company_name: str, fallback: str) -> str:
    """Return the asset class implied by the deal-trust's name. Falls back to
    the EDGAR FTS keyword that surfaced the filing if no name pattern matches."""
    if not company_name:
        return fallback
    for pat, label in _NAME_CLASS_RULES:
        if pat.search(company_name):
            return label
    return fallback


# Trust / vehicle naming patterns that mark a 424B5 / S-3 / 8-K as a
# securitization (i.e. debt) rather than a corporate offering (often equity).
# ABS-15G and ABS-EE are always debt by form type.
_DEBT_NAME_PATTERN = re.compile(
    r"\btrust\b|\breceivables?\b|\bfunding (?:co|corp|llc)\b|"
    r"\bissuance\b|\bnote(?:s)? trust\b|\bcollateral\b|\bowner trust\b|"
    r"\bmaster trust\b|\bcard funding\b",
    re.IGNORECASE,
)
_ABS_FORMS = {"ABS-15G", "ABS-EE"}


def _classify_issuance_type(company_name: str, form_type: str) -> str:
    """Best-effort debt/equity tag for an EDGAR filing.

    ABS-specific forms (ABS-15G / ABS-EE) are always debt. For 424B5 / S-3 /
    8-K, the trust/vehicle naming convention is the most reliable signal —
    securitization issuers are almost always named with 'Trust', 'Receivables',
    'Funding LLC', etc. Plain corporates doing the same form types are
    usually equity offerings.
    """
    if form_type in _ABS_FORMS:
        return "debt"
    if company_name and _DEBT_NAME_PATTERN.search(company_name):
        return "debt"
    return "equity"


def _search_filings(
    form_type: str,
    keywords: list[str],
    days_back: int = 7,
) -> list[dict]:
    """
    Search EDGAR full-text search for recent filings matching form_type
    and any of the provided keywords.
    """
    since = utc_days_ago_str(days_back)
    results = []

    for keyword in keywords:
        # NB: EDGAR FTS now 500s when sent `dateRange=custom` or `_source`;
        # pass only q/forms/startdt/enddt (the omitted fields come back anyway).
        params = {
            "q": f'"{keyword}"',
            "startdt": since,
            "enddt": utc_today_str(),
            "forms": form_type,
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
                        # Asset class is derived from the deal-trust name when
                        # possible — the FTS keyword that surfaced the filing
                        # is only the fallback. Stops mortgage trusts from
                        # being tagged 'royalty' just because the doc text
                        # contains the word.
                        "asset_class": _classify_asset_class(company, keyword),
                        "issuance_type": _classify_issuance_type(company, form_type),
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
    issuance_type: Optional[str] = None,
) -> list[dict]:
    """Return ABS-related filings from the DB, newest first, with optional filters."""
    from cache.db import get_conn

    sql = """
        SELECT accession_no, company_name, form_type, filed_at,
               description, url, asset_class, issuance_type
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
    if issuance_type:
        sql += " AND issuance_type = ?"
        params.append(issuance_type)
    sql += " ORDER BY filed_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_edgar_facets() -> dict:
    """Distinct form types, asset classes, and issuance types — for filter dropdowns."""
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
        issuance_types = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT issuance_type FROM edgar_filings WHERE issuance_type IS NOT NULL ORDER BY issuance_type"
            )
        ]
    return {"form_types": forms, "asset_classes": classes, "issuance_types": issuance_types}
