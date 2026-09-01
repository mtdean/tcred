"""Cover data/article_dedup.py + the /api/articles dedup path."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from cache import db
from data import article_dedup as dedup


# Dynamic so seeded articles always fall inside dedup's 72-hour window.
NOW = datetime.now(timezone.utc).isoformat()


def _seed(id_, title, feed_name="Bloomberg", published_at=None,
          score=4, snippet="x"):
    db.upsert_article({
        "id": id_, "feed_name": feed_name, "feed_category": "macro",
        "title": title, "snippet": snippet, "url": f"https://x/{id_}",
        "published_at": published_at or NOW, "fetched_at": NOW,
    })
    if score is not None:
        db.update_article_relevance(id_, score, "[]")


class TestPickPrimaryPublisherTier:
    @pytest.fixture(autouse=True)
    def tiers(self, monkeypatch):
        from data import feeds
        monkeypatch.setattr(feeds, "load_data_sources", lambda: {
            "publisher_tiers": {
                "trusted": ["WSJ"],
                "junk": ["Stock Titan"],
            }
        })

    def test_trusted_publisher_beats_earlier_junk(self, fresh_db):
        # Junk site "broke" the story an hour earlier — trusted must still win.
        _seed("junk", "Carvana ABS deal prices tight", score=4,
              published_at="2026-09-01T08:00:00+00:00")
        _seed("wsj", "Carvana ABS deal prices tight", score=4,
              published_at="2026-09-01T09:00:00+00:00")
        with db.get_conn() as conn:
            conn.execute("UPDATE articles SET publisher='Stock Titan' WHERE id='junk'")
            conn.execute("UPDATE articles SET publisher='WSJ' WHERE id='wsj'")

        members = [
            {"id": "junk", "relevance_score": 4, "publisher": "Stock Titan",
             "published_at": "2026-09-01T08:00:00+00:00", "fetched_at": NOW},
            {"id": "wsj", "relevance_score": 4, "publisher": "WSJ",
             "published_at": "2026-09-01T09:00:00+00:00", "fetched_at": NOW},
        ]
        assert dedup._pick_primary(members)["id"] == "wsj"

    def test_score_still_outranks_tier(self, fresh_db):
        members = [
            {"id": "junk5", "relevance_score": 5, "publisher": "Stock Titan",
             "published_at": "2026-09-01T08:00:00+00:00", "fetched_at": NOW},
            {"id": "wsj4", "relevance_score": 4, "publisher": "WSJ",
             "published_at": "2026-09-01T09:00:00+00:00", "fetched_at": NOW},
        ]
        assert dedup._pick_primary(members)["id"] == "junk5"

    def test_earliest_wins_within_same_tier(self, fresh_db):
        members = [
            {"id": "later", "relevance_score": 4, "publisher": None,
             "published_at": "2026-09-01T09:00:00+00:00", "fetched_at": NOW},
            {"id": "earlier", "relevance_score": 4, "publisher": None,
             "published_at": "2026-09-01T08:00:00+00:00", "fetched_at": NOW},
        ]
        assert dedup._pick_primary(members)["id"] == "earlier"


# ─── Title normalization ────────────────────────────────────────────────────
class TestNormalizeTitle:
    @pytest.mark.parametrize("raw, expected", [
        ("Fed Holds Rates Steady - Reuters", "fed holds rates steady"),
        ("Fed Holds Rates Steady | WSJ", "fed holds rates steady"),
        ("Fed Holds Rates Steady — Bloomberg", "fed holds rates steady"),
        ("Headline — The New York Times", "headline"),
        ("Already clean headline", "already clean headline"),
        ("X - WSJ - Reuters", "x"),  # strips repeated suffixes
    ])
    def test_strips_publisher_suffix(self, raw, expected):
        assert dedup._normalize_title(raw) == expected

    def test_empty_input(self):
        assert dedup._normalize_title("") == ""


class TestTokens:
    def test_drops_punctuation_and_stop_words(self):
        toks = dedup._tokens("Fed Holds Rates Steady amid Inflation Concerns - Reuters")
        assert "fed" in toks
        assert "rates" in toks
        assert "inflation" in toks
        assert "amid" not in toks  # stop word
        assert "-" not in toks


class TestJaccard:
    def test_full_overlap(self):
        assert dedup._jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_no_overlap(self):
        assert dedup._jaccard({"a"}, {"b"}) == 0.0

    def test_partial(self):
        # {a,b} ∩ {b,c} = {b}; union = {a,b,c}; ⇒ 1/3
        assert dedup._jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)

    def test_empty_set_yields_zero(self):
        assert dedup._jaccard(set(), {"a"}) == 0.0


# ─── Clustering ─────────────────────────────────────────────────────────────
class TestClusterArticles:
    def test_similar_titles_join(self, fresh_db):
        articles = [
            {"id": "a", "title": "Fed Holds Rates Steady Amid Inflation Pressures"},
            {"id": "b", "title": "Fed Holds Rates Steady Amid Inflation Concerns"},
            {"id": "c", "title": "Carvana Files New Subprime Auto ABS Deal"},
        ]
        clusters = dedup._cluster_articles(articles)
        assert len(clusters) == 2
        # The two "Fed Holds Rates" headlines land in one cluster.
        member_sets = [set(c) for c in clusters]
        assert {"a", "b"} in member_sets
        assert {"c"} in member_sets

    def test_dissimilar_titles_stay_separate(self, fresh_db):
        articles = [
            {"id": "a", "title": "Carvana ABS pricing widens"},
            {"id": "b", "title": "Honda issues prime auto deal"},
        ]
        clusters = dedup._cluster_articles(articles)
        assert sorted(sorted(c) for c in clusters) == [["a"], ["b"]]

    def test_empty_tokens_get_solo_cluster(self, fresh_db):
        # Only stop words → empty token set.
        articles = [
            {"id": "a", "title": "the and or"},
            {"id": "b", "title": "the and or"},
        ]
        clusters = dedup._cluster_articles(articles)
        # Both end up solo because there's nothing to compare against.
        assert sorted(sorted(c) for c in clusters) == [["a"], ["b"]]


# ─── Pick primary ───────────────────────────────────────────────────────────
class TestPickPrimary:
    def test_highest_score_wins(self):
        members = [
            {"id": "a", "relevance_score": 3, "published_at": "2026-05-30T01:00:00+00:00"},
            {"id": "b", "relevance_score": 5, "published_at": "2026-05-30T02:00:00+00:00"},
            {"id": "c", "relevance_score": 4, "published_at": "2026-05-30T00:00:00+00:00"},
        ]
        assert dedup._pick_primary(members)["id"] == "b"

    def test_tie_break_earliest_published(self):
        members = [
            {"id": "a", "relevance_score": 5, "published_at": "2026-05-30T02:00:00+00:00"},
            {"id": "b", "relevance_score": 5, "published_at": "2026-05-30T01:00:00+00:00"},
        ]
        assert dedup._pick_primary(members)["id"] == "b"


# ─── End-to-end DB pass ─────────────────────────────────────────────────────
class TestDedupRecentArticles:
    def test_empty_db_returns_zero(self, fresh_db):
        result = dedup.dedup_recent_articles()
        assert result["processed"] == 0
        assert result["duplicates"] == 0

    def test_writes_cluster_id_and_duplicate_of(self, fresh_db):
        _seed("a", "Fed Holds Rates Steady Amid Inflation Pressures",
              feed_name="Reuters", score=5)
        _seed("b", "Fed Holds Rates Steady Amid Inflation Concerns",
              feed_name="WSJ", score=4)
        _seed("c", "Carvana ABS pricing widens sharply",
              feed_name="Bloomberg", score=4)

        result = dedup.dedup_recent_articles()
        assert result["processed"] == 3
        assert result["clusters"] == 2
        assert result["duplicates"] == 1

        with db.get_conn() as conn:
            rows = {r["id"]: dict(r) for r in conn.execute(
                "SELECT id, cluster_id, duplicate_of, deduped_at FROM articles"
            ).fetchall()}
        # a (higher score) is the primary; b is its duplicate.
        assert rows["a"]["duplicate_of"] is None
        assert rows["b"]["duplicate_of"] == "a"
        assert rows["a"]["cluster_id"] == rows["b"]["cluster_id"]
        assert rows["c"]["duplicate_of"] is None
        assert rows["c"]["cluster_id"] != rows["a"]["cluster_id"]
        # deduped_at written for everyone.
        assert all(rows[i]["deduped_at"] for i in ("a", "b", "c"))

    def test_idempotent_rerun(self, fresh_db):
        _seed("a", "Fed Holds Rates Steady Amid Inflation Pressures", score=5)
        _seed("b", "Fed Holds Rates Steady Amid Inflation Concerns", score=4)
        first = dedup.dedup_recent_articles()
        second = dedup.dedup_recent_articles()
        # Counts identical; cluster ids may differ (re-issued) but the
        # grouping holds.
        assert second["clusters"] == first["clusters"]
        assert second["duplicates"] == first["duplicates"]


# ─── annotate_with_sources ──────────────────────────────────────────────────
class TestAnnotate:
    def test_attaches_n_sources_and_other_sources(self, fresh_db):
        _seed("a", "Fed Holds Rates Steady Amid Inflation Pressures",
              feed_name="Reuters", score=5)
        _seed("b", "Fed Holds Rates Steady Amid Inflation Concerns",
              feed_name="WSJ", score=4)
        dedup.dedup_recent_articles()

        with db.get_conn() as conn:
            primaries = [dict(r) for r in conn.execute(
                "SELECT id, feed_name, cluster_id FROM articles WHERE duplicate_of IS NULL"
            ).fetchall()]
        annotated = dedup.annotate_with_sources(primaries)
        ann_a = next(r for r in annotated if r["id"] == "a")
        assert ann_a["n_sources"] == 2
        assert ann_a["other_sources"] == ["WSJ"]

    def test_no_cluster_yields_one_source(self, fresh_db):
        _seed("a", "Unique headline", score=4)
        with db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, feed_name, cluster_id FROM articles"
            ).fetchall()]
        out = dedup.annotate_with_sources(rows)
        assert out[0]["n_sources"] == 1
        assert out[0]["other_sources"] == []


# ─── /api/articles dedup path ───────────────────────────────────────────────
class TestArticlesRouteDedup:
    def test_excludes_duplicates_by_default(self, api_client, fresh_db):
        _seed("a", "Fed Holds Rates Steady Amid Inflation Pressures",
              feed_name="Reuters", score=5)
        _seed("b", "Fed Holds Rates Steady Amid Inflation Concerns",
              feed_name="WSJ", score=4)
        dedup.dedup_recent_articles()

        resp = api_client.get("/api/articles?min_score=4")
        data = resp.json()
        ids = {a["id"] for a in data["items"]}
        assert ids == {"a"}
        primary = next(a for a in data["items"] if a["id"] == "a")
        assert primary["n_sources"] == 2
        assert primary["other_sources"] == ["WSJ"]

    def test_include_duplicates_returns_all(self, api_client, fresh_db):
        _seed("a", "Fed Holds Rates Steady Amid Inflation Pressures",
              feed_name="Reuters", score=5)
        _seed("b", "Fed Holds Rates Steady Amid Inflation Concerns",
              feed_name="WSJ", score=4)
        dedup.dedup_recent_articles()
        resp = api_client.get(
            "/api/articles?min_score=4&include_duplicates=true"
        )
        ids = {a["id"] for a in resp.json()["items"]}
        assert ids == {"a", "b"}
