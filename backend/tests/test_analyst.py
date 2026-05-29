"""
Cover the analyst module: snapshot assembly, briefing generation (with mocked
Anthropic), tool dispatch, chat loop (including a tool_use → tool_result round
trip), and the /api/briefings/* routes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from cache import db
from data import analyst, analyst_tools


NOW = "2026-05-29T12:00:00+00:00"


def _seed_metric(series_id, date, value, label="x", category="rates"):
    db.upsert_metric({
        "series_id": series_id, "label": label, "category": category,
        "date": date, "value": value, "fetched_at": NOW,
    })


def _seed_market(ticker, dates_values):
    for d, v in dates_values:
        _seed_metric(f"mkt_{ticker}", d, v, label=ticker, category="market")


# ─── _latest_and_delta ──────────────────────────────────────────────────────
class TestLatestAndDelta:
    def test_returns_none_when_no_data(self, fresh_db):
        assert analyst._latest_and_delta("MISSING") is None

    def test_computes_3mo_delta(self, fresh_db):
        # ~120 days apart so the prior obs falls outside the 95-day cutoff.
        _seed_metric("X", "2026-01-15", 4.0)
        _seed_metric("X", "2026-05-15", 4.5)
        out = analyst._latest_and_delta("X")
        assert out["latest"] == 4.5
        assert out["as_of"] == "2026-05-15"
        assert out["delta_3mo"] == pytest.approx(0.5)

    def test_delta_none_when_no_prior_obs(self, fresh_db):
        _seed_metric("X", "2026-05-15", 4.5)
        out = analyst._latest_and_delta("X")
        assert out["latest"] == 4.5
        assert out["delta_3mo"] is None


# ─── _market_moves ──────────────────────────────────────────────────────────
class TestMarketMoves:
    def test_one_month_three_month_twelve_month(self, fresh_db):
        # Latest 2026-05-29 at 100; ~1mo ago 95; ~3mo ago 90; ~12mo ago 80
        _seed_market("SPY", [
            ("2025-05-29", 80.0),
            ("2026-02-28", 90.0),
            ("2026-04-29", 95.0),
            ("2026-05-29", 100.0),
        ])
        out = analyst._market_moves("SPY")
        assert out["latest"] == 100.0
        assert out["pct_1m"] == pytest.approx(5.26, abs=0.5)
        assert out["pct_3m"] == pytest.approx(11.11, abs=0.5)
        assert out["pct_12m"] == pytest.approx(25.0, abs=0.5)


# ─── _edgar_recent / _regulatory_recent ─────────────────────────────────────
class TestEdgarAndRegulatory:
    def test_edgar_recent_counts_and_groups(self, fresh_db):
        today = datetime.now(timezone.utc).date().isoformat()
        for cls in ("auto loan", "auto loan", "credit card"):
            db.upsert_edgar_filing({
                "accession_no": f"a-{cls}-{today}-{cls.replace(' ', '_')}-x",
                "company_name": "X", "form_type": "424B5",
                "filed_at": today, "description": "", "url": "u",
                "asset_class": cls, "issuance_type": "debt", "fetched_at": NOW,
            })
        # Ensure unique accession_no for the 3rd row.
        with db.get_conn() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM edgar_filings").fetchone()
        assert rows[0] >= 2  # at minimum

        out = analyst._edgar_recent(days=30)
        assert out["window_days"] == 30
        assert out["total_filings"] >= 2
        cls_counts = {r["asset_class"]: r["n"] for r in out["by_asset_class"]}
        assert "auto loan" in cls_counts

    def test_regulatory_recent_filters_by_score(self, fresh_db):
        today = datetime.now(timezone.utc).date().isoformat()
        with db.get_conn() as conn:
            for i, score in enumerate([5, 3, 1]):
                conn.execute(
                    """INSERT INTO regulatory_actions
                       (id, agency, action_type, title, publication_date,
                        relevance_score, fetched_at)
                       VALUES (?, 'CFPB', 'RULE', 't', ?, ?, ?)""",
                    (f"r{i}", today, score, NOW),
                )
        out = analyst._regulatory_recent(days=30, min_score=3)
        scores = {it["score"] for it in out["items"]}
        assert scores == {5, 3}


# ─── build_snapshot ─────────────────────────────────────────────────────────
class TestBuildSnapshot:
    def test_assembles_expected_top_level_keys(self, fresh_db):
        _seed_metric("RECESSION_RISK_ENSEMBLE", "2026-05-01", 18.0,
                     label="ensemble", category="recession_risk")
        _seed_metric("RECESSION_RISK_ENSEMBLE", "2026-01-01", 16.0,
                     label="ensemble", category="recession_risk")
        snap = analyst.build_snapshot(period_label="2026-05")
        for key in ("period_label", "as_of", "indicators", "market_moves",
                    "abs_spread_changes_recent", "bdc_state", "edgar_recent",
                    "regulatory_recent", "news_digests_recent"):
            assert key in snap
        assert snap["period_label"] == "2026-05"
        assert "RECESSION_RISK_ENSEMBLE" in snap["indicators"]
        assert snap["indicators"]["RECESSION_RISK_ENSEMBLE"]["delta_3mo"] == pytest.approx(2.0)


# ─── _extract_watch_items ───────────────────────────────────────────────────
class TestExtractWatchItems:
    def test_splits_prose_from_json_block(self):
        raw = (
            "Regime is mixed-to-cautious. EBP at 0.45...\n\n"
            "```json\n"
            '{"watch_items": [{"title": "Subprime auto", "severity": "warn", "why": "spreads widening"}]}\n'
            "```\n"
        )
        body, items = analyst._extract_watch_items(raw)
        assert "Regime is mixed-to-cautious" in body
        assert "```json" not in body
        assert items == [{"title": "Subprime auto", "severity": "warn", "why": "spreads widening"}]

    def test_returns_none_items_when_no_json_block(self):
        raw = "Just prose, no JSON."
        body, items = analyst._extract_watch_items(raw)
        assert body == "Just prose, no JSON."
        assert items is None

    def test_returns_none_items_when_json_is_malformed(self):
        raw = "Prose\n\n```json\n{ not valid }\n```"
        body, items = analyst._extract_watch_items(raw)
        assert items is None


# ─── Tool dispatch ──────────────────────────────────────────────────────────
class TestToolDispatch:
    def test_get_indicator_history(self, fresh_db):
        for d, v in [("2025-05-01", 4.0), ("2026-05-01", 4.5)]:
            _seed_metric("DGS10", d, v, label="10y", category="rates")
        out = analyst_tools._tool_get_indicator_history("DGS10", days_back=730)
        assert out["series_id"] == "DGS10"
        assert out["n"] == 2
        assert out["observations"][0]["date"] == "2025-05-01"

    def test_get_abs_spread_series_all_bucket(self, fresh_db):
        db.upsert_abs_tranche({
            "id": "x1", "accession_no": "a1", "edgar_url": "u",
            "filing_date": "2026-05-01", "class_name": "A-2",
            "asset_class": "prime_auto_loan", "parse_confidence": "high",
            "spread_to_benchmark": 60.0, "fetched_at": NOW,
        })
        out = analyst_tools._tool_get_abs_spread_series(
            asset_class="prime_auto_loan", rating_bucket="all",
        )
        assert out["asset_class"] == "prime_auto_loan"
        assert out["n_weeks"] == 1

    def test_get_recent_filings_filters_asset_class(self, fresh_db):
        today = datetime.now(timezone.utc).date().isoformat()
        for i, cls in enumerate(("auto loan", "credit card")):
            db.upsert_edgar_filing({
                "accession_no": f"acc-{i}", "company_name": "X",
                "form_type": "424B5", "filed_at": today, "description": "",
                "url": "u", "asset_class": cls, "issuance_type": "debt",
                "fetched_at": NOW,
            })
        out = analyst_tools._tool_get_recent_filings(asset_class="auto loan")
        assert all(f["asset_class"] == "auto loan" for f in out["filings"])

    def test_get_market_history(self, fresh_db):
        _seed_market("SPY", [("2026-05-28", 500.0), ("2026-05-29", 510.0)])
        out = analyst_tools._tool_get_market_history("SPY", days_back=30)
        assert out["ticker"] == "SPY"
        assert out["n"] == 2
        assert out["observations"][-1]["close"] == 510.0

    def test_search_articles(self, fresh_db):
        db.upsert_article({
            "id": "a1", "feed_name": "Bloomberg", "feed_category": "macro",
            "title": "t", "snippet": "s", "url": "u",
            "published_at": "2026-05-28T10:00:00+00:00", "fetched_at": NOW,
        })
        db.update_article_relevance("a1", 5, "[]")
        out = analyst_tools._tool_search_articles(min_score=4, category="macro")
        assert out["n"] == 1
        assert out["articles"][0]["title"] == "t"


# ─── generate_briefing — mocked Anthropic ───────────────────────────────────
class _StubStreamCtx:
    """Mimics `client.messages.stream(...)` as a context manager that returns
    a stub whose .get_final_message() yields a typed-looking Message."""
    def __init__(self, text: str):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_final_message(self):
        msg = MagicMock()
        msg.content = [MagicMock(type="text", text=self._text)]
        msg.usage = MagicMock(input_tokens=1234, output_tokens=345,
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0)
        msg.stop_reason = "end_turn"
        return msg


@pytest.fixture
def stub_anthropic(monkeypatch):
    """Patch anthropic.Anthropic with a stub that lets the test set the
    response text for .messages.stream() and .messages.create()."""
    state = {"text": "ok", "create_responses": []}

    class _Messages:
        def stream(self, **kwargs):
            return _StubStreamCtx(state["text"])

        def create(self, **kwargs):
            # Each call pops the next planned response; if empty, return a
            # default end_turn with the state text.
            if state["create_responses"]:
                return state["create_responses"].pop(0)
            resp = MagicMock()
            resp.content = [MagicMock(type="text", text=state["text"])]
            resp.usage = MagicMock(input_tokens=10, output_tokens=20,
                                   cache_read_input_tokens=0,
                                   cache_creation_input_tokens=0)
            resp.stop_reason = "end_turn"
            return resp

    class _StubClient:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _StubClient)

    class _Handle:
        def next_briefing_text(self, text: str) -> None:
            state["text"] = text

        def push_create_response(self, response) -> None:
            state["create_responses"].append(response)

    return _Handle()


class TestGenerateBriefing:
    def test_persists_and_returns_briefing(self, fresh_db, stub_anthropic):
        stub_anthropic.next_briefing_text(
            "Regime is cautious. EBP at 0.45 with 3mo widening...\n\n"
            "```json\n"
            '{"watch_items": [{"title": "Subprime auto", "severity": "warn", "why": "spreads widening"}]}\n'
            "```"
        )
        briefing = analyst.generate_briefing(period_label="2026-05")
        assert briefing["period_label"] == "2026-05"
        assert "Regime is cautious" in briefing["briefing_md"]
        assert "```json" not in briefing["briefing_md"]  # stripped
        items = json.loads(briefing["watch_items"])
        assert items[0]["title"] == "Subprime auto"
        assert briefing["input_tokens"] == 1234


# ─── chat_with_briefing — including a tool-use round trip ───────────────────
def _make_briefing_for_chat(fresh_db) -> str:
    """Seed a briefing row and return its id."""
    snap = {"period_label": "2026-05", "indicators": {}}
    bid = "brf_test123"
    db.insert_briefing({
        "id": bid, "period_label": "2026-05", "generated_at": NOW,
        "model": "claude-opus-4-7",
        "briefing_md": "Stable regime. HY OAS 380bps.",
        "watch_items": None,
        "snapshot_json": json.dumps(snap),
    })
    return bid


def _tool_use_response(tool_name: str, tool_input: dict, tool_use_id: str):
    """Build a stub Anthropic response that requests one tool_use."""
    resp = MagicMock()
    tu = MagicMock()
    tu.type = "tool_use"
    tu.name = tool_name
    tu.input = tool_input
    tu.id = tool_use_id
    resp.content = [tu]
    resp.stop_reason = "tool_use"
    resp.usage = MagicMock(input_tokens=10, output_tokens=20,
                           cache_read_input_tokens=0,
                           cache_creation_input_tokens=0)
    return resp


def _end_turn_response(text: str):
    resp = MagicMock()
    tb = MagicMock()
    tb.type = "text"
    tb.text = text
    resp.content = [tb]
    resp.stop_reason = "end_turn"
    resp.usage = MagicMock(input_tokens=100, output_tokens=50,
                           cache_read_input_tokens=80,
                           cache_creation_input_tokens=20)
    return resp


class TestChatWithBriefing:
    def test_direct_answer_without_tool_use(self, fresh_db, stub_anthropic):
        bid = _make_briefing_for_chat(fresh_db)
        stub_anthropic.next_briefing_text("HY OAS is 380bps per the briefing.")
        result = analyst.chat_with_briefing(
            briefing_id=bid, history=[], user_message="Where is HY OAS?",
        )
        assert result["reply"].startswith("HY OAS")
        assert result["tool_calls"] == []
        assert result["usage"]["output_tokens"] > 0

    def test_tool_use_round_trip(self, fresh_db, stub_anthropic):
        bid = _make_briefing_for_chat(fresh_db)
        # Seed real data the tool will return.
        for d, v in [("2025-05-01", 4.0), ("2026-05-01", 4.5)]:
            _seed_metric("DGS10", d, v, label="10y", category="rates")

        # First call: the model emits a tool_use; second call: end_turn.
        stub_anthropic.push_create_response(
            _tool_use_response("get_indicator_history",
                               {"series_id": "DGS10"}, "toolu_abc")
        )
        stub_anthropic.push_create_response(
            _end_turn_response("DGS10 went from 4.0 to 4.5 over the past year.")
        )

        result = analyst.chat_with_briefing(
            briefing_id=bid, history=[], user_message="What's DGS10 doing?",
        )
        assert "4.5" in result["reply"]
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "get_indicator_history"
        assert result["tool_calls"][0]["input"]["series_id"] == "DGS10"

    def test_missing_briefing_raises(self, fresh_db, stub_anthropic):
        with pytest.raises(analyst.ChatError, match="not found"):
            analyst.chat_with_briefing(
                briefing_id="brf_missing", history=[], user_message="hi",
            )


# ─── /api/briefings/* routes ────────────────────────────────────────────────
class TestBriefingRoutes:
    def test_list_briefings_empty(self, api_client, fresh_db):
        resp = api_client.get("/api/briefings")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    def test_latest_returns_404_when_none(self, api_client, fresh_db):
        resp = api_client.get("/api/briefings/latest")
        assert resp.status_code == 404

    def test_get_briefing_404(self, api_client, fresh_db):
        resp = api_client.get("/api/briefings/brf_missing")
        assert resp.status_code == 404

    def test_round_trip(self, api_client, fresh_db):
        bid = _make_briefing_for_chat(fresh_db)
        resp = api_client.get(f"/api/briefings/{bid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == bid
        assert "snapshot" in data
        assert data["briefing_md"].startswith("Stable regime")

        latest = api_client.get("/api/briefings/latest").json()
        assert latest["id"] == bid

    def test_chat_endpoint_404_for_missing_briefing(
        self, api_client, fresh_db, stub_anthropic
    ):
        resp = api_client.post(
            "/api/briefings/brf_missing/chat",
            json={"message": "hi", "history": []},
        )
        assert resp.status_code == 404

    def test_chat_endpoint_returns_reply(
        self, api_client, fresh_db, stub_anthropic
    ):
        bid = _make_briefing_for_chat(fresh_db)
        stub_anthropic.next_briefing_text("Direct answer.")
        resp = api_client.post(
            f"/api/briefings/{bid}/chat",
            json={"message": "tell me more", "history": []},
        )
        assert resp.status_code == 200
        assert resp.json()["reply"] == "Direct answer."

    def test_chat_validates_role(self, api_client, fresh_db):
        bid = _make_briefing_for_chat(fresh_db)
        resp = api_client.post(
            f"/api/briefings/{bid}/chat",
            json={"message": "hi", "history": [{"role": "system", "content": "x"}]},
        )
        assert resp.status_code == 422

    def test_generate_briefing_endpoint(
        self, api_client, fresh_db, stub_anthropic
    ):
        stub_anthropic.next_briefing_text("New briefing body.")
        resp = api_client.post("/api/briefings/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["briefing_md"] == "New briefing body."
