"""
backend/api/routes.py — REST endpoints for the dashboard frontend.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from data.dates import utc_days_ago_str

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
    include_duplicates: bool = Query(
        default=False,
        description="When false (default), exclude articles tagged as duplicates "
                    "of another, and return n_sources / other_sources for the "
                    "primary version.",
    ),
):
    """
    Return scored articles filtered by relevance and optional category/source.
    Default: score >= 4 (macro/credit/finance relevant); duplicates hidden.
    """
    from cache.db import get_conn
    from data.article_dedup import annotate_with_sources

    with get_conn() as conn:
        base = """
            SELECT id, feed_name, feed_category, title, snippet, url,
                   published_at, fetched_at, relevance_score, relevance_tags,
                   is_read, source_type, cluster_id, duplicate_of
            FROM articles
            WHERE relevance_score >= ?
        """
        params: list = [min_score]

        if not include_duplicates:
            base += " AND duplicate_of IS NULL"

        if category:
            cats = [c.strip() for c in category.split(",") if c.strip()]
            if cats:
                placeholders = ",".join("?" * len(cats))
                base += f" AND feed_category IN ({placeholders})"
                params.extend(cats)

        if source_type:
            slugs = [s.strip() for s in source_type.split(",") if s.strip()]
            if slugs:
                placeholders = ",".join("?" * len(slugs))
                base += f" AND source_type IN ({placeholders})"
                params.extend(slugs)

        base += " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        rows = [dict(r) for r in conn.execute(base, params).fetchall()]

    if not include_duplicates:
        rows = annotate_with_sources(rows)
    return {"items": rows, "offset": offset, "limit": limit}


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


@router.post("/market/refresh")
def trigger_market_refresh():
    """Manually pull market data (token-free yfinance fetch)."""
    from datetime import datetime, timezone
    from cache.db import set_meta
    from data.market import fetch_market_data
    n = fetch_market_data()
    set_meta("last_market_refresh", datetime.now(timezone.utc).isoformat())
    return {"rows": n}


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


@router.post("/fred/refresh")
def trigger_fred_refresh():
    """Manually pull FRED series + recompute derived indicators (probits, EBP, ...)."""
    from datetime import datetime, timezone
    from cache.db import set_meta
    from data.fred import fetch_fred_series
    from data.indicators import compute_all_indicators
    n = fetch_fred_series()
    m = compute_all_indicators()
    set_meta("last_fred_refresh", datetime.now(timezone.utc).isoformat())
    return {"fred_rows": n, "indicator_rows": m}


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
    issuance_type: Optional[str] = Query(default=None, pattern="^(debt|equity)$"),
):
    """Recent ABS-related EDGAR filings, newest first, with optional filters."""
    from data.edgar import get_recent_filings
    return get_recent_filings(
        limit=limit, offset=offset, form_type=form_type,
        asset_class=asset_class, issuance_type=issuance_type,
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


# ── MASTER-TRUST MONTHLY PERFORMANCE (10-D) ──────────────────────────────────

@router.get("/trust-performance")
def get_trust_performance_endpoint(
    metric: Optional[str] = None,
    trust: Optional[str] = None,
    limit: int = Query(default=500, le=5000),
):
    """Monthly master-trust metrics (delinquency / charge-off / payment rate),
    oldest first — one row per (trust, period, metric)."""
    from data.trust_performance import get_trust_performance
    return get_trust_performance(metric=metric, trust=trust, limit=limit)


@router.get("/trust-performance/latest")
def get_trust_performance_latest_endpoint():
    """Latest reported period per trust, metrics pivoted into one row."""
    from data.trust_performance import get_trust_performance_latest
    return get_trust_performance_latest()


@router.post("/trust-performance/refresh")
def trigger_trust_performance_refresh(days_back: int = Query(default=35, le=365)):
    """Discover and parse recent 10-D distribution reports."""
    from datetime import datetime, timezone
    from data.trust_performance import fetch_trust_performance
    from cache.db import set_meta

    n = fetch_trust_performance(days_back=days_back)
    set_meta("last_trust_performance_refresh", datetime.now(timezone.utc).isoformat())
    return {"rows": n}


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


# The "all" pseudo-bucket bypasses the rating filter entirely. Useful because
# 424B5 prospectuses rarely disclose ratings inline — final ratings typically
# land in the FWP or rating-agency reports — so requiring AAA matching often
# yields zero observations even when we have tranche data.
@router.get("/abs/spread-series")
def get_abs_spread_series(
    asset_class: str = Query(...),
    rating_bucket: str = Query(
        default="AAA", pattern="^(all|AAA|AA|A|BBB|BB_and_below)$",
    ),
    metric: str = Query(
        default="spread_to_benchmark",
        pattern="^(spread_to_benchmark|implied_yield|floating_spread_bps|coupon_rate)$",
    ),
    days_back: int = Query(default=365, le=3650),
):
    """Weekly-binned avg/min/max of `metric` for one asset class + rating bucket.

    Bucket → agency-label mapping and the query live in
    cache.db.get_abs_spread_series (shared with the analyst spread tool);
    unknown buckets/metrics are rejected by the Query patterns above.

    `percentile` ranks the latest weekly average against a trailing 2-year
    context window (or the requested window if longer): rank = share of weeks
    at or below the latest value. Null until 8 weekly observations exist.
    """
    from cache.db import get_abs_spread_series as _query

    series = _query(asset_class, rating_bucket, metric, days_back)

    context_days = max(days_back, 730)
    context = (
        series if context_days == days_back
        else _query(asset_class, rating_bucket, metric, context_days)
    )
    percentile = None
    if series and len(context) >= 8:
        latest = series[-1]["avg_spread"]
        values = [r["avg_spread"] for r in context]
        percentile = {
            "latest": latest,
            "rank": round(100 * sum(1 for v in values if v <= latest) / len(values)),
            "window_days": context_days,
            "n_weeks": len(values),
        }

    return {
        "asset_class": asset_class,
        "rating_bucket": rating_bucket,
        "metric": metric,
        "series": series,
        "percentile": percentile,
    }


@router.get("/abs/deal-summary")
def get_abs_deal_summary(days_back: int = Query(default=90, le=3650)):
    """Per asset class: deal count, total volume, avg spread. Trailing window."""
    from cache.db import get_conn

    since = utc_days_ago_str(days_back)
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


@router.get("/bdc/latest-per-bdc")
def get_bdc_latest_per_bdc():
    """Per-BDC roll-up, one row per BDC at each BDC's freshest available period.
    Includes filing_url for the source EDGAR filing."""
    from data.bdc import get_bdc_summary_latest_per_bdc as _latest
    return _latest()


@router.get("/bdc/aggregate-trend")
def get_bdc_aggregate_trend():
    """Industry-wide per-period aggregates: NAV total, WA rate, portfolio mix."""
    from data.bdc import get_bdc_aggregate_trend as _agg
    return _agg()


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
    """System health: DB row counts, last fetch times, last job-run summary."""
    from cache.db import get_conn, get_latest_job_runs, get_meta

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
        "last_market_refresh": get_meta("last_market_refresh"),
        "last_fred_refresh": get_meta("last_fred_refresh"),
        "last_abs_pricing_refresh": get_meta("last_abs_pricing_refresh"),
        "last_abs_424b5_refresh": get_meta("last_abs_424b5_refresh"),
        "last_bdc_refresh": get_meta("last_bdc_refresh"),
        "last_regulatory_refresh": get_meta("last_regulatory_refresh"),
        "last_regulatory_score": get_meta("last_regulatory_score"),
        "last_kbra_refresh": get_meta("last_kbra_refresh"),
        "jobs": get_latest_job_runs(),
    }


# ── ANALYST BRIEFINGS ────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class BriefingChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[ChatMessage] = Field(default_factory=list)


def _briefing_to_api(b: dict, include_snapshot: bool = True) -> dict:
    """Shape a DB row for the API: parse JSON fields, drop noise."""
    import json as _json
    out = {
        "id": b["id"],
        "period_label": b["period_label"],
        "generated_at": b["generated_at"],
        "model": b["model"],
        "briefing_md": b["briefing_md"],
        "watch_items": _json.loads(b["watch_items"]) if b.get("watch_items") else None,
        "usage": {
            "input_tokens": b.get("input_tokens"),
            "output_tokens": b.get("output_tokens"),
            "cache_read_input_tokens": b.get("cache_read_tokens"),
            "cache_creation_input_tokens": b.get("cache_write_tokens"),
        },
    }
    if include_snapshot and b.get("snapshot_json"):
        out["snapshot"] = _json.loads(b["snapshot_json"])
    return out


@router.get("/briefings")
def list_briefings(limit: int = Query(default=30, le=200)):
    """List past briefings (newest first), with a short preview."""
    return {"items": db_list_briefings(limit=limit)}


@router.get("/briefings/latest")
def get_latest_briefing():
    """Most recent briefing, including the full snapshot it was built on."""
    from cache.db import get_latest_briefing as _latest
    row = _latest()
    if row is None:
        raise HTTPException(status_code=404, detail="No briefings yet — generate one first.")
    return _briefing_to_api(row)


@router.get("/briefings/{briefing_id}")
def get_briefing(briefing_id: str):
    from cache.db import get_briefing as _get
    row = _get(briefing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="briefing not found")
    return _briefing_to_api(row)


@router.post("/briefings/generate")
def trigger_briefing_generate(period_label: Optional[str] = Query(default=None)):
    """Build a new briefing (synchronous; uses Claude Opus 4.7 tokens)."""
    from data.analyst import BriefingError, generate_briefing
    try:
        briefing = generate_briefing(period_label=period_label)
    except BriefingError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Briefing generation failed: {e}")
    return _briefing_to_api(briefing)


@router.post("/briefings/{briefing_id}/chat")
def chat_with_briefing(briefing_id: str, body: BriefingChatRequest):
    """One chat turn against a briefing. History is client-held — pass the full
    transcript (excluding this turn's user message) every time."""
    from data.analyst import ChatError, chat_with_briefing as _chat
    try:
        result = _chat(
            briefing_id=briefing_id,
            history=[m.model_dump() for m in body.history],
            user_message=body.message,
        )
    except ChatError as e:
        # 404 if the briefing is missing; 422 for everything else (no key, etc.)
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Chat failed: {e}")
    return result


# Local import to keep `from cache.db import list_briefings as ...` out of the
# module top-level (routes.py already lazy-imports many helpers in route bodies).
def db_list_briefings(limit: int = 30) -> list[dict]:
    from cache.db import list_briefings as _list
    return _list(limit=limit)


# ── ABS ISSUANCE (SIFMA) ─────────────────────────────────────

@router.get("/abs/issuance")
def get_abs_issuance():
    """Monthly ABS issuance series by asset class (SIFMA) + FRED supplement."""
    from data.sifma import get_issuance_series
    return get_issuance_series()


@router.post("/abs/issuance/refresh")
def trigger_abs_issuance_refresh():
    """Scan the SIFMA drop folder for new xlsx files; ingest + archive."""
    from data.sifma import fetch_fred_abs_issuance, ingest_sifma_drops
    result = ingest_sifma_drops()
    result["fred_supplement_rows"] = fetch_fred_abs_issuance()
    return result


# ── ISSUER / DEAL PIVOT ──────────────────────────────────────

@router.get("/issuers")
def list_issuers_route(limit: int = Query(default=100, ge=1, le=500)):
    """Distinct issuer_name values seen in abs_new_issues, ordered by recency."""
    from data.issuers import list_issuers
    return {"items": list_issuers(limit=limit)}


@router.get("/issuers/summary")
def get_issuer_summary_route(
    q: str = Query(..., min_length=1, description="Issuer/deal substring query"),
    deals_limit: int = Query(default=200, ge=1, le=1000),
    edgar_limit: int = Query(default=100, ge=1, le=500),
    article_limit: int = Query(default=50, ge=1, le=200),
    article_min_score: int = Query(default=3, ge=1, le=5),
    article_days_back: int = Query(default=180, ge=1, le=3650),
):
    """Cross-table substring search returning everything we know about the query."""
    from data.issuers import get_issuer_summary
    result = get_issuer_summary(
        query=q,
        deals_limit=deals_limit,
        edgar_limit=edgar_limit,
        article_limit=article_limit,
        article_min_score=article_min_score,
        article_days_back=article_days_back,
    )
    if result is None:
        raise HTTPException(status_code=422, detail="query is required")
    return result


# ── WATCHLISTS ───────────────────────────────────────────────

class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = None
    keywords: list[str] = Field(min_length=1)
    news_categories: Optional[list[str]] = None
    edgar_asset_classes: Optional[list[str]] = None
    edgar_form_types: Optional[list[str]] = None
    regulatory_agencies: Optional[list[str]] = None
    min_score: int = Field(default=3, ge=1, le=5)


class WatchlistUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    keywords: Optional[list[str]] = Field(default=None, min_length=1)
    news_categories: Optional[list[str]] = None
    edgar_asset_classes: Optional[list[str]] = None
    edgar_form_types: Optional[list[str]] = None
    regulatory_agencies: Optional[list[str]] = None
    min_score: Optional[int] = Field(default=None, ge=1, le=5)


@router.get("/watchlists")
def list_watchlists_route():
    from data.watchlists import list_watchlists
    return {"items": list_watchlists()}


@router.post("/watchlists")
def create_watchlist_route(body: WatchlistCreate):
    from data.watchlists import create_watchlist
    try:
        return create_watchlist(body.model_dump(exclude_unset=False))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/watchlists/{watchlist_id}")
def get_watchlist_route(watchlist_id: str):
    from data.watchlists import get_watchlist
    row = get_watchlist(watchlist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="watchlist not found")
    return row


@router.patch("/watchlists/{watchlist_id}")
def update_watchlist_route(watchlist_id: str, body: WatchlistUpdate):
    from data.watchlists import update_watchlist
    try:
        row = update_watchlist(watchlist_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="watchlist not found")
    return row


@router.delete("/watchlists/{watchlist_id}")
def delete_watchlist_route(watchlist_id: str):
    from data.watchlists import delete_watchlist
    ok = delete_watchlist(watchlist_id)
    if not ok:
        raise HTTPException(status_code=404, detail="watchlist not found")
    return {"deleted": watchlist_id}


@router.get("/watchlists/{watchlist_id}/results")
def get_watchlist_results(
    watchlist_id: str,
    per_source_limit: int = Query(default=100, ge=1, le=500),
):
    from data.watchlists import run_watchlist
    result = run_watchlist(watchlist_id, per_source_limit=per_source_limit)
    if result is None:
        raise HTTPException(status_code=404, detail="watchlist not found")
    return result


@router.post("/watchlists/{watchlist_id}/viewed")
def mark_watchlist_viewed(watchlist_id: str):
    from data.watchlists import mark_viewed
    row = mark_viewed(watchlist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="watchlist not found")
    return row


# ── DB BACKUPS ───────────────────────────────────────────────

@router.get("/backups")
def list_db_backups():
    """All SQLite snapshots currently on disk, newest first."""
    from data.backups import list_backups
    return {"backups": list_backups()}


@router.post("/backups/run")
def trigger_db_backup():
    """Take a snapshot of monitor.db now, prune the retention window."""
    from data.backups import backup_database
    return backup_database()


# ── PERCENTILE / REGIME CONTEXT ──────────────────────────────

@router.get("/percentiles")
def get_percentiles(
    series_ids: str = Query(..., description="Comma-separated series ids."),
    window_days: int = Query(default=1825, ge=30, le=20000),
):
    """Trailing-window percentile + min/max/median for one or more series."""
    from data.percentiles import compute_percentiles
    ids = [s.strip() for s in series_ids.split(",") if s.strip()]
    return {"window_days": window_days, "series": compute_percentiles(ids, window_days=window_days)}


# ── DATA FRESHNESS ───────────────────────────────────────────

@router.get("/freshness")
def get_freshness():
    """Per-series staleness assessment + summary counts + last-run-per-job."""
    from data.freshness import freshness_report
    return freshness_report()


@router.get("/jobs/status")
def jobs_status():
    """Latest run per scheduled job: status, duration, rows ingested, last error."""
    from cache.db import get_latest_job_runs
    return {"jobs": get_latest_job_runs()}


@router.get("/jobs/history")
def jobs_history(
    job_id: Optional[str] = None,
    limit: int = Query(default=50, le=500),
):
    """Recent job-run history, optionally filtered to one job_id."""
    from cache.db import get_job_run_history
    return {"runs": get_job_run_history(job_id=job_id, limit=limit)}
