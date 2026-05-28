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
from datetime import datetime
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
    asset_class     TEXT,   -- matched keyword
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
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO edgar_filings
              (accession_no, company_name, form_type, filed_at,
               description, url, asset_class, fetched_at)
            VALUES
              (:accession_no, :company_name, :form_type, :filed_at,
               :description, :url, :asset_class, :fetched_at)
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


# Initialize on import
init_db()
