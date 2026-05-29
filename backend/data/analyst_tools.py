"""
backend/data/analyst_tools.py — read-only tools the analyst chat can call.

Each tool is a thin wrapper over an existing DB query helper. The dispatch
table is consumed by `chat_with_briefing` in `data/analyst.py`; the schema
list is what the Anthropic SDK sees.

All tools return JSON-serializable dicts/lists. Errors are caught by the
dispatcher and surfaced as `{"error": "..."}` to the model.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from cache import db


# ── Tool implementations ────────────────────────────────────────────────────

def _tool_get_indicator_history(
    series_id: str,
    days_back: int = 730,
) -> dict:
    """Historical observations for a single metric series_id."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date().isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value, label, category FROM metrics "
            "WHERE series_id = ? AND date >= ? AND value IS NOT NULL "
            "ORDER BY date ASC",
            (series_id, cutoff),
        ).fetchall()
    if not rows:
        return {"series_id": series_id, "n": 0, "observations": []}
    return {
        "series_id": series_id,
        "label": rows[0]["label"],
        "category": rows[0]["category"],
        "n": len(rows),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "observations": [{"date": r["date"], "value": float(r["value"])} for r in rows],
    }


def _tool_get_abs_spread_series(
    asset_class: str,
    rating_bucket: str = "AAA",
    metric: str = "spread_to_benchmark",
    days_back: int = 365,
) -> dict:
    """Weekly-binned spread series for one ABS asset class + rating bucket."""
    if metric not in ("spread_to_benchmark", "implied_yield",
                      "floating_spread_bps", "coupon_rate"):
        return {"error": f"metric must be one of spread_to_benchmark/implied_yield/floating_spread_bps/coupon_rate; got {metric!r}"}
    rating_map = {
        "AAA": ["AAA", "Aaa"],
        "AA":  ["AA+", "AA", "AA-", "Aa1", "Aa2", "Aa3"],
        "A":   ["A+", "A", "A-", "A1", "A2", "A3"],
        "BBB": ["BBB+", "BBB", "BBB-", "Baa1", "Baa2", "Baa3"],
    }
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    with db.get_conn() as conn:
        if rating_bucket == "all":
            sql = f"""
                SELECT strftime('%Y-W%W', filing_date) AS week,
                       MIN(filing_date) AS week_start,
                       AVG({metric}) AS avg_spread,
                       MIN({metric}) AS min_spread,
                       MAX({metric}) AS max_spread,
                       COUNT(*) AS n_tranches
                FROM abs_new_issues
                WHERE filing_date >= ? AND asset_class = ?
                  AND {metric} IS NOT NULL
                  AND parse_confidence IN ('high','medium')
                GROUP BY week ORDER BY week ASC
            """
            params = [since, asset_class]
        else:
            ratings = rating_map.get(rating_bucket)
            if not ratings:
                return {"error": f"rating_bucket must be all/AAA/AA/A/BBB; got {rating_bucket!r}"}
            placeholders = ",".join("?" * len(ratings))
            sql = f"""
                SELECT strftime('%Y-W%W', filing_date) AS week,
                       MIN(filing_date) AS week_start,
                       AVG({metric}) AS avg_spread,
                       MIN({metric}) AS min_spread,
                       MAX({metric}) AS max_spread,
                       COUNT(*) AS n_tranches
                FROM abs_new_issues
                WHERE filing_date >= ? AND asset_class = ?
                  AND {metric} IS NOT NULL
                  AND parse_confidence IN ('high','medium')
                  AND (rating_sp IN ({placeholders})
                    OR rating_moodys IN ({placeholders})
                    OR rating_kbra IN ({placeholders}))
                GROUP BY week ORDER BY week ASC
            """
            params = [since, asset_class] + ratings * 3
        rows = conn.execute(sql, params).fetchall()

    return {
        "asset_class": asset_class,
        "rating_bucket": rating_bucket,
        "metric": metric,
        "n_weeks": len(rows),
        "series": [dict(r) for r in rows],
    }


def _tool_get_bdc_summary(period: Optional[str] = None) -> dict:
    """Per-BDC roll-up for the latest (or specified) reporting period."""
    from data.bdc import get_bdc_summary

    try:
        rows = get_bdc_summary(period=period)
    except Exception as e:
        return {"error": str(e)}
    return {"period": period or "latest", "n_bdcs": len(rows), "bdcs": rows}


def _tool_get_recent_filings(
    asset_class: Optional[str] = None,
    form_type: Optional[str] = None,
    days_back: int = 30,
    limit: int = 50,
) -> dict:
    """Recent EDGAR filings, optionally filtered by asset class or form type."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date().isoformat()
    sql = ("SELECT accession_no, company_name, form_type, filed_at, "
           "description, url, asset_class, issuance_type "
           "FROM edgar_filings WHERE filed_at >= ?")
    params: list = [cutoff]
    if asset_class:
        sql += " AND asset_class = ?"
        params.append(asset_class)
    if form_type:
        sql += " AND form_type = ?"
        params.append(form_type)
    sql += " ORDER BY filed_at DESC LIMIT ?"
    params.append(min(int(limit), 200))

    with db.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {
        "window_days": days_back,
        "asset_class": asset_class,
        "form_type": form_type,
        "n": len(rows),
        "filings": [dict(r) for r in rows],
    }


def _tool_search_articles(
    min_score: int = 4,
    category: Optional[str] = None,
    days_back: int = 14,
    limit: int = 30,
) -> dict:
    """Scored news articles in a recent window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    sql = ("SELECT id, feed_name, feed_category, title, snippet, url, "
           "published_at, fetched_at, relevance_score, relevance_tags "
           "FROM articles "
           "WHERE relevance_score >= ? "
           "AND COALESCE(published_at, fetched_at) >= ?")
    params: list = [int(min_score), cutoff]
    if category:
        sql += " AND feed_category = ?"
        params.append(category)
    sql += " ORDER BY relevance_score DESC, COALESCE(published_at, fetched_at) DESC LIMIT ?"
    params.append(min(int(limit), 100))
    with db.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {
        "window_days": days_back,
        "min_score": min_score,
        "category": category,
        "n": len(rows),
        "articles": [dict(r) for r in rows],
    }


def _tool_get_market_history(ticker: str, days_back: int = 365) -> dict:
    """Daily close history for a ticker."""
    series_id = f"mkt_{ticker}"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date().isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics "
            "WHERE series_id = ? AND date >= ? AND value IS NOT NULL "
            "ORDER BY date ASC",
            (series_id, cutoff),
        ).fetchall()
    return {
        "ticker": ticker,
        "n": len(rows),
        "observations": [{"date": r["date"], "close": float(r["value"])} for r in rows],
    }


# ── Schemas (sent to Claude) ────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_indicator_history",
        "description": (
            "Return daily/weekly/quarterly observations for a single metric series "
            "(FRED series like DGS10 or T10Y3M, or computed series like "
            "RECESSION_RISK_ENSEMBLE, EBP, NEAR_TERM_FWD_SPREAD, CFSI, OFR_FSI, "
            "BIS_CREDIT_GAP_US, CREDIT_IMPULSE). Use when the briefing's current "
            "value isn't enough and the user wants the trajectory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "series_id": {"type": "string", "description": "Series id, e.g. DGS10, T10Y3M, RECESSION_RISK_ENSEMBLE, EBP, CFSI."},
                "days_back": {"type": "integer", "description": "How far back to fetch. Default 730 (≈2 years)."},
            },
            "required": ["series_id"],
        },
    },
    {
        "name": "get_abs_spread_series",
        "description": (
            "Weekly-binned ABS new-issue spread series for one asset class + rating "
            "bucket. asset_class examples: prime_auto_loan, subprime_auto_loan, "
            "credit_card, equipment, consumer_loan, student_loan, solar, "
            "esoteric_wireless, rmbs_non_agency, cmbs, clo. rating_bucket: "
            "all/AAA/AA/A/BBB. Use when the user asks about ABS spread trends."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_class": {"type": "string"},
                "rating_bucket": {"type": "string", "enum": ["all", "AAA", "AA", "A", "BBB"], "default": "AAA"},
                "metric": {"type": "string", "enum": ["spread_to_benchmark", "implied_yield", "floating_spread_bps", "coupon_rate"], "default": "spread_to_benchmark"},
                "days_back": {"type": "integer", "default": 365},
            },
            "required": ["asset_class"],
        },
    },
    {
        "name": "get_bdc_summary",
        "description": (
            "Per-BDC roll-up (nonaccrual rate, NAV, lien mix, weighted-average "
            "rate) for the latest reporting period, or a specified period (YYYYMMDD). "
            "Use when the user asks about BDC stress or wants a specific name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "Optional YYYYMMDD period. Omit for the latest period."},
            },
        },
    },
    {
        "name": "get_recent_filings",
        "description": (
            "Recent SEC EDGAR ABS filings. Optionally filter by asset_class (e.g. "
            "'auto loan', 'credit card', 'CLO', 'mortgage') or form_type (e.g. "
            "'424B5', 'FWP', 'ABS-15G', 'ABS-EE')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_class": {"type": "string"},
                "form_type": {"type": "string"},
                "days_back": {"type": "integer", "default": 30},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "search_articles",
        "description": (
            "Scored news articles in a recent window. Filter by min_score (1-5; "
            "4+ is recommended for high-relevance) and feed_category (one of: "
            "macro, credit, structured_finance, fintech, regulation, data_science)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_score": {"type": "integer", "default": 4, "minimum": 1, "maximum": 5},
                "category": {"type": "string"},
                "days_back": {"type": "integer", "default": 14},
                "limit": {"type": "integer", "default": 30},
            },
        },
    },
    {
        "name": "get_market_history",
        "description": (
            "Daily close history for a tracked ETF/index ticker (e.g. SPY, QQQ, "
            "VIX, HYG, LQD, IEF, TLT, JNK)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "days_back": {"type": "integer", "default": 365},
            },
            "required": ["ticker"],
        },
    },
]


TOOL_DISPATCH = {
    "get_indicator_history": _tool_get_indicator_history,
    "get_abs_spread_series": _tool_get_abs_spread_series,
    "get_bdc_summary": _tool_get_bdc_summary,
    "get_recent_filings": _tool_get_recent_filings,
    "search_articles": _tool_search_articles,
    "get_market_history": _tool_get_market_history,
}
