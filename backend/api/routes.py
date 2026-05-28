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
    source_type: Optional[str] = Query(
        default=None,
        description="Single slug or comma-separated list (e.g. 'wsj,ft,bloomberg').",
    ),
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
            slugs = [s.strip() for s in source_type.split(",") if s.strip()]
            if slugs:
                placeholders = ",".join("?" * len(slugs))
                base += f" AND source_type IN ({placeholders})"
                params.extend(slugs)

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


@router.get("/abs/spread-momentum/deltas")
def get_abs_spread_momentum_deltas():
    """Spread change vs prior comparable deal, per segment + seniority bucket.

    Widening = positive bps, tightening = negative; includes a rolling z-score.
    """
    from data.abs_pricing import get_abs_spread_momentum_deltas as _deltas
    return _deltas()


@router.post("/abs/pricing/refresh")
def trigger_abs_pricing_refresh(days_back: int = Query(default=30, le=180)):
    """Discover and parse recent ABS pricing term sheets (FWP path)."""
    from datetime import datetime, timezone
    from data.abs_pricing import fetch_abs_pricing
    from cache.db import set_meta

    n = fetch_abs_pricing(days_back=days_back)
    set_meta("last_abs_pricing_refresh", datetime.now(timezone.utc).isoformat())
    return {"tranches": n}


# ── 424B5 NEW-ISSUE PARSER (richer schema than FWP path) ─────────────────────

@router.get("/abs/new-issues")
def get_abs_new_issues_endpoint(
    asset_class: Optional[str] = Query(default=None),
    days_back: int = Query(default=365, le=3650),
    min_confidence: str = Query(default="medium", pattern="^(low|medium|high)$"),
    limit: int = Query(default=500, le=2000),
):
    """Tranche-level new-issue records parsed from 424B5 filings."""
    from cache.db import get_abs_new_issues

    rows = get_abs_new_issues(
        asset_class=asset_class,
        days_back=days_back,
        min_confidence=min_confidence,
        limit=limit,
    )
    return {"items": rows, "count": len(rows)}


# Rating bucket → list of agency-specific labels that belong in it. The
# spread-series endpoint matches a tranche to a bucket if ANY of its agencies
# falls in the bucket's labels (so a deal rated AAA/Aaa by S&P/Moody's is one
# observation in the AAA bucket).
#
# The "all" pseudo-bucket bypasses the rating filter entirely. Useful because
# 424B5 prospectuses rarely disclose ratings inline — final ratings typically
# land in the FWP or rating-agency reports — so requiring AAA matching often
# yields zero observations even when we have tranche data.
_RATING_BUCKET_MAP: dict[str, list[str]] = {
    "AAA": ["AAA", "Aaa"],
    "AA":  ["AA+", "AA", "AA-", "Aa1", "Aa2", "Aa3"],
    "A":   ["A+", "A", "A-", "A1", "A2", "A3"],
    "BBB": ["BBB+", "BBB", "BBB-", "Baa1", "Baa2", "Baa3"],
    "BB_and_below": [],  # everything that doesn't match the buckets above
}


@router.get("/abs/spread-series")
def get_abs_spread_series(
    asset_class: str = Query(...),
    rating_bucket: str = Query(default="AAA"),
    metric: str = Query(
        default="spread_to_benchmark",
        pattern="^(spread_to_benchmark|implied_yield|floating_spread_bps|coupon_rate)$",
    ),
    days_back: int = Query(default=365, le=3650),
):
    """Weekly-binned median/min/max of `metric` for one asset class + rating bucket.

    Used by the new-issue spread tracker chart. The bucket maps to a fixed set
    of agency labels; we match tranches where any of (rating_sp, rating_moodys,
    rating_kbra) lands in that set.
    """
    from cache.db import get_conn

    since = datetime_now_iso_days_ago(days_back)

    with get_conn() as conn:
        if rating_bucket == "all":
            # Skip the rating filter entirely — useful when ratings aren't
            # disclosed in the parsed prospectus and you just want the full
            # weekly spread distribution for the asset class.
            sql = f"""
                SELECT
                    strftime('%Y-W%W', filing_date) AS week,
                    MIN(filing_date)                AS week_start,
                    AVG({metric})                   AS avg_spread,
                    MIN({metric})                   AS min_spread,
                    MAX({metric})                   AS max_spread,
                    COUNT(*)                        AS n_tranches
                FROM abs_new_issues
                WHERE filing_date >= ?
                  AND asset_class = ?
                  AND {metric} IS NOT NULL
                  AND parse_confidence IN ('high','medium')
                GROUP BY week
                ORDER BY week ASC
            """
            params: list = [since, asset_class]
            rows = conn.execute(sql, params).fetchall()
            series = [
                {
                    "week": r["week"],
                    "week_start": r["week_start"],
                    "avg_spread": r["avg_spread"],
                    "min_spread": r["min_spread"],
                    "max_spread": r["max_spread"],
                    "n_tranches": r["n_tranches"],
                }
                for r in rows
            ]
            return {
                "asset_class": asset_class,
                "rating_bucket": rating_bucket,
                "metric": metric,
                "series": series,
            }
        ratings = _RATING_BUCKET_MAP.get(rating_bucket, [])
        if ratings:
            placeholders = ",".join("?" * len(ratings))
            sql = f"""
                SELECT
                    strftime('%Y-W%W', filing_date) AS week,
                    MIN(filing_date)                AS week_start,
                    AVG({metric})                   AS avg_spread,
                    MIN({metric})                   AS min_spread,
                    MAX({metric})                   AS max_spread,
                    COUNT(*)                        AS n_tranches
                FROM abs_new_issues
                WHERE filing_date >= ?
                  AND asset_class = ?
                  AND {metric} IS NOT NULL
                  AND parse_confidence IN ('high','medium')
                  AND (rating_sp     IN ({placeholders})
                    OR rating_moodys IN ({placeholders})
                    OR rating_kbra   IN ({placeholders}))
                GROUP BY week
                ORDER BY week ASC
            """
            params = [since, asset_class] + ratings * 3
        else:
            # BB_and_below: rows whose ratings don't land in any IG bucket
            # (treated as null = unrated, also captured here).
            ig_flat: list[str] = []
            for b in ("AAA", "AA", "A", "BBB"):
                ig_flat.extend(_RATING_BUCKET_MAP[b])
            placeholders = ",".join("?" * len(ig_flat))
            sql = f"""
                SELECT
                    strftime('%Y-W%W', filing_date) AS week,
                    MIN(filing_date)                AS week_start,
                    AVG({metric})                   AS avg_spread,
                    MIN({metric})                   AS min_spread,
                    MAX({metric})                   AS max_spread,
                    COUNT(*)                        AS n_tranches
                FROM abs_new_issues
                WHERE filing_date >= ?
                  AND asset_class = ?
                  AND {metric} IS NOT NULL
                  AND parse_confidence IN ('high','medium')
                  AND (rating_sp NOT IN ({placeholders}) OR rating_sp IS NULL)
                  AND (rating_moodys NOT IN ({placeholders}) OR rating_moodys IS NULL)
                  AND (rating_kbra NOT IN ({placeholders}) OR rating_kbra IS NULL)
                GROUP BY week
                ORDER BY week ASC
            """
            params = [since, asset_class] + ig_flat * 3

        rows = conn.execute(sql, params).fetchall()

    return {
        "asset_class": asset_class,
        "rating_bucket": rating_bucket,
        "metric": metric,
        "series": [dict(r) for r in rows],
    }


@router.get("/abs/deal-summary")
def get_abs_deal_summary(days_back: int = Query(default=90, le=3650)):
    """Per asset class: deal count, total volume, avg spread. Trailing window."""
    from cache.db import get_conn

    since = datetime_now_iso_days_ago(days_back)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                asset_class,
                COUNT(DISTINCT accession_no)  AS deal_count,
                SUM(principal_amount)         AS total_volume,
                AVG(spread_to_benchmark)      AS avg_spread_bps,
                MIN(filing_date)              AS earliest,
                MAX(filing_date)              AS latest
            FROM abs_new_issues
            WHERE filing_date >= ?
              AND parse_confidence IN ('high','medium')
            GROUP BY asset_class
            ORDER BY total_volume DESC
            """,
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/abs/new-issues/refresh")
def trigger_abs_new_issues_refresh(days_back: int = Query(default=14, le=120)):
    """Discover + parse recent 424B5 ABS filings (Claude fallback enabled).

    Synchronous: blocks until discovery + parse + DB write are complete, then
    returns the tranche count. Mirrors POST /articles/refresh. Cadence-wise,
    the scheduler runs a 2-day window every 4h; the button defaults to 14d so
    a manual press also backfills anything the scheduler missed (EDGAR FTS 500s
    intermittently and the rerun catches stragglers).
    """
    from datetime import datetime, timezone
    from data.abs_parser import fetch_and_parse_abs_424b5
    from cache.db import set_meta

    n = fetch_and_parse_abs_424b5(days_back=days_back)
    set_meta("last_abs_424b5_refresh", datetime.now(timezone.utc).isoformat())
    return {"tranches_stored": n}


def datetime_now_iso_days_ago(days: int) -> str:
    """Helper: ISO date `days` ago. Kept here so the 424B5 endpoints stay self-contained."""
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


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


# ── PHASE 7: BDC PORTFOLIO MONITOR ───────────────────────────

@router.get("/bdc/watch-list")
def get_bdc_watch_list():
    """Curated BDC watch list (CIK / ticker / name) for UI sorting/highlight."""
    from data.bdc import get_watch_list
    return get_watch_list()


@router.get("/bdc/nonaccrual-trend")
def get_bdc_nonaccrual_trend():
    """Aggregate non-accrual rate across BDCs by reporting period — core stress signal."""
    from data.bdc import get_bdc_nonaccrual_trend as _trend
    return _trend()


@router.get("/bdc/summary")
def get_bdc_summary(period: Optional[str] = Query(default=None)):
    """Per-BDC roll-up for the latest (or specified) reporting period."""
    from data.bdc import get_bdc_summary as _summary
    return _summary(period=period)


@router.get("/bdc/nonaccruals")
def get_bdc_nonaccruals(limit: int = Query(default=100, le=500)):
    """Individual non-accrual holdings across all BDCs, latest period."""
    from data.bdc import get_bdc_nonaccruals as _na
    return _na(limit=limit)


@router.post("/bdc/refresh")
def trigger_bdc_refresh():
    """Download + parse SEC BDC bulk dataset. Token-free."""
    from datetime import datetime, timezone
    from data.bdc import fetch_bdc_data
    from cache.db import set_meta
    n = fetch_bdc_data()
    set_meta("last_bdc_refresh", datetime.now(timezone.utc).isoformat())
    return {"holdings_stored": n}


# ── PHASE 7: REGULATORY FLOW MONITOR ─────────────────────────

@router.get("/regulatory/actions")
def get_regulatory_actions(
    agency: Optional[str] = Query(default=None),
    action_type: Optional[str] = Query(default=None),
    min_score: int = Query(default=1, ge=1, le=5),
    days_back: int = Query(default=90, le=365),
    limit: int = Query(default=100, le=500),
):
    """Recent regulatory actions, filterable by agency / type / relevance score."""
    from data.regulatory import get_regulatory_actions as _list
    return _list(
        agency=agency, action_type=action_type,
        min_score=min_score, days_back=days_back, limit=limit,
    )


@router.post("/regulatory/refresh")
def trigger_regulatory_refresh(days_back: int = Query(default=7, le=60)):
    """Fetch new Federal Register documents + agency RSS. Token-free.

    Claude scoring is a separate manual step — POST /regulatory/score.
    """
    from datetime import datetime, timezone
    from data.regulatory import fetch_regulatory_actions
    from cache.db import set_meta
    n = fetch_regulatory_actions(days_back=days_back)
    set_meta("last_regulatory_refresh", datetime.now(timezone.utc).isoformat())
    return {"actions_stored": n}


@router.post("/regulatory/score")
def trigger_regulatory_score(limit: int = Query(default=100, le=500)):
    """Manually score unscored regulatory actions with Claude (token-spending)."""
    from datetime import datetime, timezone
    from data.regulatory import score_regulatory_actions
    from cache.db import set_meta
    n = score_regulatory_actions(limit=limit)
    set_meta("last_regulatory_score", datetime.now(timezone.utc).isoformat())
    return {"actions_scored": n}


# ── PHASE 7: FED H.8 BANK CREDIT MONITOR ─────────────────────

@router.get("/h8/metrics")
def get_h8_metrics():
    """Latest H.8 series with WoW, YoY, 4w moving average computed on the fly."""
    from data.h8 import compute_h8_metrics
    return compute_h8_metrics()


@router.get("/h8/credit-impulse")
def get_credit_impulse():
    """Quarterly credit impulse (QoQ change in bank loans / GDP, annualized %)."""
    from data.h8 import compute_credit_impulse
    return compute_credit_impulse()


# ── PHASE 7: CLO STRESS MONITOR ──────────────────────────────

@router.get("/clo/spread-proxy")
def get_clo_spread_proxy():
    """Daily JBBB/JAAA price-ratio as a CLO mezz-vs-AAA stress proxy."""
    from data.clo import compute_clo_spread_proxy
    return compute_clo_spread_proxy()


@router.get("/clo/filings")
def get_clo_filings(limit: int = Query(default=50, le=200)):
    """Recent CLO-tagged EDGAR filings (matched via asset_class_keywords)."""
    from data.clo import get_clo_edgar_filings
    return get_clo_edgar_filings(limit=limit)


# ── PHASE 7: KBRA PRESALE PARSER ─────────────────────────────

@router.get("/kbra/presales")
def get_kbra_presales(
    asset_class: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    """Parsed KBRA presale assumptions (CDR, CPR, loss, CE by tranche)."""
    from data.kbra import get_kbra_presales as _list
    return _list(asset_class=asset_class, limit=limit)


@router.post("/kbra/refresh")
def trigger_kbra_refresh():
    """Process any new PDFs in data/kbra/presales/ via Claude (token-spending)."""
    from datetime import datetime, timezone
    from data.kbra import process_kbra_presales
    from cache.db import set_meta
    n = process_kbra_presales()
    set_meta("last_kbra_refresh", datetime.now(timezone.utc).isoformat())
    return {"presales_processed": n}


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
        "last_abs_pricing_refresh": get_meta("last_abs_pricing_refresh"),
        "last_abs_424b5_refresh": get_meta("last_abs_424b5_refresh"),
        "last_bdc_refresh": get_meta("last_bdc_refresh"),
        "last_regulatory_refresh": get_meta("last_regulatory_refresh"),
        "last_regulatory_score": get_meta("last_regulatory_score"),
        "last_kbra_refresh": get_meta("last_kbra_refresh"),
    }
