"""Cover data/classifier.py — batched Claude relevance scoring.

Strategy:
  * `_build_user_prompt` is a deterministic string-builder — test the contract.
  * `classify_articles` goes through the stubbed Anthropic client; vary the
    response payload to cover: happy path, fenced JSON, malformed JSON, empty
    DB, items the API mis-ids, etc.
  * Persistence is verified by querying the test DB after scoring.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from cache import db
from data import classifier as c


NOW = "2026-05-29T12:00:00+00:00"


@pytest.fixture
def stub_client(monkeypatch):
    """
    Replace classifier's module-level anthropic client. The real one is built
    at import time, so the stub_client fixture (which patches the class)
    doesn't reach it. This fixture installs a tiny stub whose .messages.create
    returns whatever text you set via next_text(...).
    """
    state = {"text": "[]"}

    class _StubMsg:
        def __init__(self, text):
            self.content = [MagicMock(text=text)]

    class _StubMessages:
        def create(self, **kwargs):
            return _StubMsg(state["text"])

    class _StubClient:
        messages = _StubMessages()

    monkeypatch.setattr(c, "client", _StubClient())

    class _Handle:
        def next_text(self, text):
            state["text"] = text

    return _Handle()


def _seed_unscored(n: int) -> list[str]:
    """Insert n unscored articles, return their ids."""
    ids = []
    for i in range(n):
        aid = f"a{i}"
        db.upsert_article({
            "id": aid,
            "feed_name": "Bloomberg",
            "feed_category": "macro",
            "title": f"Headline {i}",
            "snippet": f"snippet {i}",
            "url": f"https://x/{i}",
            "published_at": "2026-05-28T10:00:00+00:00",
            "fetched_at": NOW,
        })
        ids.append(aid)
    return ids


# ─── _build_user_prompt ──────────────────────────────────────────────────────
class TestBuildUserPrompt:
    def test_emits_one_line_per_article_with_ids(self):
        articles = [
            {"id": "x1", "title": "Fed hikes", "snippet": "rate decision"},
            {"id": "x2", "title": "ABS pricing", "snippet": "tightened"},
        ]
        prompt = c._build_user_prompt(articles)
        # Header is present.
        assert prompt.startswith("Score these articles:")
        # Both ids appear.
        assert "ID:x1" in prompt
        assert "ID:x2" in prompt
        # Titles propagate verbatim.
        assert "TITLE:Fed hikes" in prompt
        assert "TITLE:ABS pricing" in prompt

    def test_truncates_snippet_to_200_chars(self):
        long_snip = "a" * 500
        articles = [{"id": "x1", "title": "t", "snippet": long_snip}]
        prompt = c._build_user_prompt(articles)
        # The SNIPPET:... segment must contain at most 200 a's.
        seg = prompt.split("SNIPPET:")[1]
        assert seg.count("a") == 200

    def test_handles_none_snippet(self):
        # Some upstream items have no snippet at all.
        articles = [{"id": "x1", "title": "t", "snippet": None}]
        prompt = c._build_user_prompt(articles)
        assert "SNIPPET:" in prompt  # empty but present


# ─── classify_articles ──────────────────────────────────────────────────────
class TestClassifyArticles:
    async def test_returns_zero_when_no_unscored(self, fresh_db, stub_client):
        # Empty DB → no API call needed → returns 0.
        n = await c.classify_articles(batch_size=10)
        assert n == 0

    async def test_persists_scores_and_tags(self, fresh_db, stub_client):
        ids = _seed_unscored(3)
        stub_client.next_text(json.dumps([
            {"id": ids[0], "score": 5, "tags": ["macro", "rates"]},
            {"id": ids[1], "score": 3, "tags": ["fintech"]},
            {"id": ids[2], "score": 1, "tags": []},
        ]))
        n = await c.classify_articles(batch_size=10)
        assert n == 3

        with db.get_conn() as conn:
            rows = {
                r["id"]: r
                for r in conn.execute(
                    "SELECT id, relevance_score, relevance_tags FROM articles"
                ).fetchall()
            }
        assert rows[ids[0]]["relevance_score"] == 5
        assert json.loads(rows[ids[0]]["relevance_tags"]) == ["macro", "rates"]
        assert rows[ids[1]]["relevance_score"] == 3
        assert rows[ids[2]]["relevance_score"] == 1
        assert json.loads(rows[ids[2]]["relevance_tags"]) == []

    async def test_strips_markdown_json_fences(self, fresh_db, stub_client):
        ids = _seed_unscored(1)
        fenced = (
            "```json\n"
            + json.dumps([{"id": ids[0], "score": 4, "tags": ["macro"]}])
            + "\n```"
        )
        stub_client.next_text(fenced)
        n = await c.classify_articles(batch_size=10)
        assert n == 1

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT relevance_score FROM articles WHERE id=?", (ids[0],),
            ).fetchone()
        assert row["relevance_score"] == 4

    async def test_malformed_json_returns_zero(self, fresh_db, stub_client):
        _seed_unscored(2)
        stub_client.next_text("not json at all {")
        n = await c.classify_articles(batch_size=10)
        assert n == 0

        # Scores should remain NULL.
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT relevance_score FROM articles"
            ).fetchall()
        assert all(r["relevance_score"] is None for r in rows)

    async def test_score_coerced_to_int(self, fresh_db, stub_client):
        # Model occasionally returns a numeric string — int() must absorb it.
        ids = _seed_unscored(1)
        stub_client.next_text(json.dumps([
            {"id": ids[0], "score": "4", "tags": ["macro"]},
        ]))
        n = await c.classify_articles(batch_size=10)
        assert n == 1
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT relevance_score FROM articles WHERE id=?", (ids[0],),
            ).fetchone()
        assert row["relevance_score"] == 4

    async def test_missing_tags_field_defaults_to_empty(
        self, fresh_db, stub_client
    ):
        ids = _seed_unscored(1)
        # Model omits the tags key entirely → classifier should default to [].
        stub_client.next_text(json.dumps([
            {"id": ids[0], "score": 5},
        ]))
        n = await c.classify_articles(batch_size=10)
        assert n == 1

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT relevance_tags FROM articles WHERE id=?", (ids[0],),
            ).fetchone()
        assert json.loads(row["relevance_tags"]) == []

    async def test_individual_bad_item_does_not_fail_batch(
        self, fresh_db, stub_client
    ):
        ids = _seed_unscored(2)
        # First item missing the score field → int(missing) raises and that
        # row is skipped, but the second valid row still scores.
        stub_client.next_text(json.dumps([
            {"id": ids[0], "tags": ["macro"]},  # no "score" → int(KeyError) skip
            {"id": ids[1], "score": 4, "tags": []},
        ]))
        n = await c.classify_articles(batch_size=10)
        assert n == 1

        with db.get_conn() as conn:
            row0 = conn.execute(
                "SELECT relevance_score FROM articles WHERE id=?", (ids[0],),
            ).fetchone()
            row1 = conn.execute(
                "SELECT relevance_score FROM articles WHERE id=?", (ids[1],),
            ).fetchone()
        assert row0["relevance_score"] is None
        assert row1["relevance_score"] == 4
