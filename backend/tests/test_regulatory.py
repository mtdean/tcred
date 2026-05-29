"""
Cover data/regulatory.py — Federal Register + agency RSS ingest and Claude
scoring.

Strategy:
  * Pure helpers (`_rss_id`, `_rss_pub_date`, `_build_user_prompt`).
  * `fetch_regulatory_actions` end-to-end: stub Federal Register JSON for each
    configured agency + monkeypatch the RSS parser to return canned entries.
  * `score_regulatory_actions` with the Claude client mocked.
  * `get_regulatory_actions` query helper seeded directly via SQL.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from cache import db
from data import regulatory as reg


NOW = "2026-05-29T12:00:00+00:00"


# ─── _rss_id ──────────────────────────────────────────────────────────────────
class TestRssId:
    def test_deterministic_for_same_inputs(self):
        a = reg._rss_id("CFPB", "https://x/post/1")
        b = reg._rss_id("CFPB", "https://x/post/1")
        assert a == b

    def test_different_for_different_links(self):
        a = reg._rss_id("CFPB", "https://x/post/1")
        b = reg._rss_id("CFPB", "https://x/post/2")
        assert a != b

    def test_different_for_different_agencies(self):
        a = reg._rss_id("CFPB", "https://x/p")
        b = reg._rss_id("OCC", "https://x/p")
        assert a != b

    def test_length_is_16(self):
        # SHA256[:16] — 16 hex chars.
        assert len(reg._rss_id("CFPB", "https://x/p")) == 16


# ─── _rss_pub_date ────────────────────────────────────────────────────────────
class _FakeEntry(dict):
    """feedparser entries support both attribute and dict access."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class TestRssPubDate:
    def test_extracts_from_published_parsed(self):
        entry = _FakeEntry(published_parsed=(2026, 5, 28, 10, 0, 0, 0, 0, 0))
        assert reg._rss_pub_date(entry) == "2026-05-28"

    def test_falls_back_to_updated_parsed(self):
        entry = _FakeEntry(updated_parsed=(2026, 3, 15, 9, 0, 0, 0, 0, 0))
        assert reg._rss_pub_date(entry) == "2026-03-15"

    def test_falls_back_to_raw_published_string(self):
        entry = _FakeEntry(published="2026-04-01T12:00:00Z")
        assert reg._rss_pub_date(entry) == "2026-04-01"

    def test_no_date_returns_none(self):
        entry = _FakeEntry()
        assert reg._rss_pub_date(entry) is None


# ─── _build_user_prompt ───────────────────────────────────────────────────────
class TestBuildUserPrompt:
    def test_includes_each_action_id_and_title(self):
        batch = [
            {"id": "a1", "agency": "CFPB", "action_type": "RULE",
             "title": "First", "abstract": "abstract one"},
            {"id": "a2", "agency": "OCC", "action_type": "NOTICE",
             "title": "Second", "abstract": "abstract two"},
        ]
        prompt = reg._build_user_prompt(batch)
        assert "ID:a1" in prompt
        assert "ID:a2" in prompt
        assert "TITLE:First" in prompt
        assert "AGENCY:CFPB" in prompt

    def test_truncates_abstract_to_300_chars(self):
        long_text = "x" * 500
        batch = [{"id": "a", "agency": "X", "action_type": "RULE",
                  "title": "t", "abstract": long_text}]
        prompt = reg._build_user_prompt(batch)
        # Only the first 300 chars of the abstract should appear.
        assert "x" * 300 in prompt
        assert "x" * 301 not in prompt

    def test_handles_missing_abstract(self):
        batch = [{"id": "a", "agency": "X", "action_type": "RULE",
                  "title": "t"}]
        # Should not raise — abstract defaults to "".
        prompt = reg._build_user_prompt(batch)
        assert "ID:a" in prompt


# ─── fetch_regulatory_actions — Federal Register path ────────────────────────
def _fr_response(docs: list[dict]) -> dict:
    return {"results": docs}


class TestFetchRegulatoryActions:
    def test_persists_federal_register_documents(
        self, fresh_db, mocked_responses, monkeypatch
    ):
        # Override the YAML config with a tight set of agencies + no RSS feeds.
        monkeypatch.setattr(reg, "load_data_sources", lambda: {
            "regulatory": {
                "federal_register_api":
                    "https://www.federalregister.gov/api/v1/documents.json",
                "fr_agencies": [
                    {"slug": "consumer-financial-protection-bureau",
                     "short": "CFPB"},
                ],
                "agency_rss": {},
            }
        })
        fr_url = "https://www.federalregister.gov/api/v1/documents.json"
        mocked_responses.get(
            fr_url, status=200, json=_fr_response([
                {
                    "document_number": "2026-12345",
                    "title": "Prohibition on Junk Fees",
                    "abstract": "CFPB proposes a rule to ban hidden fees.",
                    "publication_date": "2026-05-20",
                    "effective_on": "2026-08-01",
                    "comments_close_on": "2026-07-15",
                    "docket_ids": ["CFPB-2026-0001"],
                    "cfr_references": [{"title": 12, "part": 1026}],
                    "html_url": "https://federalregister.gov/d/2026-12345",
                    "pdf_url": "https://federalregister.gov/d/2026-12345.pdf",
                    "type": "PROPOSED_RULE",
                }
            ]),
        )

        n = reg.fetch_regulatory_actions(days_back=30)
        assert n == 1

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM regulatory_actions WHERE id='2026-12345'"
            ).fetchone()
        assert row is not None
        assert row["agency"] == "CFPB"
        assert row["action_type"] == "PROPOSED_RULE"
        assert row["title"] == "Prohibition on Junk Fees"
        assert row["publication_date"] == "2026-05-20"
        assert row["html_url"] == "https://federalregister.gov/d/2026-12345"
        # CFR refs are flattened to "12 CFR 1026".
        assert json.loads(row["cfr_references"]) == ["12 CFR 1026"]
        assert json.loads(row["docket_id"]) == ["CFPB-2026-0001"]
        # Scoring fields stay NULL until SCORE is pressed.
        assert row["relevance_score"] is None
        assert row["relevance_tags"] is None

    def test_documents_without_doc_number_skipped(
        self, fresh_db, mocked_responses, monkeypatch
    ):
        monkeypatch.setattr(reg, "load_data_sources", lambda: {
            "regulatory": {
                "federal_register_api":
                    "https://www.federalregister.gov/api/v1/documents.json",
                "fr_agencies": [{"slug": "x", "short": "X"}],
                "agency_rss": {},
            }
        })
        mocked_responses.get(
            "https://www.federalregister.gov/api/v1/documents.json",
            status=200, json=_fr_response([
                {"document_number": "", "title": "Nameless"},
                {"document_number": "good-1", "title": "Real", "type": "RULE"},
            ]),
        )
        assert reg.fetch_regulatory_actions() == 1
        with db.get_conn() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM regulatory_actions"
            )]
        assert ids == ["good-1"]

    def test_federal_register_network_error_yields_zero(
        self, fresh_db, mocked_responses, monkeypatch
    ):
        monkeypatch.setattr(reg, "load_data_sources", lambda: {
            "regulatory": {
                "federal_register_api":
                    "https://www.federalregister.gov/api/v1/documents.json",
                "fr_agencies": [{"slug": "x", "short": "X"}],
                "agency_rss": {},
            }
        })
        mocked_responses.get(
            "https://www.federalregister.gov/api/v1/documents.json",
            status=500,
        )
        assert reg.fetch_regulatory_actions() == 0

    def test_no_agencies_and_no_rss_yields_zero(self, fresh_db, monkeypatch):
        monkeypatch.setattr(reg, "load_data_sources", lambda: {
            "regulatory": {
                "federal_register_api": "https://x",
                "fr_agencies": [], "agency_rss": {},
            }
        })
        assert reg.fetch_regulatory_actions() == 0

    def test_dedupes_existing_rows(
        self, fresh_db, mocked_responses, monkeypatch
    ):
        monkeypatch.setattr(reg, "load_data_sources", lambda: {
            "regulatory": {
                "federal_register_api":
                    "https://www.federalregister.gov/api/v1/documents.json",
                "fr_agencies": [{"slug": "x", "short": "X"}],
                "agency_rss": {},
            }
        })
        # Pre-insert the row.
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO regulatory_actions(id, agency, action_type, "
                "title, fetched_at) VALUES('dup1', 'X', 'RULE', 'old', ?)",
                (NOW,),
            )
        mocked_responses.get(
            "https://www.federalregister.gov/api/v1/documents.json",
            status=200, json=_fr_response([
                {"document_number": "dup1", "title": "new title",
                 "type": "RULE"},
            ]),
        )
        # rowcount of INSERT OR IGNORE on duplicate is 0.
        n = reg.fetch_regulatory_actions()
        assert n == 0
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT title FROM regulatory_actions WHERE id='dup1'"
            ).fetchone()
        # Original row preserved.
        assert row["title"] == "old"


# ─── fetch_regulatory_actions — RSS path ─────────────────────────────────────
class TestFetchRegulatoryActionsRss:
    def test_persists_rss_entries(self, fresh_db, monkeypatch):
        monkeypatch.setattr(reg, "load_data_sources", lambda: {
            "regulatory": {
                "federal_register_api": "https://x",
                "fr_agencies": [],
                "agency_rss": {"CFPB": "https://cfpb.example/rss"},
            }
        })

        # Build a fake feedparser response with two entries.
        class _FakeFeed:
            entries = [
                _FakeEntry(
                    title="CFPB Announces Action",
                    link="https://cfpb.example/news/1",
                    summary="Detail of the action",
                    published_parsed=(2026, 5, 27, 9, 0, 0, 0, 0, 0),
                ),
                _FakeEntry(
                    title="No Link Entry",
                    link="",  # link missing → must be skipped
                    summary="ignored",
                ),
            ]

        monkeypatch.setattr(reg.feedparser, "parse", lambda *a, **kw: _FakeFeed())

        n = reg.fetch_regulatory_actions()
        assert n == 1
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM regulatory_actions"
            ).fetchone()
        assert row["agency"] == "CFPB"
        assert row["action_type"] == "PRESS_RELEASE"
        assert row["title"] == "CFPB Announces Action"
        assert row["html_url"] == "https://cfpb.example/news/1"
        assert row["publication_date"] == "2026-05-27"


# ─── score_regulatory_actions ─────────────────────────────────────────────────
def _seed_unscored(id_: str, agency: str = "CFPB", title: str = "t",
                   pub: str = "2026-05-20") -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO regulatory_actions(id, agency, action_type, title, "
            "publication_date, fetched_at) VALUES(?, ?, 'RULE', ?, ?, ?)",
            (id_, agency, title, pub, NOW),
        )


class TestScoreRegulatoryActions:
    def test_no_unscored_returns_zero(self, fresh_db, mock_anthropic, monkeypatch):
        # Reset the cached client so the conftest stub is picked up.
        monkeypatch.setattr(reg, "_claude_client", None)
        assert reg.score_regulatory_actions() == 0

    def test_persists_score_and_tags(
        self, fresh_db, mock_anthropic, monkeypatch
    ):
        _seed_unscored("a1", title="ABS rule")
        _seed_unscored("a2", title="Unrelated tax thing")
        monkeypatch.setattr(reg, "_claude_client", None)
        mock_anthropic.next_text(json.dumps([
            {"id": "a1", "score": 5, "tags": ["abs_securitization"]},
            {"id": "a2", "score": 2, "tags": ["enforcement"]},
        ]))

        n = reg.score_regulatory_actions()
        assert n == 2
        with db.get_conn() as conn:
            rows = {
                r["id"]: (r["relevance_score"], json.loads(r["relevance_tags"]))
                for r in conn.execute(
                    "SELECT id, relevance_score, relevance_tags "
                    "FROM regulatory_actions"
                )
            }
        assert rows["a1"] == (5, ["abs_securitization"])
        assert rows["a2"] == (2, ["enforcement"])

    def test_invalid_score_is_silently_dropped(
        self, fresh_db, mock_anthropic, monkeypatch
    ):
        _seed_unscored("a1")
        _seed_unscored("a2")
        monkeypatch.setattr(reg, "_claude_client", None)
        mock_anthropic.next_text(json.dumps([
            {"id": "a1", "score": 7, "tags": []},   # out of 1-5 range
            {"id": "a2", "score": 3, "tags": ["x"]},
        ]))
        n = reg.score_regulatory_actions()
        assert n == 1
        with db.get_conn() as conn:
            scored_ids = [r[0] for r in conn.execute(
                "SELECT id FROM regulatory_actions WHERE relevance_score IS NOT NULL"
            )]
        assert scored_ids == ["a2"]

    def test_strips_markdown_fence_around_json(
        self, fresh_db, mock_anthropic, monkeypatch
    ):
        _seed_unscored("a1")
        monkeypatch.setattr(reg, "_claude_client", None)
        # Claude sometimes returns a ```json ...``` fenced block.
        mock_anthropic.next_text(
            '```json\n[{"id": "a1", "score": 4, "tags": ["bank_lending"]}]\n```'
        )
        n = reg.score_regulatory_actions()
        assert n == 1

    def test_malformed_json_is_caught(
        self, fresh_db, mock_anthropic, monkeypatch
    ):
        _seed_unscored("a1")
        monkeypatch.setattr(reg, "_claude_client", None)
        mock_anthropic.next_text("not JSON at all")
        # Should not raise, returns 0 because the batch failed.
        assert reg.score_regulatory_actions() == 0

    def test_skips_when_no_api_key(self, fresh_db, monkeypatch):
        _seed_unscored("a1")
        from config import settings
        original = settings.ANTHROPIC_API_KEY
        settings.ANTHROPIC_API_KEY = ""
        try:
            assert reg.score_regulatory_actions() == 0
        finally:
            settings.ANTHROPIC_API_KEY = original


# ─── get_regulatory_actions ──────────────────────────────────────────────────
def _seed_action(id_: str, agency: str, action_type: str,
                 pub: str, score=None) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO regulatory_actions(id, agency, action_type, title, "
            "publication_date, relevance_score, fetched_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (id_, agency, action_type, f"title-{id_}", pub, score, NOW),
        )


class TestGetRegulatoryActions:
    def test_empty_returns_empty_list(self, fresh_db):
        assert reg.get_regulatory_actions() == []

    def test_returns_unscored_rows(self, fresh_db):
        today = datetime.now().strftime("%Y-%m-%d")
        _seed_action("a1", "CFPB", "RULE", today, score=None)
        out = reg.get_regulatory_actions(min_score=4)
        # NULL score → should still be returned regardless of min_score.
        assert {r["id"] for r in out} == {"a1"}

    def test_filters_below_min_score(self, fresh_db):
        today = datetime.now().strftime("%Y-%m-%d")
        _seed_action("low", "CFPB", "RULE", today, score=2)
        _seed_action("high", "CFPB", "RULE", today, score=5)
        out = reg.get_regulatory_actions(min_score=4)
        assert {r["id"] for r in out} == {"high"}

    def test_filters_by_agency(self, fresh_db):
        today = datetime.now().strftime("%Y-%m-%d")
        _seed_action("c1", "CFPB", "RULE", today, score=5)
        _seed_action("o1", "OCC", "RULE", today, score=5)
        out = reg.get_regulatory_actions(agency="CFPB")
        assert {r["id"] for r in out} == {"c1"}

    def test_filters_by_action_type(self, fresh_db):
        today = datetime.now().strftime("%Y-%m-%d")
        _seed_action("r1", "CFPB", "RULE", today, score=5)
        _seed_action("p1", "CFPB", "PRESS_RELEASE", today, score=5)
        out = reg.get_regulatory_actions(action_type="PRESS_RELEASE")
        assert {r["id"] for r in out} == {"p1"}

    def test_filters_outside_days_back_window(self, fresh_db):
        recent = datetime.now().strftime("%Y-%m-%d")
        old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        _seed_action("recent", "CFPB", "RULE", recent, score=5)
        _seed_action("old", "CFPB", "RULE", old, score=5)
        out = reg.get_regulatory_actions(days_back=90)
        assert {r["id"] for r in out} == {"recent"}

    def test_null_publication_date_still_included(self, fresh_db):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO regulatory_actions(id, agency, action_type, "
                "title, publication_date, fetched_at) "
                "VALUES('np', 'CFPB', 'RULE', 't', NULL, ?)",
                (NOW,),
            )
        out = reg.get_regulatory_actions()
        assert {r["id"] for r in out} == {"np"}

    def test_limit_caps_results(self, fresh_db):
        today = datetime.now().strftime("%Y-%m-%d")
        for i in range(5):
            _seed_action(f"a{i}", "CFPB", "RULE", today, score=5)
        out = reg.get_regulatory_actions(limit=2)
        assert len(out) == 2
