"""Cover data/watchlists.py + /api/watchlists routes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cache import db
from data import watchlists as wl


NOW = "2026-05-29T12:00:00+00:00"


def _seed_article(id_, title, snippet="x", category="macro", score=4, **overrides):
    row = {
        "id": id_, "feed_name": "Bloomberg", "feed_category": category,
        "title": title, "snippet": snippet, "url": f"https://x/{id_}",
        "published_at": "2026-05-28T10:00:00+00:00", "fetched_at": NOW,
    }
    row.update(overrides)
    db.upsert_article(row)
    if score is not None:
        db.update_article_relevance(id_, score, "[]")


def _seed_edgar(accession, company_name, asset_class="auto loan",
                form_type="424B5", description=""):
    db.upsert_edgar_filing({
        "accession_no": accession, "company_name": company_name,
        "form_type": form_type, "filed_at": "2026-05-28",
        "description": description, "url": "https://x",
        "asset_class": asset_class, "issuance_type": "debt",
        "fetched_at": NOW,
    })


def _seed_regulatory(id_, agency, title, abstract=""):
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO regulatory_actions
               (id, agency, action_type, title, abstract, publication_date,
                fetched_at)
               VALUES (?, ?, 'RULE', ?, ?, '2026-05-28', ?)""",
            (id_, agency, title, abstract, NOW),
        )


# ─── Validation ─────────────────────────────────────────────────────────────
class TestKeywordValidation:
    @pytest.mark.parametrize("bad", [None, "string", [], [""], ["   "], [1, 2]])
    def test_invalid_keywords_rejected(self, bad):
        with pytest.raises(ValueError):
            wl._validate_keywords(bad)

    def test_strips_whitespace_and_drops_empty(self):
        out = wl._validate_keywords(["  Carvana ", "", "   ", "subprime auto"])
        assert out == ["Carvana", "subprime auto"]


# ─── CRUD ───────────────────────────────────────────────────────────────────
class TestCrud:
    def test_create_and_get_roundtrip(self, fresh_db):
        created = wl.create_watchlist({
            "name": "Subprime Auto",
            "description": "Track subprime auto names",
            "keywords": ["Carvana", "Exeter"],
            "edgar_asset_classes": ["auto loan"],
            "min_score": 4,
        })
        assert created["id"].startswith("wl_")
        assert created["keywords"] == ["Carvana", "Exeter"]
        assert created["edgar_asset_classes"] == ["auto loan"]
        assert created["min_score"] == 4

        fetched = wl.get_watchlist(created["id"])
        assert fetched["name"] == "Subprime Auto"

    def test_create_requires_name_and_keywords(self, fresh_db):
        with pytest.raises(ValueError):
            wl.create_watchlist({"name": "", "keywords": ["x"]})
        with pytest.raises(ValueError):
            wl.create_watchlist({"name": "x", "keywords": []})

    def test_list_orders_by_updated_desc(self, fresh_db):
        a = wl.create_watchlist({"name": "A", "keywords": ["x"]})
        b = wl.create_watchlist({"name": "B", "keywords": ["x"]})
        wl.update_watchlist(a["id"], {"description": "touched"})
        out = wl.list_watchlists()
        assert [x["id"] for x in out] == [a["id"], b["id"]]

    def test_update_only_provided_fields(self, fresh_db):
        created = wl.create_watchlist({
            "name": "Original", "keywords": ["x"], "min_score": 3,
        })
        updated = wl.update_watchlist(created["id"], {"name": "Renamed"})
        assert updated["name"] == "Renamed"
        assert updated["keywords"] == ["x"]  # untouched
        assert updated["min_score"] == 3
        assert updated["updated_at"] >= created["updated_at"]

    def test_update_keywords_validated(self, fresh_db):
        created = wl.create_watchlist({"name": "x", "keywords": ["x"]})
        with pytest.raises(ValueError):
            wl.update_watchlist(created["id"], {"keywords": []})

    def test_delete(self, fresh_db):
        created = wl.create_watchlist({"name": "x", "keywords": ["x"]})
        assert wl.delete_watchlist(created["id"]) is True
        assert wl.get_watchlist(created["id"]) is None
        assert wl.delete_watchlist(created["id"]) is False

    def test_mark_viewed(self, fresh_db):
        created = wl.create_watchlist({"name": "x", "keywords": ["x"]})
        assert created["last_viewed_at"] is None
        out = wl.mark_viewed(created["id"])
        assert out["last_viewed_at"] is not None


# ─── Match engine ───────────────────────────────────────────────────────────
class TestKeywordRegex:
    def test_substring_or_match(self):
        rx = wl._keyword_re(["Carvana", "subprime auto"])
        assert rx.search("Carvana files new ABS")
        assert rx.search("Subprime Auto delinquencies up")  # case-insensitive
        assert not rx.search("Honda prime issuance")

    def test_special_chars_escaped(self):
        rx = wl._keyword_re(["AAA(sf)"])
        assert rx.search("Rating: AAA(sf)")

    def test_word_boundaries_kill_substring_false_positives(self):
        # The classic company-name traps: Ares/shares, Affirm/affirmed, SoFi/Sofia.
        rx = wl._keyword_re(["Ares", "Affirm", "SoFi"])
        assert rx.search("Ares Management raises new fund")
        assert rx.search("Affirm reports quarterly earnings")
        assert not rx.search("Bank shares rally on the news")
        assert not rx.search("Moody's affirmed the ratings")
        assert not rx.search("Sofia hosts the conference")

    def test_boundary_match_with_leading_symbol_keyword(self):
        rx = wl._keyword_re(["S&P"])
        assert rx.search("S&P Global downgraded the tranche")
        assert not rx.search("CUSIP codes are unrelated")


class TestMatchArticlesPublisherTier:
    @pytest.fixture(autouse=True)
    def tiers(self, monkeypatch):
        from data import feeds
        monkeypatch.setattr(feeds, "load_data_sources", lambda: {
            "publisher_tiers": {
                "trusted": ["WSJ"],
                "junk": ["Stock Titan"],
            }
        })

    def test_sorted_trusted_first_junk_last_with_tier_field(self, fresh_db):
        for id_, pub, when in [
            ("junk", "Stock Titan", "2026-09-01T10:00:00+00:00"),  # newest
            ("blog", None,          "2026-09-01T09:00:00+00:00"),
            ("wsj",  "WSJ",         "2026-09-01T08:00:00+00:00"),  # oldest
        ]:
            _seed_article(id_, "Carvana deal news", score=4,
                          published_at=when, publisher=pub)
        w = wl.create_watchlist({"name": "S", "keywords": ["Carvana"], "min_score": 3})
        out = wl._match_articles(wl._keyword_re(w["keywords"]), w, 100)
        assert [a["id"] for a in out] == ["wsj", "blog", "junk"]
        assert [a["publisher_tier"] for a in out] == ["trusted", "unknown", "junk"]

    def test_junk_cannot_crowd_trusted_out_of_limit(self, fresh_db):
        # 3 junk hits are newer than the single trusted hit; with limit=2 the
        # trusted article must still make the cut.
        for i in range(3):
            _seed_article(f"j{i}", "Carvana promo rehash", score=4,
                          published_at=f"2026-09-01T1{i}:00:00+00:00",
                          publisher="Stock Titan")
        _seed_article("wsj", "Carvana earnings analysis", score=4,
                      published_at="2026-09-01T01:00:00+00:00", publisher="WSJ")
        w = wl.create_watchlist({"name": "S", "keywords": ["Carvana"], "min_score": 3})
        out = wl._match_articles(wl._keyword_re(w["keywords"]), w, 2)
        assert out[0]["id"] == "wsj"
        assert len(out) == 2


class TestMatchArticles:
    def test_or_match_with_min_score(self, fresh_db):
        _seed_article("a1", "Carvana files new ABS", score=4)
        _seed_article("a2", "Honda prime issuance",  score=4)
        _seed_article("a3", "Carvana delinquencies", score=2)  # below min_score
        w = wl.create_watchlist({
            "name": "Sub", "keywords": ["Carvana", "Exeter"], "min_score": 3,
        })
        out = wl._match_articles(wl._keyword_re(w["keywords"]), w, 100)
        ids = {a["id"] for a in out}
        assert ids == {"a1"}

    def test_category_filter(self, fresh_db):
        _seed_article("a1", "Carvana macro", category="macro", score=4)
        _seed_article("a2", "Carvana credit", category="credit", score=4)
        w = wl.create_watchlist({
            "name": "Sub", "keywords": ["Carvana"],
            "news_categories": ["credit"], "min_score": 3,
        })
        out = wl._match_articles(wl._keyword_re(w["keywords"]), w, 100)
        ids = {a["id"] for a in out}
        assert ids == {"a2"}

    def test_snippet_also_searched(self, fresh_db):
        _seed_article("a1", "Auto receivables report",
                      snippet="Mentions Carvana in passing", score=4)
        w = wl.create_watchlist({
            "name": "Sub", "keywords": ["Carvana"], "min_score": 3,
        })
        out = wl._match_articles(wl._keyword_re(w["keywords"]), w, 100)
        assert {a["id"] for a in out} == {"a1"}


class TestMatchEdgar:
    def test_company_and_filter(self, fresh_db):
        _seed_edgar("f1", "Carvana Auto Receivables Trust 2026-1",
                    asset_class="auto loan")
        _seed_edgar("f2", "Wells Fargo Mortgage Trust",
                    asset_class="mortgage")
        _seed_edgar("f3", "Exeter Funding 2026-2",
                    asset_class="auto loan")
        w = wl.create_watchlist({
            "name": "Sub", "keywords": ["Carvana", "Exeter"],
            "edgar_asset_classes": ["auto loan"],
        })
        out = wl._match_edgar(wl._keyword_re(w["keywords"]), w, 100)
        accs = {f["accession_no"] for f in out}
        assert accs == {"f1", "f3"}


class TestMatchRegulatory:
    def test_agency_filter(self, fresh_db):
        _seed_regulatory("r1", "CFPB", "Subprime auto rule proposal")
        _seed_regulatory("r2", "OCC", "Bank capital subprime guidance")
        w = wl.create_watchlist({
            "name": "Sub", "keywords": ["subprime"],
            "regulatory_agencies": ["CFPB"],
        })
        out = wl._match_regulatory(wl._keyword_re(w["keywords"]), w, 100)
        ids = {r["id"] for r in out}
        assert ids == {"r1"}


class TestRunWatchlist:
    def test_aggregates_across_sources(self, fresh_db):
        _seed_article("a1", "Carvana files ABS", score=5)
        _seed_edgar("f1", "Carvana Auto Receivables Trust")
        _seed_regulatory("r1", "CFPB", "Subprime auto crackdown")
        w = wl.create_watchlist({
            "name": "Sub", "keywords": ["Carvana", "subprime auto"],
        })
        result = wl.run_watchlist(w["id"])
        assert result["counts"]["articles"] == 1
        assert result["counts"]["edgar_filings"] == 1
        assert result["counts"]["regulatory_actions"] == 1
        assert result["counts"]["total"] == 3

    def test_missing_watchlist_returns_none(self, fresh_db):
        assert wl.run_watchlist("wl_missing") is None


# ─── API routes ─────────────────────────────────────────────────────────────
class TestWatchlistRoutes:
    def test_list_empty(self, api_client, fresh_db):
        resp = api_client.get("/api/watchlists")
        assert resp.json() == {"items": []}

    def test_create_validates(self, api_client, fresh_db):
        # Missing keywords -> 422
        resp = api_client.post("/api/watchlists", json={"name": "x", "keywords": []})
        assert resp.status_code == 422

    def test_create_and_fetch(self, api_client, fresh_db):
        resp = api_client.post("/api/watchlists", json={
            "name": "Sub", "keywords": ["Carvana"], "min_score": 4,
        })
        assert resp.status_code == 200
        wid = resp.json()["id"]
        fetched = api_client.get(f"/api/watchlists/{wid}").json()
        assert fetched["name"] == "Sub"

    def test_patch_partial(self, api_client, fresh_db):
        wid = api_client.post("/api/watchlists", json={
            "name": "Sub", "keywords": ["Carvana"],
        }).json()["id"]
        resp = api_client.patch(f"/api/watchlists/{wid}",
                                json={"description": "now with detail"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "now with detail"

    def test_delete(self, api_client, fresh_db):
        wid = api_client.post("/api/watchlists", json={
            "name": "Sub", "keywords": ["Carvana"],
        }).json()["id"]
        assert api_client.delete(f"/api/watchlists/{wid}").status_code == 200
        assert api_client.get(f"/api/watchlists/{wid}").status_code == 404

    def test_results_route(self, api_client, fresh_db):
        _seed_article("a1", "Carvana ABS new issue", score=5)
        wid = api_client.post("/api/watchlists", json={
            "name": "Sub", "keywords": ["Carvana"], "min_score": 3,
        }).json()["id"]
        resp = api_client.get(f"/api/watchlists/{wid}/results")
        assert resp.status_code == 200
        body = resp.json()
        assert body["counts"]["articles"] == 1

    def test_viewed_updates_marker(self, api_client, fresh_db):
        wid = api_client.post("/api/watchlists", json={
            "name": "Sub", "keywords": ["Carvana"],
        }).json()["id"]
        first = api_client.get(f"/api/watchlists/{wid}").json()
        assert first["last_viewed_at"] is None
        resp = api_client.post(f"/api/watchlists/{wid}/viewed")
        assert resp.json()["last_viewed_at"] is not None
