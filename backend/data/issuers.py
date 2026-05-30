"""
backend/data/issuers.py — issuer/deal pivot aggregator.

The dashboard already carries multiple views of the same world: abs_new_issues
has tranche-level pricing from 424B5 parses, abs_pricing has the FWP-derived
spread feed, edgar_filings has every form, kbra_presales has assumptions, and
articles has news. This module stitches them by substring match so a query
like "Carvana" or "Santander Drive" returns everything we know about that
sponsor family in one shot.

`list_issuers()` exposes the universe — distinct, non-empty issuer_name values
from abs_new_issues ordered by recent filing activity, so the UI can offer
quick-select chips of names we actually have data on.

`get_issuer_summary(query)` does the cross-table substring search and returns:
  * deals          — one row per accession_no with rolled-up totals and the
                     senior-tranche spread + WAL
  * pricing        — abs_pricing tranches matching the query
  * filings        — recent EDGAR filings (any form type)
  * presales       — KBRA presale rows
  * articles       — high-scored news mentioning the query
  * stats          — quick numeric summary (deal count, total volume, span)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from cache import db

logger = logging.getLogger(__name__)


def list_issuers(limit: int = 100) -> list[dict]:
    """Distinct issuer_name values from abs_new_issues, ordered by activity.

    Activity = most recent filing date for that issuer. Returns up to `limit`
    rows: {issuer_name, deal_count, latest_filing_date}.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT issuer_name,
                   COUNT(DISTINCT accession_no) AS deal_count,
                   MAX(filing_date)             AS latest_filing_date
            FROM abs_new_issues
            WHERE issuer_name IS NOT NULL AND issuer_name <> ''
            GROUP BY issuer_name
            ORDER BY latest_filing_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Cross-table search helpers ─────────────────────────────────────────────

def _abs_new_issues_for(query: str, limit: int) -> list[dict]:
    pat = f"%{query}%"
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM abs_new_issues
            WHERE (issuer_name LIKE ? COLLATE NOCASE
                OR depositor LIKE ? COLLATE NOCASE
                OR servicer LIKE ? COLLATE NOCASE)
            ORDER BY filing_date DESC, accession_no
            LIMIT ?
            """,
            (pat, pat, pat, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _aggregate_deals(tranches: list[dict]) -> list[dict]:
    """Roll tranche rows up to one row per deal (accession_no)."""
    by_deal: dict[str, dict] = {}
    for t in tranches:
        acc = t["accession_no"]
        d = by_deal.setdefault(acc, {
            "accession_no": acc,
            "issuer_name": t.get("issuer_name"),
            "depositor": t.get("depositor"),
            "filing_date": t.get("filing_date"),
            "closing_date": t.get("closing_date"),
            "asset_class": t.get("asset_class"),
            "total_deal_size": t.get("total_deal_size") or 0,
            "edgar_url": t.get("edgar_url"),
            "parse_confidence": t.get("parse_confidence"),
            "tranches": [],
            "n_tranches": 0,
            "senior_spread_bps": None,
            "senior_wal_years": None,
            "senior_class_name": None,
        })
        d["tranches"].append({
            "class_name": t.get("class_name"),
            "principal_amount": t.get("principal_amount"),
            "coupon_type": t.get("coupon_type"),
            "coupon_rate": t.get("coupon_rate"),
            "floating_index": t.get("floating_index"),
            "floating_spread_bps": t.get("floating_spread_bps"),
            "wal_years": t.get("wal_years"),
            "spread_to_benchmark": t.get("spread_to_benchmark"),
            "benchmark": t.get("benchmark"),
            "rating_sp": t.get("rating_sp"),
            "rating_moodys": t.get("rating_moodys"),
            "rating_kbra": t.get("rating_kbra"),
        })
        d["n_tranches"] += 1
        # The senior-most tranche is the longest-WAL AAA-rated row by convention;
        # we approximate with the widest-WAL row in the deal that has a spread.
        spread = t.get("spread_to_benchmark")
        wal = t.get("wal_years")
        if spread is not None and wal is not None:
            if d["senior_wal_years"] is None or wal > d["senior_wal_years"]:
                d["senior_spread_bps"] = spread
                d["senior_wal_years"] = wal
                d["senior_class_name"] = t.get("class_name")
    deals = list(by_deal.values())
    deals.sort(key=lambda d: (d.get("filing_date") or "", d["accession_no"]), reverse=True)
    return deals


def _abs_pricing_for(query: str, limit: int) -> list[dict]:
    pat = f"%{query}%"
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM abs_pricing
            WHERE (issuer LIKE ? COLLATE NOCASE OR deal_name LIKE ? COLLATE NOCASE)
            ORDER BY pricing_date DESC, accession_no, wal
            LIMIT ?
            """,
            (pat, pat, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _edgar_for(query: str, limit: int) -> list[dict]:
    pat = f"%{query}%"
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT accession_no, company_name, form_type, filed_at,
                   description, url, asset_class, issuance_type
            FROM edgar_filings
            WHERE company_name LIKE ? COLLATE NOCASE
            ORDER BY filed_at DESC
            LIMIT ?
            """,
            (pat, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _kbra_for(query: str, limit: int) -> list[dict]:
    pat = f"%{query}%"
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM kbra_presales
            WHERE (issuer LIKE ? COLLATE NOCASE OR deal_name LIKE ? COLLATE NOCASE)
            ORDER BY closing_date DESC, parsed_at DESC
            LIMIT ?
            """,
            (pat, pat, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _articles_for(query: str, min_score: int, days_back: int, limit: int) -> list[dict]:
    pat = f"%{query}%"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, feed_name, feed_category, title, snippet, url,
                   published_at, fetched_at, relevance_score, source_type
            FROM articles
            WHERE relevance_score >= ?
              AND COALESCE(published_at, fetched_at) >= ?
              AND (title LIKE ? COLLATE NOCASE OR snippet LIKE ? COLLATE NOCASE)
            ORDER BY relevance_score DESC,
                     COALESCE(published_at, fetched_at) DESC
            LIMIT ?
            """,
            (min_score, cutoff, pat, pat, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _summarize_stats(deals: list[dict]) -> dict:
    if not deals:
        return {
            "n_deals": 0,
            "total_volume": 0.0,
            "earliest_filing": None,
            "latest_filing": None,
            "n_asset_classes": 0,
        }
    dates = [d.get("filing_date") for d in deals if d.get("filing_date")]
    return {
        "n_deals": len(deals),
        "total_volume": float(sum((d.get("total_deal_size") or 0) for d in deals)),
        "earliest_filing": min(dates) if dates else None,
        "latest_filing": max(dates) if dates else None,
        "n_asset_classes": len({d.get("asset_class") for d in deals if d.get("asset_class")}),
    }


def get_issuer_summary(
    query: str,
    deals_limit: int = 200,
    edgar_limit: int = 100,
    article_limit: int = 50,
    article_min_score: int = 3,
    article_days_back: int = 180,
) -> Optional[dict]:
    """Substring-search across every issuer-bearing table; return the lot."""
    q = (query or "").strip()
    if not q:
        return None

    tranches = _abs_new_issues_for(q, deals_limit)
    deals = _aggregate_deals(tranches)
    pricing = _abs_pricing_for(q, deals_limit)
    filings = _edgar_for(q, edgar_limit)
    presales = _kbra_for(q, edgar_limit)
    articles = _articles_for(q, article_min_score, article_days_back, article_limit)

    return {
        "query": q,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "stats": _summarize_stats(deals),
        "deals": deals,
        "pricing": pricing,
        "edgar_filings": filings,
        "kbra_presales": presales,
        "articles": articles,
    }
