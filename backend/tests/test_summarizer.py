"""Cover data/summarizer.py — Claude summaries of full-text articles.

Strategy (mirrors test_classifier.py):
  * `_build_user_prompt` is deterministic — test the contract.
  * `summarize_articles` goes through a stubbed module-level client; vary the
    response payload for: happy path, fenced JSON, malformed JSON, empty DB,
    hallucinated ids, empty summaries.
  * Selection rules (needs content_text, needs score >= min_score, skips
    already-summarized) are verified against the test DB.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from cache import db
from data import summarizer as s


NOW = "2026-05-29T12:00:00+00:00"
BODY = "Spreads widened 25bps on the week as issuance surged. " * 30


@pytest.fixture
def stub_client(monkeypatch):
    """Replace summarizer's module-level anthropic client (built at import)."""
    state = {"text": "[]"}

    class _StubMsg:
        def __init__(self, text):
            self.content = [MagicMock(text=text)]

    class _StubMessages:
        def create(self, **kwargs):
            return _StubMsg(state["text"])

    class _StubClient:
        messages = _StubMessages()

    monkeypatch.setattr(s, "client", _StubClient())

    class _Handle:
        def next_text(self, text):
            state["text"] = text

    return _Handle()


def _seed(n: int, *, score: int | None = 5, content: str | None = BODY) -> list[str]:
    """Insert n articles with the given score/content, return their ids."""
    ids = []
    for i in range(n):
        aid = f"s{i}"
        db.upsert_article({
            "id": aid,
            "feed_name": "Net Interest (Marc Rubinstein)",
            "feed_category": "credit",
            "title": f"Letter {i}",
            "snippet": "snippet",
            "url": f"https://x/{i}",
            "published_at": "2026-05-28T10:00:00+00:00",
            "fetched_at": NOW,
            "content_text": content,
        })
        if score is not None:
            db.update_article_relevance(aid, score, "[]")
        ids.append(aid)
    return ids


def _summary_of(article_id: str):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT ai_summary, summarized_at FROM articles WHERE id=?",
            (article_id,),
        ).fetchone()
    return row


# ─── _build_user_prompt ──────────────────────────────────────────────────────
class TestBuildUserPrompt:
    def test_emits_id_title_body_per_article(self):
        articles = [
            {"id": "x1", "title": "On bank funding", "content_text": "body one"},
            {"id": "x2", "title": "CLO arb", "content_text": "body two"},
        ]
        prompt = s._build_user_prompt(articles)
        assert prompt.startswith("Summarize these articles:")
        assert "ID:x1" in prompt and "ID:x2" in prompt
        assert "TITLE:On bank funding" in prompt
        assert "body two" in prompt

    def test_truncates_body_to_cap(self):
        long_body = "a" * (s.MAX_BODY_CHARS + 5000)
        prompt = s._build_user_prompt(
            [{"id": "x1", "title": "t", "content_text": long_body}]
        )
        body_seg = prompt.split("BODY:\n")[1]
        assert body_seg.count("a") == s.MAX_BODY_CHARS

    def test_handles_none_body(self):
        prompt = s._build_user_prompt([{"id": "x1", "title": "t", "content_text": None}])
        assert "BODY:" in prompt


# ─── summarize_articles ──────────────────────────────────────────────────────
class TestSummarizeArticles:
    async def test_returns_zero_when_no_candidates(self, fresh_db, stub_client):
        n = await s.summarize_articles()
        assert n == 0

    async def test_persists_summary_and_timestamp(self, fresh_db, stub_client):
        ids = _seed(2)
        stub_client.next_text(json.dumps([
            {"id": ids[0], "summary": "Spreads widened 25bps."},
            {"id": ids[1], "summary": "Issuance surged."},
        ]))
        n = await s.summarize_articles()
        assert n == 2
        row = _summary_of(ids[0])
        assert row["ai_summary"] == "Spreads widened 25bps."
        assert row["summarized_at"] is not None

    async def test_skips_low_score_articles(self, fresh_db, stub_client):
        _seed(1, score=3)
        n = await s.summarize_articles(min_score=4)
        assert n == 0

    async def test_skips_articles_without_content(self, fresh_db, stub_client):
        _seed(1, content=None)
        n = await s.summarize_articles()
        assert n == 0

    async def test_skips_unscored_articles(self, fresh_db, stub_client):
        # relevance_score is NULL → NULL >= 4 is not true in SQLite.
        _seed(1, score=None)
        n = await s.summarize_articles()
        assert n == 0

    async def test_already_summarized_not_reselected(self, fresh_db, stub_client):
        ids = _seed(1)
        db.update_article_summary(ids[0], "done already")
        n = await s.summarize_articles()
        assert n == 0
        assert _summary_of(ids[0])["ai_summary"] == "done already"

    async def test_strips_markdown_json_fences(self, fresh_db, stub_client):
        ids = _seed(1)
        fenced = (
            "```json\n"
            + json.dumps([{"id": ids[0], "summary": "Fenced summary."}])
            + "\n```"
        )
        stub_client.next_text(fenced)
        n = await s.summarize_articles()
        assert n == 1
        assert _summary_of(ids[0])["ai_summary"] == "Fenced summary."

    async def test_malformed_json_returns_zero(self, fresh_db, stub_client):
        ids = _seed(1)
        stub_client.next_text("not json {")
        n = await s.summarize_articles()
        assert n == 0
        assert _summary_of(ids[0])["ai_summary"] is None

    async def test_hallucinated_id_is_ignored(self, fresh_db, stub_client):
        ids = _seed(1)
        stub_client.next_text(json.dumps([
            {"id": "nonexistent", "summary": "made up"},
            {"id": ids[0], "summary": "real one"},
        ]))
        n = await s.summarize_articles()
        assert n == 1
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM articles WHERE ai_summary IS NOT NULL"
            ).fetchall()
        assert [r["id"] for r in rows] == [ids[0]]

    async def test_empty_summary_is_skipped(self, fresh_db, stub_client):
        ids = _seed(1)
        stub_client.next_text(json.dumps([{"id": ids[0], "summary": "  "}]))
        n = await s.summarize_articles()
        assert n == 0
        assert _summary_of(ids[0])["ai_summary"] is None

    async def test_batch_size_limits_selection(self, fresh_db, stub_client):
        ids = _seed(5)
        # Echo back whatever would be asked — but only 2 fit the batch.
        stub_client.next_text(json.dumps(
            [{"id": i, "summary": "s"} for i in ids]
        ))
        # get_unsummarized_articles caps at batch_size; hallucination guard
        # drops the ids outside the requested batch.
        n = await s.summarize_articles(batch_size=2)
        assert n == 2
