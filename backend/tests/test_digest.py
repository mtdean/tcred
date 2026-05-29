"""Cover data/digest.py — Claude-powered prose briefing over recent articles.

Strategy:
  * `_fetch_window` is a DB query — seed articles with varying scores/dates and
    verify the cutoff and score filtering.
  * `_build_prompt` is deterministic — confirm format and snippet truncation.
  * `generate_digest` calls Anthropic; use mock_anthropic to control the
    response. Tests cover happy path, missing key (DigestError), no articles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cache import db
from data import digest as d


NOW = "2026-05-29T12:00:00+00:00"


def _seed_article(
    aid: str,
    score: int | None = 5,
    published_at: str | None = None,
    fetched_at: str = NOW,
    title: str = "headline",
    category: str = "macro",
    snippet: str = "snippet body",
) -> None:
    db.upsert_article({
        "id": aid,
        "feed_name": "X Feed",
        "feed_category": category,
        "title": title,
        "snippet": snippet,
        "url": f"https://x/{aid}",
        "published_at": published_at,
        "fetched_at": fetched_at,
    })
    if score is not None:
        # update_article_relevance expects JSON-encoded tags.
        db.update_article_relevance(aid, score, "[]")


# ─── _fetch_window ────────────────────────────────────────────────────────────
class TestFetchWindow:
    def test_filters_by_min_score(self, fresh_db):
        # Two articles, one above and one below the score floor.
        recent = datetime.now(timezone.utc).isoformat()
        _seed_article("hi", score=5, published_at=recent)
        _seed_article("lo", score=2, published_at=recent)

        articles, _, _ = d._fetch_window(hours_back=24, min_score=4)
        ids = {a["id"] for a in articles}
        assert ids == {"hi"}

    def test_excludes_articles_outside_window(self, fresh_db):
        # 48h-old article should fall outside a 24h window.
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _seed_article("old", score=5, published_at=old)
        _seed_article("new", score=5, published_at=recent)

        articles, _, _ = d._fetch_window(hours_back=24, min_score=4)
        ids = {a["id"] for a in articles}
        assert ids == {"new"}

    def test_falls_back_to_fetched_at_when_published_null(self, fresh_db):
        # No published_at on the article — fetched_at must drive the cutoff.
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _seed_article("a1", score=5, published_at=None, fetched_at=recent)

        articles, oldest, newest = d._fetch_window(hours_back=24, min_score=4)
        assert len(articles) == 1
        assert oldest == recent
        assert newest == recent

    def test_no_matches_returns_empty_with_synthetic_window(self, fresh_db):
        # Empty DB — function still returns a (cutoff, now) pair to label.
        articles, oldest, newest = d._fetch_window(hours_back=12, min_score=4)
        assert articles == []
        # oldest is "now - 12h" cutoff; newest is "now". Both must be ISO strings.
        assert "T" in oldest
        assert "T" in newest

    def test_orders_by_relevance_then_date_desc(self, fresh_db):
        recent = datetime.now(timezone.utc).isoformat()
        _seed_article("low", score=4, published_at=recent)
        _seed_article("high", score=5, published_at=recent)

        articles, _, _ = d._fetch_window(hours_back=24, min_score=4)
        # Highest score first.
        assert articles[0]["id"] == "high"
        assert articles[1]["id"] == "low"


# ─── _build_prompt ────────────────────────────────────────────────────────────
class TestBuildPrompt:
    def test_renders_one_line_per_article_with_category_score_tag(self):
        articles = [
            {
                "feed_category": "macro", "relevance_score": 5,
                "title": "Fed hikes",
                "snippet": "Rate decision details",
            },
            {
                "feed_category": "credit", "relevance_score": 4,
                "title": "ABS spreads tighten",
                "snippet": "subprime auto",
            },
        ]
        prompt = d._build_prompt(articles)
        assert prompt.startswith("Recent items:")
        assert "[macro/5] Fed hikes" in prompt
        assert "[credit/4] ABS spreads tighten" in prompt

    def test_truncates_snippet_to_240_chars(self):
        long_snip = "x" * 1000
        articles = [{
            "feed_category": "macro", "relevance_score": 5,
            "title": "t", "snippet": long_snip,
        }]
        prompt = d._build_prompt(articles)
        tail = prompt.split(" — ")[-1]
        assert len(tail) == 240

    def test_handles_none_snippet(self):
        articles = [{
            "feed_category": "macro", "relevance_score": 5,
            "title": "t", "snippet": None,
        }]
        # Should not raise.
        prompt = d._build_prompt(articles)
        assert prompt.endswith("— ")


# ─── generate_digest ─────────────────────────────────────────────────────────
class TestGenerateDigest:
    def test_raises_when_no_articles(self, fresh_db, mock_anthropic):
        # Empty DB → DigestError with helpful message.
        with pytest.raises(d.DigestError) as excinfo:
            d.generate_digest(hours_back=24, min_score=4)
        assert "no articles" in str(excinfo.value).lower()

    def test_raises_when_anthropic_key_missing(
        self, fresh_db, mock_anthropic, monkeypatch
    ):
        # Even with seeded articles, empty key should error first.
        recent = datetime.now(timezone.utc).isoformat()
        _seed_article("a1", score=5, published_at=recent)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(d.DigestError) as excinfo:
            d.generate_digest()
        assert "ANTHROPIC_API_KEY" in str(excinfo.value)

    def test_happy_path_returns_summary_payload(
        self, fresh_db, mock_anthropic
    ):
        recent = datetime.now(timezone.utc).isoformat()
        _seed_article("a1", score=5, published_at=recent, title="Fed hikes")
        _seed_article("a2", score=4, published_at=recent, title="ABS spreads")

        mock_anthropic.next_text(
            "Fed signaled a hold; ABS spreads tightened modestly."
        )

        out = d.generate_digest(hours_back=24, min_score=4)
        assert out["summary"].startswith("Fed signaled a hold")
        assert out["article_count"] == 2
        assert out["hours_back"] == 24
        assert out["min_score"] == 4
        assert out["model"] == d.DIGEST_MODEL
        assert "from" in out["date_range"] and "to" in out["date_range"]
        # generated_at should be an ISO UTC string.
        assert out["generated_at"].endswith("+00:00")

    def test_strips_leading_trailing_whitespace_from_summary(
        self, fresh_db, mock_anthropic
    ):
        recent = datetime.now(timezone.utc).isoformat()
        _seed_article("a1", score=5, published_at=recent)
        mock_anthropic.next_text("\n   Briefing text here.   \n")
        out = d.generate_digest()
        assert out["summary"] == "Briefing text here."
