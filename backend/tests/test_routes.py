"""
Cover api/routes.py — REST endpoints over the FastAPI TestClient.

Strategy:
  * Seed the test DB with representative rows, hit the route, assert response shape.
  * POST refresh routes are tested by monkey-patching the underlying data fn —
    we trust the data layer's tests for the fetch correctness; here we only
    confirm the route wires through and persists `set_meta` markers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from cache import db


NOW = "2026-05-29T12:00:00+00:00"


# ─── helpers ─────────────────────────────────────────────────────────────────
def _seed_article(
    id_, title="t", category="macro", source_type="news",
    score=4, is_read=0, **overrides,
):
    """Insert a fully populated article row (some fields are post-insert updates)."""
    row = {
        "id": id_, "feed_name": "Bloomberg", "feed_category": category,
        "title": title, "snippet": "s", "url": f"https://x/{id_}",
        "published_at": "2026-05-28T10:00:00+00:00", "fetched_at": NOW,
        "source_type": source_type,
    }
    row.update(overrides)
    db.upsert_article(row)
    if score is not None:
        db.update_article_relevance(id_, score, json.dumps([]))
    if is_read:
        db.mark_article_read(id_)


def _seed_metric(series_id, date, value, label="x", category="rates"):
    db.upsert_metric({
        "series_id": series_id, "label": label, "category": category,
        "date": date, "value": value, "fetched_at": NOW,
    })


def _seed_edgar(accession, asset_class="auto loan", form_type="424B5",
                issuance_type="debt", company="Carvana Auto Receivables Trust"):
    db.upsert_edgar_filing({
        "accession_no": accession, "company_name": company,
        "form_type": form_type, "filed_at": "2026-05-28",
        "description": "d", "url": "https://x", "asset_class": asset_class,
        "issuance_type": issuance_type, "fetched_at": NOW,
    })


# ─── /api/articles ───────────────────────────────────────────────────────────
class TestArticlesRoute:
    def test_default_min_score_4_returns_only_high_relevance(
        self, api_client, fresh_db
    ):
        _seed_article("a3", score=3)
        _seed_article("a4", score=4)
        _seed_article("a5", score=5)

        resp = api_client.get("/api/articles")
        assert resp.status_code == 200
        ids = {a["id"] for a in resp.json()["items"]}
        assert ids == {"a4", "a5"}

    def test_min_score_param_overrides_default(self, api_client, fresh_db):
        _seed_article("a3", score=3)
        _seed_article("a4", score=4)
        resp = api_client.get("/api/articles?min_score=3")
        assert len(resp.json()["items"]) == 2

    def test_category_filter(self, api_client, fresh_db):
        _seed_article("a1", category="macro", score=5)
        _seed_article("a2", category="credit", score=5)
        resp = api_client.get("/api/articles?category=credit")
        ids = {a["id"] for a in resp.json()["items"]}
        assert ids == {"a2"}

    def test_source_type_filter_supports_comma_separated(
        self, api_client, fresh_db
    ):
        _seed_article("a1", source_type="wsj", score=5)
        _seed_article("a2", source_type="ft", score=5)
        _seed_article("a3", source_type="bloomberg", score=5)
        resp = api_client.get("/api/articles?source_type=wsj,ft")
        ids = {a["id"] for a in resp.json()["items"]}
        assert ids == {"a1", "a2"}

    def test_pagination_offset_and_limit(self, api_client, fresh_db):
        for i in range(5):
            _seed_article(f"a{i}", score=5)
        resp = api_client.get("/api/articles?limit=2&offset=1")
        data = resp.json()
        assert data["limit"] == 2
        assert data["offset"] == 1
        assert len(data["items"]) == 2


# ─── /api/articles/{id}/read ─────────────────────────────────────────────────
class TestMarkArticleRead:
    def test_marks_and_returns_1(self, api_client, fresh_db):
        _seed_article("ar1", score=5)
        resp = api_client.post("/api/articles/ar1/read")
        assert resp.status_code == 200
        assert resp.json() == {"id": "ar1", "is_read": 1}

    def test_missing_article_returns_404(self, api_client, fresh_db):
        resp = api_client.post("/api/articles/nope/read")
        assert resp.status_code == 404


# ─── /api/articles/feed-health ───────────────────────────────────────────────
class TestFeedHealthRoute:
    def test_returns_health_rows_ordered_by_live_status(
        self, api_client, fresh_db
    ):
        db.upsert_feed_health({
            "feed_name": "DeadFeed", "url": "https://d", "last_checked": NOW,
            "is_live": 0, "http_status": 500, "error_msg": "x", "needs_url": 0,
        })
        db.upsert_feed_health({
            "feed_name": "LiveFeed", "url": "https://l", "last_checked": NOW,
            "is_live": 1, "http_status": 200, "error_msg": None, "needs_url": 0,
        })
        resp = api_client.get("/api/articles/feed-health")
        names = [r["feed_name"] for r in resp.json()]
        assert names[0] == "LiveFeed"  # live first


# ─── /api/digests ────────────────────────────────────────────────────────────
class TestDigestRoutes:
    def test_list_digests_returns_reshape_with_date_range(
        self, api_client, fresh_db
    ):
        db.save_digest({
            "date": "2026-05-29", "session": "AM", "summary": "...",
            "article_count": 5, "hours_back": 24, "min_score": 4,
            "date_from": "from", "date_to": "to", "model": "claude-sonnet-4-6",
            "generated_at": NOW,
        })
        resp = api_client.get("/api/digests")
        assert resp.status_code == 200
        d = resp.json()[0]
        assert d["date_range"] == {"from": "from", "to": "to"}
        assert d["session"] == "AM"

    def test_post_digest_422_when_no_articles(
        self, api_client, fresh_db
    ):
        resp = api_client.post("/api/digest", json={"hours_back": 24, "min_score": 4})
        assert resp.status_code == 422


# ─── /api/market/* ───────────────────────────────────────────────────────────
class TestMarketRoutes:
    def test_market_history_filters_to_series_prefix(
        self, api_client, fresh_db
    ):
        _seed_metric("mkt_SPY", "2026-05-27", 500.0)
        _seed_metric("mkt_SPY", "2026-05-28", 505.0)
        _seed_metric("mkt_QQQ", "2026-05-28", 420.0)
        resp = api_client.get("/api/market/history/SPY")
        rows = resp.json()
        assert len(rows) == 2
        assert all("value" in r for r in rows)


# ─── /api/fred/* ─────────────────────────────────────────────────────────────
class TestFredRoutes:
    def test_fred_history_returns_ascending_dates(
        self, api_client, fresh_db
    ):
        for d, v in [("2026-05-26", 4.0), ("2026-05-27", 4.1), ("2026-05-28", 4.2)]:
            _seed_metric("DGS10", d, v, label="10Y", category="rates")
        rows = api_client.get("/api/fred/history/DGS10").json()
        # The DB query orders DESC then reverses → ascending in response.
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)

    def test_fred_latest_returns_max_date_per_series(
        self, api_client, fresh_db
    ):
        _seed_metric("DGS10", "2026-05-27", 4.0)
        _seed_metric("DGS10", "2026-05-28", 4.2)
        _seed_metric("DGS2", "2026-05-28", 3.9)
        rows = api_client.get("/api/fred/latest").json()
        by_id = {r["series_id"]: r for r in rows}
        assert by_id["DGS10"]["date"] == "2026-05-28"
        assert by_id["DGS10"]["value"] == 4.2


# ─── /api/edgar/* ────────────────────────────────────────────────────────────
class TestEdgarRoutes:
    def test_filings_filters_by_form_type_and_asset_class(
        self, api_client, fresh_db
    ):
        _seed_edgar("f1", form_type="424B5", asset_class="auto loan")
        _seed_edgar("f2", form_type="ABS-EE", asset_class="auto loan")
        _seed_edgar("f3", form_type="424B5", asset_class="credit card")

        out = api_client.get("/api/edgar/filings?form_type=424B5").json()
        assert {r["accession_no"] for r in out} == {"f1", "f3"}

        out = api_client.get("/api/edgar/filings?asset_class=credit card").json()
        assert {r["accession_no"] for r in out} == {"f3"}

    def test_filings_issuance_type_validates_pattern(
        self, api_client, fresh_db
    ):
        resp = api_client.get("/api/edgar/filings?issuance_type=invalid")
        assert resp.status_code == 422

    def test_facets_returns_distinct_values(self, api_client, fresh_db):
        _seed_edgar("f1", form_type="424B5", asset_class="auto loan")
        _seed_edgar("f2", form_type="ABS-EE", asset_class="auto loan")
        data = api_client.get("/api/edgar/facets").json()
        assert "424B5" in data["form_types"]
        assert "ABS-EE" in data["form_types"]
        assert "auto loan" in data["asset_classes"]


# ─── /api/abs/* ─────────────────────────────────────────────────────────────
def _seed_pricing(acc, segment, date, tranches):
    for tr in tranches:
        db.upsert_abs_pricing_tranche({
            "accession_no": acc, "deal_name": f"deal-{acc}",
            "issuer": "issuer", "segment": segment, "pricing_date": date,
            "url": "https://x", "fetched_at": NOW, **tr,
        })


class TestAbsPricingRoutes:
    def test_pricing_returns_one_entry_per_deal(self, api_client, fresh_db):
        _seed_pricing("d1", "subprime_auto", "2026-05-01", [
            {"class_name": "A-2", "rating": "AAA", "wal": 2.5,
             "benchmark": "I-CRV", "spread_bps": 55.0, "coupon": 5.0},
            {"class_name": "B", "rating": "AA", "wal": 3.0,
             "benchmark": "I-CRV", "spread_bps": 100.0, "coupon": 5.5},
        ])
        data = api_client.get("/api/abs/pricing").json()
        assert len(data) == 1
        assert len(data[0]["tranches"]) == 2

    def test_pricing_segment_filter(self, api_client, fresh_db):
        _seed_pricing("d1", "subprime_auto", "2026-05-01",
                      [{"class_name": "A", "rating": "AAA", "wal": 2.0,
                        "benchmark": "I-CRV", "spread_bps": 50.0, "coupon": 5.0}])
        _seed_pricing("d2", "prime_auto", "2026-05-02",
                      [{"class_name": "A", "rating": "AAA", "wal": 2.0,
                        "benchmark": "I-CRV", "spread_bps": 30.0, "coupon": 4.5}])
        data = api_client.get("/api/abs/pricing?segment=prime_auto").json()
        assert len(data) == 1
        assert data[0]["segment"] == "prime_auto"

    def test_spread_momentum_returns_senior_and_subordinate(
        self, api_client, fresh_db
    ):
        _seed_pricing("d1", "subprime_auto", "2026-05-01", [
            {"class_name": "A-2", "rating": "AAA", "wal": 2.5,
             "benchmark": "I-CRV", "spread_bps": 55.0, "coupon": 5.0},
            {"class_name": "B", "rating": "AA", "wal": 3.0,
             "benchmark": "I-CRV", "spread_bps": 100.0, "coupon": 5.5},
        ])
        data = api_client.get("/api/abs/spread-momentum").json()
        assert data[0]["senior_spread"] == 55.0
        assert data[0]["subordinate_spread"] == 100.0


def _seed_new_issue(id_, asset_class="prime_auto_loan", confidence="high",
                    filing_date="2026-05-15", **overrides):
    row = {
        "id": id_, "accession_no": id_, "edgar_url": "u",
        "filing_date": filing_date, "asset_class": asset_class,
        "parse_confidence": confidence, "class_name": "A-2",
        "fetched_at": NOW,
    }
    row.update(overrides)
    db.upsert_abs_tranche(row)


class TestAbsNewIssuesRoute:
    def test_new_issues_filters_by_class_and_confidence(
        self, api_client, fresh_db
    ):
        _seed_new_issue("h1", asset_class="prime_auto_loan", confidence="high")
        _seed_new_issue("h2", asset_class="credit_card", confidence="medium")
        _seed_new_issue("h3", asset_class="prime_auto_loan", confidence="low")

        data = api_client.get(
            "/api/abs/new-issues?asset_class=prime_auto_loan&min_confidence=medium"
        ).json()
        assert data["count"] == 1
        assert data["items"][0]["id"] == "h1"

    def test_new_issues_min_confidence_validates_pattern(
        self, api_client, fresh_db
    ):
        resp = api_client.get("/api/abs/new-issues?min_confidence=garbage")
        assert resp.status_code == 422


class TestAbsDealSummaryRoute:
    def test_deal_summary_groups_by_asset_class(self, api_client, fresh_db):
        _seed_new_issue("h1", asset_class="prime_auto_loan",
                        principal_amount=100_000_000)
        _seed_new_issue("h2", asset_class="prime_auto_loan",
                        principal_amount=50_000_000, accession_no="acc2")
        _seed_new_issue("h3", asset_class="credit_card",
                        principal_amount=200_000_000, accession_no="acc3")
        data = api_client.get("/api/abs/deal-summary").json()
        by_class = {r["asset_class"]: r for r in data}
        assert by_class["prime_auto_loan"]["total_volume"] == 150_000_000
        assert by_class["credit_card"]["total_volume"] == 200_000_000


class TestAbsSpreadSeriesRoute:
    def test_all_bucket_bypasses_rating_filter(self, api_client, fresh_db):
        _seed_new_issue("h1", asset_class="prime_auto_loan",
                        rating_sp=None, rating_moodys=None,
                        spread_to_benchmark=60.0)
        resp = api_client.get(
            "/api/abs/spread-series?asset_class=prime_auto_loan&rating_bucket=all"
        )
        assert resp.status_code == 200
        assert len(resp.json()["series"]) == 1

    def test_aaa_bucket_matches_aaa_rating(self, api_client, fresh_db):
        _seed_new_issue("h1", asset_class="prime_auto_loan",
                        rating_sp="AAA", spread_to_benchmark=50.0)
        _seed_new_issue("h2", asset_class="prime_auto_loan",
                        rating_sp="BBB", spread_to_benchmark=200.0,
                        accession_no="acc2")
        data = api_client.get(
            "/api/abs/spread-series?asset_class=prime_auto_loan&rating_bucket=AAA"
        ).json()
        assert len(data["series"]) == 1
        assert data["series"][0]["avg_spread"] == 50.0


# ─── BDC / Regulatory / H8 / CLO / KBRA — smoke + filter ────────────────────
class TestBdcRoutes:
    def test_watch_list_returns_list(self, api_client, fresh_db):
        # data.bdc.get_watch_list reads a static YAML list — should always 200.
        resp = api_client.get("/api/bdc/watch-list")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_nonaccrual_trend_with_empty_db(self, api_client, fresh_db):
        resp = api_client.get("/api/bdc/nonaccrual-trend")
        assert resp.status_code == 200
        # Empty DB → empty list (or empty container).
        assert isinstance(resp.json(), (list, dict))


class TestRegulatoryRoute:
    def test_actions_filters_by_agency(self, api_client, fresh_db):
        for i, agency in enumerate(("CFPB", "OCC")):
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT INTO regulatory_actions
                       (id, agency, action_type, title, publication_date, fetched_at)
                       VALUES (?, ?, 'RULE', 't', ?, ?)""",
                    (f"r{i}", agency, "2026-05-28", NOW),
                )
        out = api_client.get("/api/regulatory/actions?agency=CFPB").json()
        assert all(r["agency"] == "CFPB" for r in out)


class TestKbraRoute:
    def test_presales_returns_filterable_list(self, api_client, fresh_db):
        for i, ac in enumerate(("auto", "credit_card")):
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT INTO kbra_presales
                       (id, deal_name, asset_class, pdf_filename, parsed_at)
                       VALUES (?, 'deal', ?, 'f.pdf', ?)""",
                    (f"k{i}", ac, NOW),
                )
        out = api_client.get("/api/kbra/presales?asset_class=auto").json()
        assert all(p["asset_class"] == "auto" for p in out)


# ─── /api/status ─────────────────────────────────────────────────────────────
class TestStatusRoute:
    def test_status_shape(self, api_client, fresh_db):
        _seed_article("a1", score=5)
        _seed_article("a2", score=None)  # unscored
        _seed_metric("DGS10", "2026-05-28", 4.5)
        _seed_edgar("f1")

        data = api_client.get("/api/status").json()
        assert data["articles"]["total"] == 2
        assert data["articles"]["scored"] == 1
        assert data["metrics"] == 1
        assert data["edgar_filings"] == 1
        assert "jobs" in data  # observability summary present


# ─── POST refresh routes — wiring only (data fns are mocked) ─────────────────
class TestRefreshRouteWiring:
    def test_indicators_refresh_returns_row_count(
        self, api_client, fresh_db, monkeypatch
    ):
        monkeypatch.setattr(
            "data.indicators.compute_all_indicators", lambda: 42
        )
        resp = api_client.post("/api/indicators/refresh")
        assert resp.status_code == 200
        assert resp.json() == {"rows": 42}

    def test_hhdc_refresh_returns_row_count(
        self, api_client, fresh_db, monkeypatch
    ):
        monkeypatch.setattr(
            "data.hhdc.fetch_hhdc_transitions", lambda: 7
        )
        resp = api_client.post("/api/hhdc/refresh")
        assert resp.status_code == 200
        assert resp.json() == {"rows": 7}

    def test_edgar_refresh_returns_inserted_count(
        self, api_client, fresh_db, monkeypatch
    ):
        monkeypatch.setattr(
            "data.edgar.fetch_abs_filings", lambda days_back=3: 12
        )
        resp = api_client.post("/api/edgar/refresh")
        assert resp.status_code == 200
        assert resp.json() == {"inserted": 12}

    def test_market_refresh_returns_row_count_and_sets_meta(
        self, api_client, fresh_db, monkeypatch
    ):
        monkeypatch.setattr("data.market.fetch_market_data", lambda: 9000)
        resp = api_client.post("/api/market/refresh")
        assert resp.status_code == 200
        assert resp.json() == {"rows": 9000}
        assert db.get_meta("last_market_refresh") is not None

    def test_fred_refresh_returns_both_counts_and_sets_meta(
        self, api_client, fresh_db, monkeypatch
    ):
        monkeypatch.setattr("data.fred.fetch_fred_series", lambda: 1500)
        monkeypatch.setattr(
            "data.indicators.compute_all_indicators", lambda: 300
        )
        resp = api_client.post("/api/fred/refresh")
        assert resp.status_code == 200
        assert resp.json() == {"fred_rows": 1500, "indicator_rows": 300}
        assert db.get_meta("last_fred_refresh") is not None

    def test_bdc_refresh_sets_meta_marker(
        self, api_client, fresh_db, monkeypatch
    ):
        monkeypatch.setattr("data.bdc.fetch_bdc_data", lambda: 5)
        resp = api_client.post("/api/bdc/refresh")
        assert resp.status_code == 200
        assert resp.json() == {"holdings_stored": 5}
        assert db.get_meta("last_bdc_refresh") is not None
