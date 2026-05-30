"""
backend/cache/db.py — SQLite persistence layer.

Tables:
  articles   — deduplicated news/newsletter items with relevance scores
  metrics    — FRED + market time series snapshots
  edgar_filings — ABS-related SEC filings
  feed_health   — per-feed status from health checks
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from config import settings

DB_PATH = settings.DB_PATH
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id              TEXT PRIMARY KEY,        -- SHA256 of URL
    feed_name       TEXT NOT NULL,
    feed_category   TEXT NOT NULL,
    title           TEXT NOT NULL,
    snippet         TEXT,
    url             TEXT NOT NULL,
    published_at    TEXT,
    fetched_at      TEXT NOT NULL,
    relevance_score INTEGER DEFAULT NULL,    -- 1-5, set by classifier
    relevance_tags  TEXT DEFAULT NULL        -- JSON list of matched topics
);

CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_score   ON articles(relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_articles_cat     ON articles(feed_category);

CREATE TABLE IF NOT EXISTS metrics (
    series_id   TEXT NOT NULL,
    label       TEXT NOT NULL,
    category    TEXT NOT NULL,
    date        TEXT NOT NULL,
    value       REAL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
);

CREATE TABLE IF NOT EXISTS edgar_filings (
    accession_no    TEXT PRIMARY KEY,
    company_name    TEXT,
    form_type       TEXT,
    filed_at        TEXT,
    description     TEXT,
    url             TEXT,
    asset_class     TEXT,   -- name-derived asset class (mortgage / royalty / auto / etc.)
    issuance_type   TEXT,   -- 'debt' | 'equity' (debt for ABS forms + trust-style names)
    fetched_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_health (
    feed_name       TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    last_checked    TEXT,
    is_live         INTEGER DEFAULT 0,   -- 1=OK, 0=fail
    http_status     INTEGER,
    error_msg       TEXT,
    needs_url       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS abs_pricing (
    accession_no  TEXT NOT NULL,
    deal_name     TEXT,
    issuer        TEXT,
    segment       TEXT,           -- prime_auto | subprime_auto | equipment | other
    pricing_date  TEXT,           -- filing date of the pricing term sheet
    class_name    TEXT NOT NULL,  -- tranche, e.g. A-2, B, C, D
    rating        TEXT,
    wal           REAL,           -- weighted-average life (years)
    benchmark     TEXT,           -- e.g. I-CRV (interpolated swaps), SOFR
    spread_bps    REAL,           -- new-issue spread over benchmark, bps
    coupon        REAL,
    url           TEXT,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (accession_no, class_name)
);
CREATE INDEX IF NOT EXISTS idx_abs_pricing_date ON abs_pricing(pricing_date DESC);
CREATE INDEX IF NOT EXISTS idx_abs_pricing_seg  ON abs_pricing(segment);

-- 424B5 new-issue parser (richer schema than abs_pricing's FWP feed):
-- one row per tranche, with deal-level fields denormalized so the chart
-- queries don't need to join. Spread is computed against the maturity-matched
-- treasury when WAL is present; otherwise spread_to_benchmark stays null.
CREATE TABLE IF NOT EXISTS abs_new_issues (
    id                  TEXT PRIMARY KEY,        -- SHA256(accession_no + class_name)[:16]
    accession_no        TEXT NOT NULL,
    edgar_url           TEXT NOT NULL,
    filing_date         TEXT NOT NULL,

    issuer_name         TEXT,
    depositor           TEXT,
    servicer            TEXT,
    asset_class         TEXT,                    -- taxonomy bucket
    closing_date        TEXT,
    cutoff_date         TEXT,
    total_deal_size     REAL,
    underwriters        TEXT,                    -- JSON array
    parse_confidence    TEXT,                    -- 'high' | 'medium' | 'low'

    class_name          TEXT NOT NULL,
    principal_amount    REAL,
    coupon_type         TEXT,                    -- 'fixed' | 'floating'
    coupon_rate         REAL,
    floating_index      TEXT,                    -- 'SOFR' | 'LIBOR' | null
    floating_spread_bps REAL,
    wal_years           REAL,
    final_payment_date  TEXT,
    legal_maturity_date TEXT,
    price_to_public     REAL,
    cusip               TEXT,

    rating_sp           TEXT,
    rating_moodys       TEXT,
    rating_kbra         TEXT,
    rating_fitch        TEXT,

    benchmark           TEXT,                    -- 'UST2Y' | 'SOFR' | ...
    benchmark_rate      REAL,
    spread_to_benchmark REAL,                    -- bps
    spread_source       TEXT,                    -- 'parsed' | 'implied'
    implied_yield       REAL,

    fetched_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_abs_ni_filing_date ON abs_new_issues(filing_date DESC);
CREATE INDEX IF NOT EXISTS idx_abs_ni_asset_class ON abs_new_issues(asset_class);
CREATE INDEX IF NOT EXISTS idx_abs_ni_class_name  ON abs_new_issues(class_name);

CREATE TABLE IF NOT EXISTS digests (
    date          TEXT NOT NULL,      -- YYYY-MM-DD in US/Eastern
    session       TEXT NOT NULL,      -- 'AM' (before noon ET) or 'PM' (noon+)
    summary       TEXT NOT NULL,
    article_count INTEGER,
    hours_back    INTEGER,
    min_score     INTEGER,
    date_from     TEXT,
    date_to       TEXT,
    model         TEXT,
    generated_at  TEXT NOT NULL,
    PRIMARY KEY (date, session)
);

-- ── Phase 7: BDC Portfolio Monitor (SEC bulk dataset, XBRL SOI.tsv) ──
-- One row per portfolio holding across all BDC filings in the dataset.
CREATE TABLE IF NOT EXISTS bdc_holdings (
    id                  TEXT PRIMARY KEY,  -- SHA256(adsh + InvestmentIdentifier)[:16]
    adsh                TEXT NOT NULL,
    cik                 TEXT NOT NULL,
    bdc_name            TEXT NOT NULL,
    period              TEXT NOT NULL,     -- YYYYMMDD reporting period
    company_name        TEXT,
    industry            TEXT,
    investment_type     TEXT,              -- First Lien, Second Lien, Equity, ...
    interest_rate       REAL,
    pik_rate            REAL,
    cost_basis          REAL,
    fair_value          REAL,
    fair_value_pct_nav  REAL,
    maturity_date       TEXT,
    is_nonaccrual       INTEGER DEFAULT 0,
    fetched_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bdc_period      ON bdc_holdings(period DESC);
CREATE INDEX IF NOT EXISTS idx_bdc_cik         ON bdc_holdings(cik);
CREATE INDEX IF NOT EXISTS idx_bdc_nonaccrual  ON bdc_holdings(is_nonaccrual);

CREATE TABLE IF NOT EXISTS bdc_summary (
    id                   TEXT PRIMARY KEY,  -- SHA256(cik + period)[:16]
    cik                  TEXT NOT NULL,
    bdc_name             TEXT NOT NULL,
    period               TEXT NOT NULL,
    adsh                 TEXT,              -- source filing accession (most recent for this period)
    filed                TEXT,              -- source filing date
    total_fair_value     REAL,
    total_cost_basis     REAL,
    nonaccrual_fv        REAL,
    nonaccrual_cost      REAL,
    nonaccrual_rate_fv   REAL,             -- nonaccrual_fv / total_fv
    nonaccrual_rate_cost REAL,
    pct_first_lien       REAL,
    pct_second_lien      REAL,
    pct_equity           REAL,
    wa_interest_rate     REAL,
    mark_to_cost         REAL,             -- total_fv / total_cost (portfolio mark)
    n_holdings           INTEGER,
    fetched_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bdc_summary_period ON bdc_summary(period DESC);
CREATE INDEX IF NOT EXISTS idx_bdc_summary_cik    ON bdc_summary(cik);

-- ── Phase 7: Regulatory Flow Monitor ──
-- Federal Register API documents + agency RSS press releases.
-- relevance_score is Claude-set and ONLY filled when the manual SCORE button fires.
CREATE TABLE IF NOT EXISTS regulatory_actions (
    id                  TEXT PRIMARY KEY,  -- document_number or rss hash
    agency              TEXT NOT NULL,     -- CFPB|OCC|FDIC|Fed|SEC
    action_type         TEXT NOT NULL,     -- RULE|PROPOSED_RULE|NOTICE|PRESS_RELEASE
    title               TEXT NOT NULL,
    abstract            TEXT,
    publication_date    TEXT,
    effective_date      TEXT,
    comment_close_date  TEXT,
    document_number     TEXT,
    docket_id           TEXT,              -- JSON array
    cfr_references      TEXT,              -- JSON array
    html_url            TEXT,
    pdf_url             TEXT,
    relevance_score     INTEGER,           -- 1-5, manual Claude scoring only
    relevance_tags      TEXT,              -- JSON array
    fetched_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reg_pub_date ON regulatory_actions(publication_date DESC);
CREATE INDEX IF NOT EXISTS idx_reg_agency   ON regulatory_actions(agency);
CREATE INDEX IF NOT EXISTS idx_reg_type     ON regulatory_actions(action_type);
CREATE INDEX IF NOT EXISTS idx_reg_score    ON regulatory_actions(relevance_score DESC);

-- ── Phase 7: KBRA Presale Parser ──
-- Structured assumptions extracted from manually downloaded KBRA presale PDFs.
-- Claude extraction only fires when the manual PARSE button is pressed.
CREATE TABLE IF NOT EXISTS kbra_presales (
    id                  TEXT PRIMARY KEY,  -- SHA256(pdf_filename)[:16]
    deal_name           TEXT,
    issuer              TEXT,
    asset_class         TEXT,
    closing_date        TEXT,
    pdf_filename        TEXT,
    base_cdr            REAL,
    base_cpr            REAL,
    base_loss_rate      REAL,
    base_severity       REAL,
    ce_aaa              REAL,
    ce_aa               REAL,
    ce_a                REAL,
    ce_bbb              REAL,
    ce_bb               REAL,
    extracted_text      TEXT,
    parse_confidence    TEXT,              -- 'high' | 'medium' | 'low' | null
    parsed_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_kbra_asset_class ON kbra_presales(asset_class);
CREATE INDEX IF NOT EXISTS idx_kbra_parsed_at   ON kbra_presales(parsed_at DESC);

-- ── Watchlists ───────────────────────────────────────────────────────────────
-- Persisted saved searches across news + EDGAR + regulatory. Keyword match is
-- case-insensitive substring (OR across the keyword array). Filters narrow the
-- candidate set per source.
CREATE TABLE IF NOT EXISTS watchlists (
    id                  TEXT PRIMARY KEY,        -- 'wl_' + hex(8)
    name                TEXT NOT NULL,
    description         TEXT,
    keywords            TEXT NOT NULL,           -- JSON array of strings
    news_categories     TEXT,                    -- JSON array, optional
    edgar_asset_classes TEXT,                    -- JSON array, optional
    edgar_form_types    TEXT,                    -- JSON array, optional
    regulatory_agencies TEXT,                    -- JSON array, optional
    min_score           INTEGER DEFAULT 3,       -- news relevance threshold
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    last_viewed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_watchlists_updated ON watchlists(updated_at DESC);

-- ── Analyst briefings ────────────────────────────────────────────────────────
-- A briefing is a Claude-synthesized macro/credit narrative built from a
-- structured snapshot of recent indicators + digests + ABS/BDC/regulatory state.
-- The chat layer attached to a briefing is ephemeral (per-session, client-held)
-- so we only persist the briefing itself + the snapshot it was built on (for
-- audit / regenerate-from-snapshot).
CREATE TABLE IF NOT EXISTS briefings (
    id              TEXT PRIMARY KEY,        -- 'brf_' + hex(8)
    period_label    TEXT NOT NULL,           -- e.g. '2026-05' (snapshot window)
    generated_at    TEXT NOT NULL,           -- ISO UTC
    model           TEXT NOT NULL,
    briefing_md     TEXT NOT NULL,           -- the narrative
    watch_items     TEXT,                    -- JSON: [{title, severity, why}]
    snapshot_json   TEXT NOT NULL,           -- the structured inputs fed to Claude
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cache_read_tokens   INTEGER,
    cache_write_tokens  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_briefings_generated ON briefings(generated_at DESC);

-- ── Scheduler job observability ──────────────────────────────────────────────
-- One row per scheduled-job invocation. Lets the dashboard answer "did the
-- BDC pull actually run today, and did it succeed?" without tailing logs.
CREATE TABLE IF NOT EXISTS job_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,          -- 'feeds' | 'market' | 'fred' | 'edgar' | ...
    started_at      TEXT NOT NULL,          -- ISO UTC
    ended_at        TEXT,                   -- ISO UTC; null while running
    status          TEXT NOT NULL,          -- 'running' | 'success' | 'error'
    duration_ms     INTEGER,
    rows_ingested   INTEGER,                -- whatever the job decides is "work done"
    error           TEXT,                   -- truncated repr of the exception
    triggered_by    TEXT NOT NULL DEFAULT 'scheduler'  -- 'scheduler' | 'manual'
);
CREATE INDEX IF NOT EXISTS idx_job_runs_job_started
    ON job_runs(job_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_runs_started
    ON job_runs(started_at DESC);
"""


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column_if_missing(conn, table: str, column: str, decl: str) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate(conn) -> None:
    """Additive, idempotent migrations for columns added after initial release."""
    _add_column_if_missing(conn, "articles", "is_read", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "articles", "source_type", "TEXT DEFAULT 'news'")
    _add_column_if_missing(conn, "feed_health", "platform", "TEXT")
    _add_column_if_missing(conn, "bdc_summary", "adsh", "TEXT")
    _add_column_if_missing(conn, "bdc_summary", "filed", "TEXT")
    _add_column_if_missing(conn, "abs_new_issues", "spread_source", "TEXT")
    _add_column_if_missing(conn, "edgar_filings", "issuance_type", "TEXT")

    # digests: migrate the original (date PRIMARY KEY, one/day) schema to the
    # (date, session) composite — two slots per day (AM/PM, US/Eastern).
    dcols = {r[1] for r in conn.execute("PRAGMA table_info(digests)")}
    if dcols and "session" not in dcols:
        conn.execute("ALTER TABLE digests RENAME TO digests_old")
        conn.executescript(SCHEMA)  # recreate digests with the new schema
        # Carry old rows forward, tagging them PM (they predate the AM/PM split).
        conn.execute(
            """
            INSERT OR IGNORE INTO digests
              (date, session, summary, article_count, hours_back, min_score,
               date_from, date_to, model, generated_at)
            SELECT date, 'PM', summary, article_count, hours_back, min_score,
                   date_from, date_to, model, generated_at
            FROM digests_old
            """
        )
        conn.execute("DROP TABLE digests_old")


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def upsert_article(row: dict) -> None:
    row = {"source_type": "news", **row}  # default if caller omits it
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO articles
              (id, feed_name, feed_category, title, snippet, url,
               published_at, fetched_at, source_type)
            VALUES
              (:id, :feed_name, :feed_category, :title, :snippet, :url,
               :published_at, :fetched_at, :source_type)
            """,
            row,
        )


def set_meta(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )


def get_meta(key: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def save_digest(row: dict) -> None:
    """Upsert a digest keyed by date — re-generating the same day overwrites it."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO digests
              (date, session, summary, article_count, hours_back, min_score,
               date_from, date_to, model, generated_at)
            VALUES
              (:date, :session, :summary, :article_count, :hours_back, :min_score,
               :date_from, :date_to, :model, :generated_at)
            """,
            row,
        )


def get_digests(limit: int = 60) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM digests ORDER BY generated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_article_read(article_id: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE articles SET is_read = 1 WHERE id = ?", (article_id,)
        )
        return cur.rowcount


def update_article_relevance(article_id: str, score: int, tags: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET relevance_score=?, relevance_tags=? WHERE id=?",
            (score, tags, article_id),
        )


def get_unscored_articles(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, snippet FROM articles
               WHERE relevance_score IS NULL
               ORDER BY fetched_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_metric(row: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO metrics
              (series_id, label, category, date, value, fetched_at)
            VALUES
              (:series_id, :label, :category, :date, :value, :fetched_at)
            """,
            row,
        )


def upsert_edgar_filing(row: dict) -> None:
    # Callers that don't compute issuance_type yet (older code paths) get None.
    row = {"issuance_type": None, **row}
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO edgar_filings
              (accession_no, company_name, form_type, filed_at,
               description, url, asset_class, issuance_type, fetched_at)
            VALUES
              (:accession_no, :company_name, :form_type, :filed_at,
               :description, :url, :asset_class, :issuance_type, :fetched_at)
            """,
            row,
        )


def upsert_abs_pricing_tranche(row: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO abs_pricing
              (accession_no, deal_name, issuer, segment, pricing_date, class_name,
               rating, wal, benchmark, spread_bps, coupon, url, fetched_at)
            VALUES
              (:accession_no, :deal_name, :issuer, :segment, :pricing_date, :class_name,
               :rating, :wal, :benchmark, :spread_bps, :coupon, :url, :fetched_at)
            """,
            row,
        )


def upsert_abs_tranche(row: dict) -> None:
    """Insert one tranche row from a 424B5 parse. Idempotent on `id` PK."""
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row.keys())
    with get_conn() as conn:
        conn.execute(
            f"INSERT OR IGNORE INTO abs_new_issues ({cols}) VALUES ({placeholders})",
            row,
        )


def get_abs_new_issues(
    asset_class: Optional[str] = None,
    days_back: int = 365,
    min_confidence: str = "medium",
    limit: int = 500,
) -> list[dict]:
    """Return 424B5 tranche records filtered by asset class and confidence."""
    from datetime import datetime, timedelta

    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    conf_order = {"low": 0, "medium": 1, "high": 2}
    min_conf_val = conf_order.get(min_confidence, 1)

    sql = """
        SELECT * FROM abs_new_issues
        WHERE filing_date >= ?
          AND CASE parse_confidence
              WHEN 'high'   THEN 2
              WHEN 'medium' THEN 1
              ELSE 0
          END >= ?
    """
    params: list = [since, min_conf_val]
    if asset_class:
        sql += " AND asset_class = ?"
        params.append(asset_class)
    sql += " ORDER BY filing_date DESC LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def upsert_feed_health(row: dict) -> None:
    row = {"platform": None, **row}  # default if caller omits it
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO feed_health
              (feed_name, url, last_checked, is_live, http_status, error_msg, needs_url, platform)
            VALUES
              (:feed_name, :url, :last_checked, :is_live, :http_status, :error_msg, :needs_url, :platform)
            """,
            row,
        )


# ── Analyst briefings ────────────────────────────────────────────────────────

def insert_briefing(row: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO briefings
              (id, period_label, generated_at, model, briefing_md, watch_items,
               snapshot_json, input_tokens, output_tokens,
               cache_read_tokens, cache_write_tokens)
            VALUES
              (:id, :period_label, :generated_at, :model, :briefing_md, :watch_items,
               :snapshot_json, :input_tokens, :output_tokens,
               :cache_read_tokens, :cache_write_tokens)
            """,
            {"watch_items": None, "input_tokens": None, "output_tokens": None,
             "cache_read_tokens": None, "cache_write_tokens": None, **row},
        )


def list_briefings(limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, period_label, generated_at, model, "
            "substr(briefing_md, 1, 200) AS preview, "
            "input_tokens, output_tokens "
            "FROM briefings ORDER BY generated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_briefing(briefing_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM briefings WHERE id = ?", (briefing_id,)
        ).fetchone()
    return dict(row) if row else None


def get_latest_briefing() -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM briefings ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


# ── Scheduler job observability ──────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def start_job_run(job_id: str, triggered_by: str = "scheduler") -> int:
    """Record the start of a scheduled job. Returns the row id to pass to finish_job_run."""
    started = _utcnow().isoformat() + "Z"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO job_runs (job_id, started_at, status, triggered_by) "
            "VALUES (?, ?, 'running', ?)",
            (job_id, started, triggered_by),
        )
        return int(cur.lastrowid)


def finish_job_run(
    run_id: int,
    status: str,
    rows_ingested: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    """Mark a job run finished. status: 'success' | 'error'."""
    ended_dt = _utcnow()
    ended = ended_dt.isoformat() + "Z"
    with get_conn() as conn:
        started_row = conn.execute(
            "SELECT started_at FROM job_runs WHERE id = ?", (run_id,)
        ).fetchone()
        duration_ms = None
        if started_row:
            try:
                started_dt = datetime.fromisoformat(
                    started_row["started_at"].rstrip("Z")
                )
                duration_ms = int((ended_dt - started_dt).total_seconds() * 1000)
            except ValueError:
                pass
        # Truncate gnarly tracebacks so the DB doesn't bloat with stack noise.
        err = (error[:500] if error else None)
        conn.execute(
            "UPDATE job_runs SET ended_at = ?, status = ?, duration_ms = ?, "
            "rows_ingested = ?, error = ? WHERE id = ?",
            (ended, status, duration_ms, rows_ingested, err, run_id),
        )


def get_latest_job_runs() -> list[dict]:
    """One row per job_id: the most recent invocation. Used for /api/jobs/status."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT j.* FROM job_runs j
            JOIN (
                SELECT job_id, MAX(started_at) AS mx
                FROM job_runs
                GROUP BY job_id
            ) m ON j.job_id = m.job_id AND j.started_at = m.mx
            ORDER BY j.job_id
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_job_run_history(job_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Recent invocations across (optionally filtered by) job_id."""
    sql = "SELECT * FROM job_runs"
    params: list = []
    if job_id:
        sql += " WHERE job_id = ?"
        params.append(job_id)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# Initialize on import
init_db()
