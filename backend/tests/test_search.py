"""Cover FTS5 article search (cache/db.py) + the reader/search API routes.

Strategy:
  * The FTS index is trigger-maintained — verify insert/update/delete sync by
    searching after each mutation.
  * Ranking contract: title matches outrank body matches (BM25 col weights).
  * Query robustness: raw FTS syntax works; syntax-invalid input falls back
    to the quoted form instead of erroring.
"""

from __future__ import annotations

import pytest

from cache import db


NOW = "2026-09-01T12:00:00+00:00"


def _seed(aid: str, title: str, *, snippet="", content=None, score=4,
          published="2026-08-30T10:00:00+00:00"):
    db.upsert_article({
        "id": aid,
        "feed_name": "Feed",
        "feed_category": "credit",
        "title": title,
        "snippet": snippet,
        "url": f"https://x/{aid}",
        "published_at": published,
        "fetched_at": NOW,
        "content_text": content,
    })
    db.update_article_relevance(aid, score, "[]")


class TestSearchArticlesFts:
    def test_matches_title(self, fresh_db):
        _seed("a1", "Subprime auto delinquencies rise")
        _seed("a2", "Equity rally continues")
        out = db.search_articles_fts("subprime")
        assert [r["id"] for r in out] == ["a1"]

    def test_matches_body_and_flags_full_text(self, fresh_db):
        _seed("a1", "Weekly letter", content="A deep dive into CLO arbitrage economics. " * 30)
        out = db.search_articles_fts("arbitrage")
        assert out and out[0]["id"] == "a1"
        assert out[0]["has_full_text"] == 1
        assert "[arbitrage]" in out[0]["match_snippet"].lower()

    def test_title_match_outranks_body_match(self, fresh_db):
        # Realistic body: the term appears once amid normal prose (a term
        # repeated dozens of times would legitimately win on BM25 tf).
        body = "Markets stayed calm. " * 30 + "One aside on tokenization. " + "More prose. " * 30
        _seed("body", "Something else entirely", content=body)
        _seed("title", "Tokenization of private credit")
        out = db.search_articles_fts("tokenization")
        assert [r["id"] for r in out] == ["title", "body"]

    def test_porter_stemming(self, fresh_db):
        _seed("a1", "Defaults are rising across vintages")
        assert db.search_articles_fts("default")  # stems to match "defaults"

    def test_min_score_filters_and_unscored_pass_at_one(self, fresh_db):
        _seed("hi", "spread widening", score=5)
        _seed("lo", "spread widening too", score=2)
        assert {r["id"] for r in db.search_articles_fts("spread", min_score=4)} == {"hi"}
        assert {r["id"] for r in db.search_articles_fts("spread", min_score=1)} == {"hi", "lo"}

    def test_days_back_window(self, fresh_db):
        _seed("old", "haircut analysis", published="2024-01-01T00:00:00+00:00")
        assert db.search_articles_fts("haircut", days_back=30) == []
        assert db.search_articles_fts("haircut", days_back=3650)

    def test_update_reindexes(self, fresh_db):
        _seed("a1", "Old title")
        assert db.search_articles_fts("aircraft") == []
        with db.get_conn() as conn:
            conn.execute("UPDATE articles SET title='Aircraft ABS primer' WHERE id='a1'")
        assert [r["id"] for r in db.search_articles_fts("aircraft")] == ["a1"]
        assert db.search_articles_fts("old") == []

    def test_delete_removes_from_index(self, fresh_db):
        _seed("a1", "Ephemeral story")
        with db.get_conn() as conn:
            conn.execute("DELETE FROM articles WHERE id='a1'")
        assert db.search_articles_fts("ephemeral") == []

    def test_invalid_fts_syntax_falls_back_to_quoted(self, fresh_db):
        _seed("a1", 'The "AND" trade AND more')
        # Bare AND at the end is invalid FTS5 syntax; fallback quotes tokens.
        out = db.search_articles_fts("trade AND")
        assert [r["id"] for r in out] == ["a1"]

    def test_phrase_query(self, fresh_db):
        _seed("a1", "private credit sports finance")
        _seed("a2", "sports betting private markets")
        out = db.search_articles_fts('"private credit"')
        assert [r["id"] for r in out] == ["a1"]

    def test_rebuild_indexes_preexisting_rows(self, fresh_db):
        # Simulate a DB created before FTS existed: drop the index + triggers,
        # insert a row, then re-run migrations — the rebuild must pick it up.
        with db.get_conn() as conn:
            conn.execute("DROP TABLE IF EXISTS articles_fts")
            for trg in ("articles_fts_ai", "articles_fts_ad", "articles_fts_au"):
                conn.execute(f"DROP TRIGGER IF EXISTS {trg}")
            conn.execute(
                "INSERT INTO articles (id, feed_name, feed_category, title, url, fetched_at) "
                "VALUES ('pre', 'F', 'credit', 'Preexisting esoteric ABS', 'u', ?)",
                (NOW,),
            )
        db.init_db()
        assert [r["id"] for r in db.search_articles_fts("esoteric")] == ["pre"]


class TestGetArticleContent:
    def test_returns_full_payload(self, fresh_db):
        _seed("a1", "Reader story", content="Para one.\n\nPara two.")
        row = db.get_article_content("a1")
        assert row["title"] == "Reader story"
        assert row["content_text"] == "Para one.\n\nPara two."

    def test_missing_returns_none(self, fresh_db):
        assert db.get_article_content("nope") is None


class TestSearchRoutes:
    def test_search_endpoint_shape(self, api_client, fresh_db):
        _seed("a1", "Manheim used vehicle values fall")
        data = api_client.get("/api/articles/search?q=manheim").json()
        assert data["query"] == "manheim"
        assert [r["id"] for r in data["items"]] == ["a1"]
        assert "match_snippet" in data["items"][0]

    def test_search_q_too_short_is_422(self, api_client, fresh_db):
        assert api_client.get("/api/articles/search?q=x").status_code == 422

    def test_content_endpoint(self, api_client, fresh_db):
        _seed("a1", "Reader story", content="Body text here. " * 60)
        data = api_client.get("/api/articles/a1/content").json()
        assert data["id"] == "a1"
        assert data["content_text"].startswith("Body text here.")

    def test_content_404(self, api_client, fresh_db):
        assert api_client.get("/api/articles/zzz/content").status_code == 404
