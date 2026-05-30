"""Cover data/issuers.py + /api/issuers routes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cache import db
from data import issuers


NOW = "2026-05-30T12:00:00+00:00"


def _seed_new_issue(
    id_, accession, issuer_name, filing_date,
    asset_class="prime_auto_loan", confidence="high",
    class_name="A-2", wal=2.5, spread=55.0, total=100_000_000.0,
    **overrides,
):
    row = {
        "id": id_, "accession_no": accession, "edgar_url": "u",
        "filing_date": filing_date, "asset_class": asset_class,
        "parse_confidence": confidence, "class_name": class_name,
        "issuer_name": issuer_name, "wal_years": wal,
        "spread_to_benchmark": spread, "total_deal_size": total,
        "fetched_at": NOW,
    }
    row.update(overrides)
    db.upsert_abs_tranche(row)


def _seed_edgar(accession, company_name, **overrides):
    base = {
        "accession_no": accession, "company_name": company_name,
        "form_type": "424B5", "filed_at": "2026-05-28",
        "description": "", "url": "https://x",
        "asset_class": "auto loan", "issuance_type": "debt", "fetched_at": NOW,
    }
    base.update(overrides)
    db.upsert_edgar_filing(base)


def _seed_article(id_, title, score=4, **overrides):
    row = {
        "id": id_, "feed_name": "Bloomberg", "feed_category": "credit",
        "title": title, "snippet": "x", "url": f"https://x/{id_}",
        "published_at": "2026-05-29T10:00:00+00:00", "fetched_at": NOW,
    }
    row.update(overrides)
    db.upsert_article(row)
    if score is not None:
        db.update_article_relevance(id_, score, "[]")


# ─── list_issuers ───────────────────────────────────────────────────────────
class TestListIssuers:
    def test_empty_returns_empty(self, fresh_db):
        assert issuers.list_issuers() == []

    def test_distinct_with_deal_count_and_recency(self, fresh_db):
        # Carvana: 2 deals; Honda: 1 deal; latest Carvana is most recent.
        _seed_new_issue("c1-A", "c1", "Carvana Auto Receivables Trust 2026-1", "2026-03-01")
        _seed_new_issue("c1-B", "c1", "Carvana Auto Receivables Trust 2026-1", "2026-03-01", class_name="B")
        _seed_new_issue("c2-A", "c2", "Carvana Auto Receivables Trust 2026-2", "2026-05-15")
        _seed_new_issue("h1-A", "h1", "Honda Auto Receivables Trust 2026-A", "2026-04-01")
        rows = issuers.list_issuers()
        names = [r["issuer_name"] for r in rows]
        # Carvana 2026-2 most recent → its issuer string appears first.
        assert names[0].startswith("Carvana")
        by_name = {r["issuer_name"]: r for r in rows}
        carvana_1 = by_name["Carvana Auto Receivables Trust 2026-1"]
        assert carvana_1["deal_count"] == 1  # 1 accession_no
        assert carvana_1["latest_filing_date"] == "2026-03-01"

    def test_excludes_null_issuer_name(self, fresh_db):
        _seed_new_issue("x-A", "x", None, "2026-05-01")
        assert issuers.list_issuers() == []


# ─── _aggregate_deals ───────────────────────────────────────────────────────
class TestAggregateDeals:
    def test_groups_tranches_by_accession(self, fresh_db):
        _seed_new_issue("t-A", "acc1", "Carvana ...", "2026-05-01",
                        class_name="A-2", wal=2.5, spread=55.0, total=100_000_000)
        _seed_new_issue("t-B", "acc1", "Carvana ...", "2026-05-01",
                        class_name="B", wal=3.0, spread=100.0, total=100_000_000)
        out = issuers._abs_new_issues_for("Carvana", 100)
        deals = issuers._aggregate_deals(out)
        assert len(deals) == 1
        assert deals[0]["n_tranches"] == 2
        # Widest WAL with a spread is B (3.0y) → senior = B.
        assert deals[0]["senior_class_name"] == "B"
        assert deals[0]["senior_spread_bps"] == 100.0

    def test_senior_picks_widest_wal_with_spread(self, fresh_db):
        _seed_new_issue("t-A", "acc1", "X ...", "2026-05-01",
                        class_name="A-2", wal=2.5, spread=55.0)
        _seed_new_issue("t-B", "acc1", "X ...", "2026-05-01",
                        class_name="B", wal=None, spread=100.0)
        out = issuers._aggregate_deals(issuers._abs_new_issues_for("X", 100))
        # B has no WAL → A-2 wins.
        assert out[0]["senior_class_name"] == "A-2"


# ─── Cross-table search ─────────────────────────────────────────────────────
class TestSubstringSearch:
    def test_substring_matches_issuer_and_depositor(self, fresh_db):
        _seed_new_issue("a", "a1", "Carvana Auto Receivables Trust 2026-1",
                        "2026-05-01", depositor="Carvana ABS Funding")
        _seed_new_issue("b", "b1", "Honda Auto Receivables Trust",
                        "2026-05-01", depositor="Honda Acceptance")
        out = issuers._abs_new_issues_for("Carvana", 100)
        assert len(out) == 1
        assert out[0]["accession_no"] == "a1"

    def test_case_insensitive(self, fresh_db):
        _seed_new_issue("a", "a1", "CARVANA Auto", "2026-05-01")
        assert len(issuers._abs_new_issues_for("carvana", 100)) == 1
        assert len(issuers._abs_new_issues_for("CARVANA", 100)) == 1

    def test_edgar_searches_company_name(self, fresh_db):
        _seed_edgar("acc1", "Carvana Auto Receivables Trust")
        _seed_edgar("acc2", "Honda Owner Trust")
        out = issuers._edgar_for("Carvana", 100)
        assert {f["accession_no"] for f in out} == {"acc1"}

    def test_articles_use_min_score_and_window(self, fresh_db):
        _seed_article("a1", "Carvana files new ABS", score=5)
        _seed_article("a2", "Carvana stock thoughts", score=2)  # below threshold
        out = issuers._articles_for("Carvana", min_score=3, days_back=365, limit=50)
        assert {a["id"] for a in out} == {"a1"}


# ─── get_issuer_summary ─────────────────────────────────────────────────────
class TestGetIssuerSummary:
    def test_empty_query_returns_none(self, fresh_db):
        assert issuers.get_issuer_summary("   ") is None

    def test_aggregates_across_sources(self, fresh_db):
        _seed_new_issue("t-A", "acc1", "Carvana Auto Receivables Trust 2026-1",
                        "2026-05-01", class_name="A-2", wal=2.5, spread=55.0,
                        total=200_000_000)
        _seed_edgar("e1", "Carvana Auto Receivables Trust 2026-1")
        _seed_article("a1", "Carvana ABS news", score=5)

        out = issuers.get_issuer_summary("Carvana")
        assert out["query"] == "Carvana"
        assert out["stats"]["n_deals"] == 1
        assert out["stats"]["total_volume"] == 200_000_000.0
        assert len(out["deals"]) == 1
        assert len(out["edgar_filings"]) == 1
        assert len(out["articles"]) == 1


# ─── API routes ─────────────────────────────────────────────────────────────
class TestIssuerRoutes:
    def test_list_route(self, api_client, fresh_db):
        _seed_new_issue("a", "a1", "Carvana Auto Trust 2026-1", "2026-05-01")
        resp = api_client.get("/api/issuers")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["issuer_name"].startswith("Carvana")

    def test_summary_route(self, api_client, fresh_db):
        _seed_new_issue("a", "a1", "Carvana Auto Receivables Trust 2026-1", "2026-05-01")
        resp = api_client.get("/api/issuers/summary?q=Carvana")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "Carvana"
        assert body["stats"]["n_deals"] == 1

    def test_summary_requires_q(self, api_client, fresh_db):
        resp = api_client.get("/api/issuers/summary")
        assert resp.status_code == 422
