"""
backend/api/routes.py — REST endpoints for the dashboard frontend.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter()


# ── ARTICLES / NEWS ──────────────────────────────────────────

@router.get("/articles")
def get_articles(
    min_score: int = Query(default=4, ge=1, le=5),
    category: Optional[str] = Query(default=None),
    source_type: Optional[str] = Query(default=None, pattern="^(news|letter)$"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
):
    """
    Return scored articles filtered by relevance and optional category/source.
    Default: score >= 4 (macro/credit/finance relevant).
    """
    from cache.db import get_conn

    with get_conn() as conn:
        base = """
            SELECT id, feed_name, feed_category, title, snippet, url,
                   published_at, fetched_at, relevance_score, relevance_tags,
                   is_read, source_type
            FROM articles
            WHERE relevance_score >= ?
        """
        params: list = [min_score]

        if category:
            base += " AND feed_category = ?"
            params.append(category)

        if source_type:
            base += " AND source_type = ?"
            params.append(source_type)

        base += " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        rows = conn.execute(base, params).fetchall()

    return {"items": [dict(r) for r in rows], "offset": offset, "limit": limit}


@router.post("/articles/{article_id}/read")
def mark_article_read(article_id: str):
    """Mark an article as read."""
    from cache.db import mark_article_read as _mark

    updated = _mark(article_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"id": article_id, "is_read": 1}


@router.get("/articles/feed-health")
async def get_feed_health():
    """Feed health status for the status bar."""
    from cache.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM feed_health ORDER BY is_live DESC, feed_name"
        ).fetchall()
    return [dict(r) for r in rows]


class DigestRequest(BaseModel):
    hours_back: int = Field(default=24, ge=1, le=168)
    min_score: int = Field(default=4, ge=1, le=5)


def _reshape_digest(r: dict) -> dict:
    """DB row -> API shape (nested date_range), matching the generator output."""
    return {
        "date": r["date"],
        "session": r["session"],
        "summary": r["summary"],
        "article_count": r["article_count"],
        "hours_back": r["hours_back"],
        "min_score": r["min_score"],
        "date_range": {"from": r["date_from"], "to": r["date_to"]},
        "model": r["model"],
        "generated_at": r["generated_at"],
    }


@router.post("/digest")
def post_digest(body: DigestRequest = DigestRequest()):
    """Generate an on-demand AI prose digest and persist it (one per day)."""
    from data.digest import generate_digest, DigestError
    from cache.db import save_digest

    try:
        result = generate_digest(hours_back=body.hours_back, min_score=body.min_score)
    except DigestError as e:
        # 422: request is well-formed but can't be fulfilled (no key / no articles).
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Digest generation failed: {e}")

    # Bucket by US/Eastern day + AM/PM (before vs. from noon ET).
    from datetime import datetime
    from zoneinfo import ZoneInfo

    et = datetime.fromisoformat(result["generated_at"]).astimezone(ZoneInfo("America/New_York"))
    day = et.date().isoformat()
    session = "AM" if et.hour < 12 else "PM"
    result["date"] = day
    result["session"] = session
    save_digest(
        {
            "date": day,
            "session": session,
            "summary": result["summary"],
            "article_count": result["article_count"],
            "hours_back": result["hours_back"],
            "min_score": result["min_score"],
            "date_from": result["date_range"]["from"],
            "date_to": result["date_range"]["to"],
            "model": result["model"],
            "generated_at": result["generated_at"],
        }
    )
    return result


@router.get("/digests")
def list_digests(limit: int = Query(default=60, le=365)):
    """Return saved daily digests, newest first."""
    from cache.db import get_digests

    return [_reshape_digest(r) for r in get_digests(limit)]


@router.post("/articles/refresh")
async def trigger_feed_refresh():
    """
    Manually fetch all feeds and classify the full backlog of unscored articles.
    This is the only on-demand path that spends Claude tokens (automatic
    classification is disabled — see refresh_intervals.news_feeds_minutes).
    """
    from datetime import datetime, timezone
    from data.feeds import fetch_all_feeds
    from data.classifier import classify_articles
    from cache.db import set_meta

    n = await fetch_all_feeds()
    scored = 0
    for _ in range(20):  # safety cap (~1000 articles) to bound a single command
        c = await classify_articles(batch_size=50)
        scored += c
        if c == 0:
            break
    set_meta("last_news_refresh", datetime.now(timezone.utc).isoformat())
    return {"fetched": n, "classified": scored}


# ── MARKET DATA ──────────────────────────────────────────────

@router.get("/market/snapshot")
def get_market_snapshot():
    """Latest prices + % change for all configured tickers."""
    from data.market import get_market_snapshot
    return get_market_snapshot()


@router.get("/market/history/{ticker}")
def get_market_history(ticker: str, limit: int = Query(default=252)):
    """Price history for a single ticker."""
    from cache.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT date, value FROM metrics
               WHERE series_id = ?
               ORDER BY date DESC LIMIT ?""",
            (f"mkt_{ticker}", limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── FRED / MACRO DATA ────────────────────────────────────────

@router.get("/fred/latest")
def get_fred_latest():
    """Most recent value for all FRED series."""
    from data.fred import get_latest_fred_values
    return get_latest_fred_values()


@router.get("/fred/history/{series_id}")
def get_fred_history(series_id: str, limit: int = Query(default=120)):
    """Historical values for a single FRED series."""
    from data.fred import get_series_history
    return get_series_history(series_id, limit=limit)


@router.post("/indicators/refresh")
def trigger_indicators_refresh():
    """Recompute derived indicators (recession probits, Excess Bond Premium).

    The NY Fed probit reads T10Y3M from the DB, so this assumes a FRED fetch has
    already populated it; call POST /api/fred-style refresh first on a cold DB.
    """
    from data.indicators import compute_all_indicators
    return {"rows": compute_all_indicators()}


@router.post("/hhdc/refresh")
def trigger_hhdc_refresh():
    """Pull the latest NY Fed Household Debt & Credit flow-into-delinquency rates."""
    from data.hhdc import fetch_hhdc_transitions
    return {"rows": fetch_hhdc_transitions()}


@router.get("/fred/forward-curve")
def get_fred_forward_curve():
    """Treasury yield curve at three snapshots: today, ~6mo ago, ~1yr ago."""
    from data.fred import get_forward_curve
    return get_forward_curve()


@router.get("/fred/sofr")
def get_fred_sofr(limit: int = Query(default=300, le=2000)):
    """SOFR 1M/3M term averages + a trailing 1Y rate computed from the SOFR Index."""
    from data.fred import get_sofr_rates
    return get_sofr_rates(limit=limit)


# ── EDGAR / ABS FILINGS ──────────────────────────────────────

@router.get("/edgar/filings")
def get_edgar_filings(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    form_type: Optional[str] = Query(default=None),
    asset_class: Optional[str] = Query(default=None),
):
    """Recent ABS-related EDGAR filings, newest first, with optional filters."""
    from data.edgar import get_recent_filings
    return get_recent_filings(
        limit=limit, offset=offset, form_type=form_type, asset_class=asset_class
    )


@router.get("/abs/pricing")
def get_abs_pricing(limit: int = Query(default=40, le=100), segment: Optional[str] = None):
    """Recent ABS deals priced at new issue, with per-tranche spreads."""
    from data.abs_pricing import get_abs_pricing_deals
    return get_abs_pricing_deals(limit=limit, segment=segment)


@router.get("/abs/spread-momentum")
def get_abs_spread_momentum():
    """Senior/subordinate new-issue spread per deal over time, by segment."""
    from data.abs_pricing import get_abs_spread_momentum as _mom
    return _mom()


@router.post("/abs/pricing/refresh")
def trigger_abs_pricing_refresh(days_back: int = Query(default=30, le=180)):
    """Discover and parse recent ABS pricing term sheets."""
    from data.abs_pricing import fetch_abs_pricing
    return {"tranches": fetch_abs_pricing(days_back=days_back)}


@router.get("/edgar/facets")
def get_edgar_facets():
    """Distinct form types and asset classes for filter dropdowns."""
    from data.edgar import get_edgar_facets
    return get_edgar_facets()


@router.post("/edgar/refresh")
def trigger_edgar_refresh():
    """Manually trigger an EDGAR filing fetch."""
    from data.edgar import fetch_abs_filings
    n = fetch_abs_filings(days_back=3)
    return {"inserted": n}


# ── SYSTEM ───────────────────────────────────────────────────

@router.get("/status")
def get_status():
    """System health: DB row counts, last fetch times."""
    from cache.db import get_conn, get_meta

    with get_conn() as conn:
        article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        scored_count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE relevance_score IS NOT NULL"
        ).fetchone()[0]
        metric_count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        filing_count = conn.execute("SELECT COUNT(*) FROM edgar_filings").fetchone()[0]
        live_feeds = conn.execute(
            "SELECT COUNT(*) FROM feed_health WHERE is_live = 1"
        ).fetchone()[0]
        total_feeds = conn.execute("SELECT COUNT(*) FROM feed_health").fetchone()[0]

    return {
        "articles": {"total": article_count, "scored": scored_count},
        "metrics": metric_count,
        "edgar_filings": filing_count,
        "feeds": {"live": live_feeds, "total": total_feeds},
        "last_news_refresh": get_meta("last_news_refresh"),
    }
