"""Cover cache/db.py — schema migrations, idempotent inserts, query helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from cache import db


NOW = "2026-05-29T12:00:00+00:00"


def _article(id_="a1", title="t", url="https://x/1", **overrides):
    base = {
        "id": id_,
        "feed_name": "Bloomberg Markets",
        "feed_category": "macro",
        "title": title,
        "snippet": "snippet",
        "url": url,
        "published_at": "2026-05-28T10:00:00+00:00",
        "fetched_at": NOW,
    }
    base.update(overrides)
    return base


def _metric(series_id="DGS10", date="2026-05-28", value=4.5, **overrides):
    base = {
        "series_id": series_id,
        "label": "10Y Treasury",
        "category": "rates",
        "date": date,
        "value": value,
        "fetched_at": NOW,
    }
    base.update(overrides)
    return base


def _edgar(accession_no="0001234567-25-000001", **overrides):
    base = {
        "accession_no": accession_no,
        "company_name": "Carvana Auto Receivables Trust 2026-1",
        "form_type": "424B5",
        "filed_at": "2026-05-28",
        "description": "Pricing supplement",
        "url": "https://sec.gov/x",
        "asset_class": "auto loan",
        "issuance_type": "debt",
        "fetched_at": NOW,
    }
    base.update(overrides)
    return base


# ─── Schema / migrations ─────────────────────────────────────────────────────
def test_init_db_creates_all_tables(db_conn):
    names = {r[0] for r in db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    expected = {
        "articles", "metrics", "edgar_filings", "feed_health", "meta",
        "abs_pricing", "abs_new_issues", "digests",
        "bdc_holdings", "bdc_summary",
        "regulatory_actions", "kbra_presales",
    }
    assert expected.issubset(names)


def test_migrations_add_optional_columns(db_conn):
    """Columns added after the initial release must exist after init_db()."""
    art_cols = {r[1] for r in db_conn.execute("PRAGMA table_info(articles)")}
    assert {"is_read", "source_type"} <= art_cols

    fh_cols = {r[1] for r in db_conn.execute("PRAGMA table_info(feed_health)")}
    assert "platform" in fh_cols

    bdc_cols = {r[1] for r in db_conn.execute("PRAGMA table_info(bdc_summary)")}
    assert {"adsh", "filed"} <= bdc_cols

    ani_cols = {r[1] for r in db_conn.execute("PRAGMA table_info(abs_new_issues)")}
    assert "spread_source" in ani_cols

    edg_cols = {r[1] for r in db_conn.execute("PRAGMA table_info(edgar_filings)")}
    assert "issuance_type" in edg_cols


def test_init_db_is_idempotent(fresh_db):
    db.init_db()
    db.init_db()  # second call must not error or duplicate columns
    with db.get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
    # Each migrated column should appear exactly once (sqlite enforces this
    # anyway, but the test fails fast if _add_column_if_missing breaks).
    assert "is_read" in cols


# ─── Articles: dedup + relevance ─────────────────────────────────────────────
def test_upsert_article_dedupes_by_id(fresh_db):
    db.upsert_article(_article(id_="dup", title="original"))
    db.upsert_article(_article(id_="dup", title="REPLACED"))
    with db.get_conn() as conn:
        rows = conn.execute("SELECT title FROM articles WHERE id='dup'").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "original"  # INSERT OR IGNORE — first write wins


def test_upsert_article_defaults_source_type(fresh_db):
    db.upsert_article(_article())  # caller omits source_type
    with db.get_conn() as conn:
        row = conn.execute("SELECT source_type FROM articles").fetchone()
    assert row["source_type"] == "news"


def test_mark_article_read(fresh_db):
    db.upsert_article(_article(id_="r1"))
    assert db.mark_article_read("r1") == 1
    with db.get_conn() as conn:
        row = conn.execute("SELECT is_read FROM articles WHERE id='r1'").fetchone()
    assert row["is_read"] == 1
    # Marking a nonexistent id is a no-op.
    assert db.mark_article_read("nope") == 0


def test_update_article_relevance(fresh_db):
    db.upsert_article(_article(id_="s1"))
    db.update_article_relevance("s1", 5, json.dumps(["macro", "credit"]))
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT relevance_score, relevance_tags FROM articles WHERE id='s1'"
        ).fetchone()
    assert row["relevance_score"] == 5
    assert json.loads(row["relevance_tags"]) == ["macro", "credit"]


def test_get_unscored_articles_excludes_scored(fresh_db):
    db.upsert_article(_article(id_="u1"))
    db.upsert_article(_article(id_="u2"))
    db.update_article_relevance("u1", 4, "[]")
    out = db.get_unscored_articles(limit=10)
    ids = {r["id"] for r in out}
    assert ids == {"u2"}


# ─── Metrics ─────────────────────────────────────────────────────────────────
def test_upsert_metric_is_keyed_on_series_id_and_date(fresh_db):
    db.upsert_metric(_metric(date="2026-05-28", value=4.5))
    db.upsert_metric(_metric(date="2026-05-28", value=4.7))  # same key, new value
    db.upsert_metric(_metric(date="2026-05-29", value=4.6))
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics ORDER BY date"
        ).fetchall()
    assert [(r["date"], r["value"]) for r in rows] == [
        ("2026-05-28", 4.7),  # REPLACE semantics
        ("2026-05-29", 4.6),
    ]


# ─── EDGAR filings ───────────────────────────────────────────────────────────
def test_upsert_edgar_filing_ignores_duplicates(fresh_db):
    db.upsert_edgar_filing(_edgar(accession_no="x", company_name="A"))
    db.upsert_edgar_filing(_edgar(accession_no="x", company_name="B"))  # ignored
    with db.get_conn() as conn:
        rows = conn.execute("SELECT company_name FROM edgar_filings").fetchall()
    assert [r["company_name"] for r in rows] == ["A"]


def test_upsert_edgar_filing_defaults_issuance_type(fresh_db):
    payload = _edgar(accession_no="y")
    payload.pop("issuance_type")
    db.upsert_edgar_filing(payload)
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT issuance_type FROM edgar_filings WHERE accession_no='y'"
        ).fetchone()
    assert row["issuance_type"] is None


# ─── ABS pricing + new issues ────────────────────────────────────────────────
def test_upsert_abs_pricing_replaces_on_conflict(fresh_db):
    base = {
        "accession_no": "p1", "deal_name": "CARMX 2026-1", "issuer": "Carvana",
        "segment": "subprime_auto", "pricing_date": "2026-05-20",
        "class_name": "A-2", "rating": "AAA", "wal": 2.5, "benchmark": "I-CRV",
        "spread_bps": 80.0, "coupon": 5.10, "url": "https://x", "fetched_at": NOW,
    }
    db.upsert_abs_pricing_tranche(base)
    base["spread_bps"] = 85.0
    db.upsert_abs_pricing_tranche(base)
    with db.get_conn() as conn:
        row = conn.execute("SELECT spread_bps FROM abs_pricing").fetchone()
    assert row["spread_bps"] == 85.0


def test_upsert_abs_tranche_ignore_on_duplicate_id(fresh_db):
    row = {
        "id": "h1", "accession_no": "acc", "edgar_url": "u",
        "filing_date": "2026-05-20", "class_name": "A-2",
        "asset_class": "prime_auto_loan", "parse_confidence": "high",
        "fetched_at": NOW,
    }
    db.upsert_abs_tranche(row)
    row["class_name"] = "B"  # different tranche text but same PK id
    db.upsert_abs_tranche(row)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT class_name FROM abs_new_issues").fetchall()
    assert [r["class_name"] for r in rows] == ["A-2"]


def test_get_abs_new_issues_filters_by_confidence_and_class(fresh_db):
    base = {
        "accession_no": "x", "edgar_url": "u",
        "filing_date": datetime.now(timezone.utc).date().isoformat(),
        "fetched_at": NOW,
    }
    db.upsert_abs_tranche({**base, "id": "h-low", "class_name": "A",
                           "asset_class": "prime_auto_loan",
                           "parse_confidence": "low"})
    db.upsert_abs_tranche({**base, "id": "h-med", "class_name": "B",
                           "asset_class": "prime_auto_loan",
                           "parse_confidence": "medium"})
    db.upsert_abs_tranche({**base, "id": "h-high", "class_name": "C",
                           "asset_class": "credit_card",
                           "parse_confidence": "high"})

    # default min_confidence='medium' should drop the 'low' row.
    out = db.get_abs_new_issues()
    classes = {r["class_name"] for r in out}
    assert classes == {"B", "C"}

    out = db.get_abs_new_issues(min_confidence="high")
    classes = {r["class_name"] for r in out}
    assert classes == {"C"}

    out = db.get_abs_new_issues(asset_class="prime_auto_loan", min_confidence="low")
    classes = {r["class_name"] for r in out}
    assert classes == {"A", "B"}


# ─── Digests ────────────────────────────────────────────────────────────────
def _digest(date="2026-05-29", session="AM", **overrides):
    base = {
        "date": date, "session": session, "summary": "...",
        "article_count": 10, "hours_back": 24, "min_score": 4,
        "date_from": "2026-05-28T00:00:00+00:00",
        "date_to": "2026-05-29T00:00:00+00:00",
        "model": "claude-sonnet-4-6",
        "generated_at": NOW,
    }
    base.update(overrides)
    return base


def test_save_digest_replaces_same_date_session(fresh_db):
    db.save_digest(_digest(summary="v1"))
    db.save_digest(_digest(summary="v2"))
    db.save_digest(_digest(session="PM", summary="pm"))
    rows = db.get_digests()
    by_session = {r["session"]: r["summary"] for r in rows}
    assert by_session == {"AM": "v2", "PM": "pm"}


def test_get_digests_orders_by_generated_at_desc(fresh_db):
    db.save_digest(_digest(date="2026-05-27", generated_at="2026-05-27T12:00:00+00:00"))
    db.save_digest(_digest(date="2026-05-29", generated_at="2026-05-29T12:00:00+00:00"))
    rows = db.get_digests()
    assert [r["date"] for r in rows] == ["2026-05-29", "2026-05-27"]


# ─── Meta key/value ─────────────────────────────────────────────────────────
def test_meta_roundtrip(fresh_db):
    assert db.get_meta("k") is None
    db.set_meta("k", "v1")
    assert db.get_meta("k") == "v1"
    db.set_meta("k", "v2")  # upsert
    assert db.get_meta("k") == "v2"


# ─── Feed health ────────────────────────────────────────────────────────────
def test_upsert_feed_health_default_platform(fresh_db):
    payload = {
        "feed_name": "X", "url": "https://x", "last_checked": NOW,
        "is_live": 1, "http_status": 200, "error_msg": None, "needs_url": 0,
    }
    db.upsert_feed_health(payload)
    with db.get_conn() as conn:
        row = conn.execute("SELECT platform FROM feed_health").fetchone()
    assert row["platform"] is None


def test_upsert_feed_health_replaces(fresh_db):
    base = {
        "feed_name": "X", "url": "https://x", "last_checked": NOW,
        "is_live": 1, "http_status": 200, "error_msg": None, "needs_url": 0,
        "platform": "rss",
    }
    db.upsert_feed_health(base)
    base["is_live"] = 0
    base["http_status"] = 500
    db.upsert_feed_health(base)
    with db.get_conn() as conn:
        row = conn.execute("SELECT is_live, http_status FROM feed_health").fetchone()
    assert row["is_live"] == 0 and row["http_status"] == 500


# ─── Transaction safety ─────────────────────────────────────────────────────
def test_get_conn_rolls_back_on_error(fresh_db):
    """An exception inside the context manager must roll the write back."""
    with pytest.raises(RuntimeError):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('roll', 'will-vanish')"
            )
            raise RuntimeError("boom")
    assert db.get_meta("roll") is None
