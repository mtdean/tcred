"""
Characterization tests for data/analyst_tools.py — the read-only tools the
analyst chat can call.

These lock in CURRENT behavior ahead of a date-helper / query-skeleton
refactor. All tools are exercised directly (no Anthropic round-trip); the DB
is seeded via cache.db helpers on the fresh_db fixture.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from cache import db
from data import analyst_tools as at


NOW = datetime.now(timezone.utc).isoformat()


def _days_ago_date(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _days_ago_iso(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _seed_metric(series_id, date, value, label="lbl", category="rates"):
    db.upsert_metric({
        "series_id": series_id, "label": label, "category": category,
        "date": date, "value": value, "fetched_at": NOW,
    })


def _seed_tranche(id_, asset_class="prime_auto_loan", confidence="high",
                  filing_date=None, **overrides):
    row = {
        "id": id_, "accession_no": id_, "edgar_url": "u",
        "filing_date": filing_date or _days_ago_date(10),
        "asset_class": asset_class, "parse_confidence": confidence,
        "class_name": "A-2", "fetched_at": NOW,
    }
    row.update(overrides)
    db.upsert_abs_tranche(row)


def _seed_filing(accession, filed_at=None, asset_class="auto loan",
                 form_type="424B5"):
    db.upsert_edgar_filing({
        "accession_no": accession, "company_name": "Trust",
        "form_type": form_type, "filed_at": filed_at or _days_ago_date(5),
        "description": "d", "url": "https://x", "asset_class": asset_class,
        "issuance_type": "debt", "fetched_at": NOW,
    })


def _seed_article(id_, score=5, category="macro", published_days_ago=2):
    db.upsert_article({
        "id": id_, "feed_name": "Bloomberg", "feed_category": category,
        "title": f"title-{id_}", "snippet": "s", "url": f"https://x/{id_}",
        "published_at": _days_ago_iso(published_days_ago), "fetched_at": NOW,
        "source_type": "news",
    })
    if score is not None:
        db.update_article_relevance(id_, score, json.dumps([]))


# ─── _tool_get_indicator_history ─────────────────────────────────────────────
class TestGetIndicatorHistory:
    def test_unknown_series_returns_empty_shape(self, fresh_db):
        # Empty result short-circuits BEFORE label/category are added.
        out = at._tool_get_indicator_history("NOPE")
        assert out == {"series_id": "NOPE", "n": 0, "observations": []}

    def test_returns_ascending_observations_with_metadata(self, fresh_db):
        _seed_metric("DGS10", _days_ago_date(3), 4.0, label="10Y", category="rates")
        _seed_metric("DGS10", _days_ago_date(1), 4.2, label="10Y", category="rates")
        _seed_metric("DGS2", _days_ago_date(1), 3.9)  # other series excluded

        out = at._tool_get_indicator_history("DGS10")
        assert out["series_id"] == "DGS10"
        assert out["label"] == "10Y"
        assert out["category"] == "rates"
        assert out["n"] == 2
        assert out["first_date"] == _days_ago_date(3)
        assert out["last_date"] == _days_ago_date(1)
        assert out["observations"] == [
            {"date": _days_ago_date(3), "value": 4.0},
            {"date": _days_ago_date(1), "value": 4.2},
        ]

    def test_days_back_cutoff_excludes_old_rows(self, fresh_db):
        _seed_metric("DGS10", _days_ago_date(100), 3.5)
        _seed_metric("DGS10", _days_ago_date(5), 4.2)
        out = at._tool_get_indicator_history("DGS10", days_back=30)
        assert out["n"] == 1
        assert out["observations"][0]["value"] == 4.2


# ─── _tool_get_abs_spread_series ─────────────────────────────────────────────
class TestGetAbsSpreadSeries:
    def test_invalid_metric_returns_error_dict(self, fresh_db):
        out = at._tool_get_abs_spread_series("prime_auto_loan", metric="price")
        assert out == {
            "error": "metric must be one of spread_to_benchmark/implied_yield/"
                     "floating_spread_bps/coupon_rate; got 'price'"
        }

    def test_invalid_rating_bucket_returns_error_dict(self, fresh_db):
        out = at._tool_get_abs_spread_series("prime_auto_loan", rating_bucket="junk")
        assert out == {
            "error": "rating_bucket must be one of all/AAA/AA/A/BBB/BB_and_below; "
                     "got 'junk'"
        }

    def test_rating_bucket_matches_any_agency_label(self, fresh_db):
        d = _days_ago_date(10)
        # Matches AAA via S&P label.
        _seed_tranche("t1", filing_date=d, rating_sp="AAA",
                      spread_to_benchmark=50.0)
        # Matches AAA via Moody's label only.
        _seed_tranche("t2", filing_date=d, rating_moodys="Aaa",
                      spread_to_benchmark=70.0)
        # BBB-rated → excluded from the AAA bucket.
        _seed_tranche("t3", filing_date=d, rating_sp="BBB",
                      spread_to_benchmark=200.0)
        # Unrated → excluded from any named bucket.
        _seed_tranche("t4", filing_date=d, spread_to_benchmark=300.0)

        out = at._tool_get_abs_spread_series("prime_auto_loan", rating_bucket="AAA")
        assert out["asset_class"] == "prime_auto_loan"
        assert out["rating_bucket"] == "AAA"
        assert out["metric"] == "spread_to_benchmark"
        assert out["n_weeks"] == 1
        wk = out["series"][0]
        assert wk["n_tranches"] == 2
        assert wk["avg_spread"] == 60.0  # mean of 50 and 70
        assert wk["min_spread"] == 50.0
        assert wk["max_spread"] == 70.0
        assert wk["week_start"] == d

    def test_all_bucket_includes_unrated_but_not_low_confidence(self, fresh_db):
        d = _days_ago_date(10)
        _seed_tranche("t1", filing_date=d, spread_to_benchmark=120.0)
        _seed_tranche("t2", filing_date=d, confidence="low",
                      spread_to_benchmark=999.0)
        _seed_tranche("t3", filing_date=d, asset_class="credit_card",
                      spread_to_benchmark=80.0)

        out = at._tool_get_abs_spread_series("prime_auto_loan", rating_bucket="all")
        assert out["n_weeks"] == 1
        assert out["series"][0]["n_tranches"] == 1
        assert out["series"][0]["avg_spread"] == 120.0

    def test_alternate_metric_filters_nulls_on_that_metric(self, fresh_db):
        d = _days_ago_date(10)
        _seed_tranche("t1", filing_date=d, coupon_rate=5.25)
        _seed_tranche("t2", filing_date=d, spread_to_benchmark=60.0)  # no coupon
        out = at._tool_get_abs_spread_series(
            "prime_auto_loan", rating_bucket="all", metric="coupon_rate"
        )
        assert out["metric"] == "coupon_rate"
        assert out["series"][0]["n_tranches"] == 1
        assert out["series"][0]["avg_spread"] == 5.25


# ─── _tool_get_bdc_summary ───────────────────────────────────────────────────
class TestGetBdcSummary:
    def test_success_wraps_rows_and_defaults_period_to_latest(
        self, fresh_db, monkeypatch
    ):
        monkeypatch.setattr(
            "data.bdc.get_bdc_summary",
            lambda period=None: [{"bdc_name": "ARCC"}, {"bdc_name": "OBDC"}],
        )
        out = at._tool_get_bdc_summary()
        assert out == {
            "period": "latest",
            "n_bdcs": 2,
            "bdcs": [{"bdc_name": "ARCC"}, {"bdc_name": "OBDC"}],
        }

    def test_explicit_period_is_echoed(self, fresh_db, monkeypatch):
        monkeypatch.setattr("data.bdc.get_bdc_summary", lambda period=None: [])
        out = at._tool_get_bdc_summary(period="20260331")
        assert out == {"period": "20260331", "n_bdcs": 0, "bdcs": []}

    def test_exception_surfaces_as_error_dict(self, fresh_db, monkeypatch):
        def _boom(period=None):
            raise ValueError("bad period")

        monkeypatch.setattr("data.bdc.get_bdc_summary", _boom)
        out = at._tool_get_bdc_summary(period="garbage")
        assert out == {"error": "bad period"}


# ─── _tool_get_recent_filings ────────────────────────────────────────────────
class TestGetRecentFilings:
    def test_form_type_filter(self, fresh_db):
        _seed_filing("f1", form_type="424B5")
        _seed_filing("f2", form_type="ABS-EE")
        out = at._tool_get_recent_filings(form_type="424B5")
        assert out["form_type"] == "424B5"
        assert out["n"] == 1
        assert out["filings"][0]["accession_no"] == "f1"
        assert out["filings"][0]["form_type"] == "424B5"

    def test_asset_class_filter_and_window(self, fresh_db):
        _seed_filing("f1", asset_class="auto loan")
        _seed_filing("f2", asset_class="credit card")
        _seed_filing("f3", asset_class="auto loan",
                     filed_at=_days_ago_date(90))  # outside 30d window
        out = at._tool_get_recent_filings(asset_class="auto loan")
        assert out["window_days"] == 30
        assert out["asset_class"] == "auto loan"
        assert [f["accession_no"] for f in out["filings"]] == ["f1"]

    def test_combined_filters_and_limit_cap(self, fresh_db):
        _seed_filing("f1", asset_class="auto loan", form_type="424B5")
        _seed_filing("f2", asset_class="auto loan", form_type="ABS-EE")
        out = at._tool_get_recent_filings(
            asset_class="auto loan", form_type="ABS-EE", limit=9999
        )
        assert out["n"] == 1
        assert out["filings"][0]["accession_no"] == "f2"


# ─── _tool_search_articles ───────────────────────────────────────────────────
class TestSearchArticles:
    def test_min_score_and_window(self, fresh_db):
        _seed_article("a1", score=5)
        _seed_article("a2", score=3)               # below default min_score=4
        _seed_article("a3", score=5, published_days_ago=60)  # outside 14d window
        out = at._tool_search_articles()
        assert out["min_score"] == 4
        assert out["window_days"] == 14
        assert out["n"] == 1
        assert out["articles"][0]["id"] == "a1"

    def test_category_filter(self, fresh_db):
        _seed_article("a1", category="macro")
        _seed_article("a2", category="credit")
        out = at._tool_search_articles(category="credit")
        assert out["category"] == "credit"
        assert [a["id"] for a in out["articles"]] == ["a2"]


# ─── _tool_get_market_history ────────────────────────────────────────────────
class TestGetMarketHistory:
    def test_prefixes_ticker_and_returns_close_observations(self, fresh_db):
        _seed_metric("mkt_SPY", _days_ago_date(2), 500.0)
        _seed_metric("mkt_SPY", _days_ago_date(1), 505.0)
        _seed_metric("DGS10", _days_ago_date(1), 4.2)  # not a mkt_ series
        out = at._tool_get_market_history("SPY")
        assert out["ticker"] == "SPY"
        assert out["n"] == 2
        assert out["observations"] == [
            {"date": _days_ago_date(2), "close": 500.0},
            {"date": _days_ago_date(1), "close": 505.0},
        ]

    def test_unknown_ticker_returns_empty(self, fresh_db):
        out = at._tool_get_market_history("ZZZZ")
        assert out == {"ticker": "ZZZZ", "n": 0, "observations": []}


# ─── dispatch table ──────────────────────────────────────────────────────────
class TestDispatchTable:
    def test_every_schema_has_a_dispatch_entry_and_vice_versa(self):
        schema_names = {s["name"] for s in at.TOOL_SCHEMAS}
        assert schema_names == set(at.TOOL_DISPATCH.keys())
