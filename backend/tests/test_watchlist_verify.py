"""Cover data/watchlist_verify.py — Claude entity verification of watchlist hits.

Strategy (mirrors test_classifier/test_summarizer):
  * Stubbed module-level client; vary response payloads.
  * Caching contract verified via the watchlist_verifications table and
    run_watchlist's attached `verification` field.
  * Keyword updates must invalidate cached verdicts.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from cache import db
from data import watchlists as wl
from data import watchlist_verify as wv


NOW = "2026-09-01T12:00:00+00:00"


@pytest.fixture
def stub_client(monkeypatch):
    state = {"text": "[]"}

    class _StubMsg:
        def __init__(self, text):
            self.content = [MagicMock(text=text)]

    class _StubMessages:
        def create(self, **kwargs):
            state["last_prompt"] = kwargs["messages"][0]["content"]
            return _StubMsg(state["text"])

    class _StubClient:
        messages = _StubMessages()

    monkeypatch.setattr(wv, "client", _StubClient())

    class _Handle:
        def next_text(self, text):
            state["text"] = text

        def last_prompt(self):
            return state.get("last_prompt", "")

    return _Handle()


def _seed_article(id_, title, score=4):
    db.upsert_article({
        "id": id_, "feed_name": "Feed", "feed_category": "credit",
        "title": title, "snippet": "s", "url": f"https://x/{id_}",
        "published_at": "2026-08-31T10:00:00+00:00", "fetched_at": NOW,
    })
    db.update_article_relevance(id_, score, "[]")


def _make_watchlist(keywords=("Ares",)):
    return wl.create_watchlist({
        "name": "Ares Management", "keywords": list(keywords), "min_score": 3,
    })


class TestVerifyWatchlist:
    def test_missing_watchlist_returns_none(self, fresh_db, stub_client):
        assert wv.verify_watchlist("wl_nope") is None

    def test_no_matches_is_noop(self, fresh_db, stub_client):
        w = _make_watchlist()
        out = wv.verify_watchlist(w["id"])
        assert out == {"verified": 0, "matches": 0, "rejects": 0, "pending": 0}

    def test_stores_verdicts_and_attaches_to_results(self, fresh_db, stub_client):
        w = _make_watchlist()
        _seed_article("a1", "Ares Management raises fund")
        _seed_article("a2", "Ares the videogame sequel announced")
        stub_client.next_text(json.dumps([
            {"id": "a1", "match": True, "reason": "about the asset manager"},
            {"id": "a2", "match": False, "reason": "videogame, wrong entity"},
        ]))
        out = wv.verify_watchlist(w["id"])
        assert out["verified"] == 2
        assert out["matches"] == 1
        assert out["rejects"] == 1

        results = wl.run_watchlist(w["id"])
        by_id = {a["id"]: a for a in results["matches"]["articles"]}
        assert by_id["a1"]["verification"]["verdict"] == "match"
        assert by_id["a2"]["verification"]["verdict"] == "reject"
        assert "videogame" in by_id["a2"]["verification"]["reason"]

    def test_verified_articles_not_resent(self, fresh_db, stub_client):
        w = _make_watchlist()
        _seed_article("a1", "Ares Management raises fund")
        stub_client.next_text(json.dumps([{"id": "a1", "match": True, "reason": "r"}]))
        assert wv.verify_watchlist(w["id"])["verified"] == 1
        # Second run: verdict cached → nothing pending, no tokens spent.
        out = wv.verify_watchlist(w["id"])
        assert out == {"verified": 0, "matches": 0, "rejects": 0, "pending": 0}

    def test_keyword_update_invalidates_cache(self, fresh_db, stub_client):
        w = _make_watchlist()
        _seed_article("a1", "Ares Management raises fund")
        stub_client.next_text(json.dumps([{"id": "a1", "match": True, "reason": "r"}]))
        wv.verify_watchlist(w["id"])

        wl.update_watchlist(w["id"], {"keywords": ["Ares", "Carvana"]})
        results = wl.run_watchlist(w["id"])
        assert results["matches"]["articles"][0]["verification"] is None

    def test_malformed_json_returns_pending(self, fresh_db, stub_client):
        w = _make_watchlist()
        _seed_article("a1", "Ares Management raises fund")
        stub_client.next_text("not json {")
        out = wv.verify_watchlist(w["id"])
        assert out["verified"] == 0
        assert out["pending"] == 1

    def test_hallucinated_id_ignored(self, fresh_db, stub_client):
        w = _make_watchlist()
        _seed_article("a1", "Ares Management raises fund")
        stub_client.next_text(json.dumps([
            {"id": "ghost", "match": True, "reason": "x"},
            {"id": "a1", "match": True, "reason": "real"},
        ]))
        out = wv.verify_watchlist(w["id"])
        assert out["verified"] == 1

    def test_prompt_carries_watchlist_context(self, fresh_db, stub_client):
        w = _make_watchlist()
        _seed_article("a1", "Ares Management raises fund")
        stub_client.next_text("[]")
        wv.verify_watchlist(w["id"])
        prompt = stub_client.last_prompt()
        assert "WATCHLIST: Ares Management" in prompt
        assert "KEYWORDS: Ares" in prompt
        assert "ID:a1" in prompt


class TestVerifyRoute:
    def test_route_wires_through(self, api_client, fresh_db, monkeypatch):
        w = _make_watchlist()

        def fake_verify(watchlist_id, max_articles=40):
            assert watchlist_id == w["id"]
            return {"verified": 3, "matches": 2, "rejects": 1, "pending": 0}

        monkeypatch.setattr("data.watchlist_verify.verify_watchlist", fake_verify)
        resp = api_client.post(f"/api/watchlists/{w['id']}/verify")
        assert resp.status_code == 200
        assert resp.json()["verified"] == 3

    def test_route_404(self, api_client, fresh_db, monkeypatch):
        monkeypatch.setattr(
            "data.watchlist_verify.verify_watchlist", lambda *a, **k: None
        )
        resp = api_client.post("/api/watchlists/wl_nope/verify")
        assert resp.status_code == 404
