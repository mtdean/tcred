"""
Cover data/daily_brief.py — scheduled morning digest + ntfy push.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cache import db
from data import daily_brief


def _seed_article(conn, i: int, score: int = 5, hours_ago: int = 2):
    published = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn.execute(
        "INSERT INTO articles (id, feed_name, feed_category, title, url, published_at, "
        "fetched_at, relevance_score) VALUES (?, ?, 'macro', ?, ?, ?, ?, ?)",
        (f"art{i}", "TestFeed", f"Headline {i}", f"http://x/{i}",
         published, published, score),
    )
    conn.commit()


@pytest.fixture
def _env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


class TestRunMorningBrief:
    def test_generates_persists_and_pushes(
        self, db_conn, mock_anthropic, mocked_responses, monkeypatch, _env_key
    ):
        for i in range(3):
            _seed_article(db_conn, i)
        mock_anthropic.next_text("Markets were quiet overnight; HY spreads unchanged.")
        from data import alerts
        monkeypatch.setattr(alerts.settings, "NTFY_TOPIC", "t")
        monkeypatch.setattr(alerts.settings, "NTFY_SERVER", "https://ntfy.example")
        monkeypatch.setattr(
            daily_brief, "load_data_sources",
            lambda: {"daily_brief": {"enabled": True, "push": True}},
        )
        mocked_responses.post("https://ntfy.example/t", status=200)

        n = daily_brief.run_morning_brief()
        assert n == 3
        digests = db.get_digests(5)
        assert len(digests) == 1
        assert "quiet overnight" in digests[0]["summary"]
        assert len(mocked_responses.calls) == 1
        assert mocked_responses.calls[0].request.headers["Title"].startswith(
            "TCRED Morning Brief"
        )

    def test_push_disabled_still_persists(
        self, db_conn, mock_anthropic, monkeypatch, _env_key
    ):
        _seed_article(db_conn, 1)
        mock_anthropic.next_text("Brief text.")
        monkeypatch.setattr(
            daily_brief, "load_data_sources",
            lambda: {"daily_brief": {"enabled": True, "push": False}},
        )
        # No mocked_responses fixture: any HTTP push attempt would error loudly.
        assert daily_brief.run_morning_brief() == 1
        assert len(db.get_digests(5)) == 1

    def test_no_articles_is_quiet_success(self, fresh_db, monkeypatch, _env_key):
        monkeypatch.setattr(
            daily_brief, "load_data_sources",
            lambda: {"daily_brief": {"enabled": True, "push": True}},
        )
        assert daily_brief.run_morning_brief() == 0
        assert db.get_digests(5) == []

    def test_no_topic_skips_push_but_persists(
        self, db_conn, mock_anthropic, monkeypatch, _env_key
    ):
        _seed_article(db_conn, 1)
        mock_anthropic.next_text("Brief text.")
        from data import alerts
        monkeypatch.setattr(alerts.settings, "NTFY_TOPIC", "")
        monkeypatch.setattr(
            daily_brief, "load_data_sources",
            lambda: {"daily_brief": {"enabled": True, "push": True}},
        )
        assert daily_brief.run_morning_brief() == 1
        assert len(db.get_digests(5)) == 1
