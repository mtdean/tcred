"""
backend/data/entities.py — cross-source entity lookup.

Given a company name, pull everything the monitor knows about it in one
payload: BDC holdings (who holds it, at what marks, over time), EDGAR
filings, and scored news articles (FTS). v1 is search-driven — no
precomputed entity table; SQL LIKE narrows candidates and a word-boundary
regex filters them (the watchlist lesson: 'Ares' must not match 'shares').

BDC holding identifiers are raw XBRL member blobs ("Investments -
non-controlled/non-affiliated Secured Debt Software Kipu Buyer, LLC Asset
Type First Lien Term Loan …"), so holdings matching is substring-into-blob
by design — the entity name finds the blob, not the other way around.
"""

from __future__ import annotations

import re

from cache.db import get_conn, search_articles_fts

MIN_QUERY_LEN = 3
# Widely-held names match a lot of rows (Pluralsight: 49 tranches × many
# periods of comparatives). The cap guards payload size, not correctness —
# rows are period-DESC so a hit trims the OLDEST periods first.
MAX_HOLDING_ROWS = 2000


def _word_boundary_re(name: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(name.strip()) + r"(?![A-Za-z0-9])",
                      re.IGNORECASE)


def lookup_entity(name: str) -> dict:
    """Everything known about `name` across BDC holdings, EDGAR, and news."""
    name = (name or "").strip()
    if len(name) < MIN_QUERY_LEN:
        return {"error": f"query must be at least {MIN_QUERY_LEN} characters"}

    rx = _word_boundary_re(name)
    like = f"%{name}%"

    with get_conn() as conn:
        holding_rows = [
            dict(r) for r in conn.execute(
                """
                SELECT bdc_name, cik, period, company_name, investment_type,
                       industry, interest_rate, cost_basis, fair_value,
                       is_nonaccrual
                FROM bdc_holdings
                WHERE company_name LIKE ?
                ORDER BY period DESC, fair_value DESC
                LIMIT ?
                """,
                (like, MAX_HOLDING_ROWS * 3),
            )
            if rx.search(r["company_name"] or "")
        ][:MAX_HOLDING_ROWS]

        filings = [
            dict(r) for r in conn.execute(
                """
                SELECT accession_no, company_name, form_type, filed_at,
                       description, url, asset_class
                FROM edgar_filings
                WHERE company_name LIKE ? OR description LIKE ?
                ORDER BY filed_at DESC
                LIMIT 150
                """,
                (like, like),
            )
            if rx.search((r["company_name"] or "") + " " + (r["description"] or ""))
        ][:50]

    # Roll holdings up per period: total cost/FV across every matching
    # tranche and BDC → the entity's mark trajectory.
    by_period: dict[str, dict] = {}
    for h in holding_rows:
        p = by_period.setdefault(h["period"], {
            "period": h["period"], "n_tranches": 0, "bdcs": set(),
            "cost": 0.0, "fv": 0.0, "any_nonaccrual": False,
        })
        p["n_tranches"] += 1
        p["bdcs"].add(h["bdc_name"])
        p["cost"] += h["cost_basis"] or 0
        p["fv"] += h["fair_value"] or 0
        p["any_nonaccrual"] = p["any_nonaccrual"] or bool(h["is_nonaccrual"])
    periods = []
    for p in sorted(by_period.values(), key=lambda d: d["period"]):
        periods.append({
            "period": p["period"],
            "n_tranches": p["n_tranches"],
            "n_bdcs": len(p["bdcs"]),
            "cost_basis": p["cost"],
            "fair_value": p["fv"],
            "mark_to_cost": p["fv"] / p["cost"] if p["cost"] > 0 else None,
            "any_nonaccrual": p["any_nonaccrual"],
        })

    latest_period = periods[-1]["period"] if periods else None
    current = [
        {
            "bdc_name": h["bdc_name"],
            "investment_type": h["investment_type"],
            "industry": h["industry"],
            "interest_rate": h["interest_rate"],
            "cost_basis": h["cost_basis"],
            "fair_value": h["fair_value"],
            "mark_to_cost": (h["fair_value"] / h["cost_basis"])
                if h["fair_value"] and h["cost_basis"] else None,
            "is_nonaccrual": h["is_nonaccrual"],
        }
        for h in holding_rows if h["period"] == latest_period
    ]

    articles = search_articles_fts(f'"{name}"', min_score=1, days_back=730, limit=25)

    return {
        "query": name,
        "holdings_by_period": periods,
        "latest_period": latest_period,
        "current_holders": current,
        "filings": filings,
        "articles": articles,
        "truncated_holdings": len(holding_rows) >= MAX_HOLDING_ROWS,
    }
