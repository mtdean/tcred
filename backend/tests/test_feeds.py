"""Cover data/feeds.py — RSS/Atom fetcher, health checker, source slugging.

Strategy:
  * Pure helpers (_hash_url, derive_source_type, strip_html) get table-driven tests.
  * Async fetch paths run against `mocked_aiohttp` so no real network calls fire.
  * Persistence is verified by querying the test DB after fetch_all_feeds().
  * Failure paths (HTTP 500, missing URL, network exception) must return 0 cleanly.
"""

from __future__ import annotations

import pytest

from cache import db
from data import feeds as f


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sample Feed</title>
    <link>https://example.com</link>
    <description>desc</description>
    <item>
      <title>Headline One</title>
      <link>https://example.com/one</link>
      <description><![CDATA[<p>Some &amp; <b>HTML</b> snippet</p>]]></description>
      <pubDate>Wed, 28 May 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Headline Two</title>
      <link>https://example.com/two</link>
      <description>Plain text snippet</description>
      <pubDate>Thu, 29 May 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


# ─── Pure helpers ─────────────────────────────────────────────────────────────
class TestHashUrl:
    def test_hash_is_16_hex_chars(self):
        out = f._hash_url("https://example.com/path")
        assert len(out) == 16
        assert all(c in "0123456789abcdef" for c in out)

    def test_hash_is_deterministic(self):
        assert f._hash_url("https://x/1") == f._hash_url("https://x/1")

    def test_hash_differs_per_url(self):
        assert f._hash_url("https://x/1") != f._hash_url("https://x/2")


class TestDeriveSourceType:
    @pytest.mark.parametrize("feed_name, kind, expected", [
        ("Bloomberg Markets", "news", "bloomberg"),
        ("WSJ Markets", "news", "wsj"),
        ("FT Alphaville", "news", "ft"),
        ("Financial Times Lex", "news", "ft"),
        ("MarketWatch Top Stories", "news", "marketwatch"),
        ("CNBC Markets", "news", "cnbc"),
        ("NYT Business", "news", "nyt"),
        ("Reuters Finance", "news", "reuters"),
        ("Securitization & ABS News", "news", "news"),       # no prefix → fallback
        ("Matt Levine's Money Stuff", "letter", "letter"),    # unmatched newsletter
        ("Bloomberg Markets", "letter", "letter"),            # letter wins over publisher
        ("Apollo Academy (Torsten Slok)", "letter", "research"),   # research sub-group
        ("Fed Guy (Joseph Wang)", "letter", "research"),
        ("Liberty Street Economics (NY Fed)", "letter", "research"),
        ("Petition (Restructuring)", "letter", "restructuring"),   # distressed sub-group
        ("Wolf Street", "letter", "restructuring"),
        ("The Diff (Byrne Hobart)", "letter", "letter"),     # newsletter, no sub-group
    ])
    def test_mapping(self, feed_name, kind, expected):
        assert f.derive_source_type(feed_name, kind) == expected


class TestStripHtml:
    @pytest.mark.parametrize("text, expected", [
        ("<p>Hello <b>world</b></p>", "Hello world"),
        ("Some &amp; entity &#39;quote&#39;", "Some & entity 'quote'"),
        ("  lots   of\nwhitespace\t", "lots of whitespace"),
        ("", ""),
        (None, ""),
        ("<a href='x'>link</a> text", "link text"),
        # Style/script bodies are code, not prose — must vanish entirely
        # (email newsletters embed huge CSS blocks).
        ("<style type='text/css'>.a { color: red; }</style><p>real text</p>", "real text"),
        ("<script>var x = 1;</script>prose", "prose"),
        ("<STYLE>.b{x:y}</STYLE>keep", "keep"),
        ("<!-- hidden preheader -->visible", "visible"),
        ("<style>.a{}</style>one<style>.b{}</style>two", "one two"),
    ])
    def test_strips_tags_and_collapses_ws(self, text, expected):
        assert f.strip_html(text) == expected


class TestHtmlToText:
    @pytest.mark.parametrize("text, expected", [
        # Paragraph structure survives as blank lines.
        ("<p>one</p><p>two</p>", "one\n\ntwo"),
        ("line<br>break", "line\n\nbreak"),
        ("<div>a</div><div>b</div>", "a\n\nb"),
        # Inline tags don't break paragraphs.
        ("<p>a <b>bold</b> word</p>", "a bold word"),
        # Style bodies vanish; entities decode.
        ("<style>.x{}</style><p>M&amp;A</p>", "M&A"),
        # Runs of breaks collapse to one blank line.
        ("<p>a</p><br><br><br><p>b</p>", "a\n\nb"),
        ("", ""),
        (None, ""),
    ])
    def test_structure(self, text, expected):
        assert f.html_to_text(text) == expected


# ─── extract_full_text ───────────────────────────────────────────────────────
def _rss_with_content(body_html: str) -> str:
    """One-item RSS feed carrying a <content:encoded> full body."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Full Content Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Long Read</title>
      <link>https://example.com/long</link>
      <description>Short teaser only</description>
      <content:encoded><![CDATA[{body_html}]]></content:encoded>
      <pubDate>Wed, 28 May 2026 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


class TestExtractFullText:
    def test_none_when_entry_has_no_content(self):
        class E:
            pass
        assert f.extract_full_text(E()) is None

    def test_none_when_body_below_threshold(self):
        class E:
            content = [{"value": "<p>too short</p>"}]
        assert f.extract_full_text(E()) is None

    def test_strips_html_and_returns_body(self):
        long_html = "<p>" + "word " * 400 + "</p>"

        class E:
            content = [{"value": long_html}]
        out = f.extract_full_text(E())
        assert out is not None
        assert "<" not in out
        assert out.startswith("word word")

    def test_picks_longest_content_block(self):
        long_body = "long " * 400

        class E:
            content = [{"value": "tiny"}, {"value": long_body}]
        out = f.extract_full_text(E())
        assert out is not None and out.startswith("long long")

    def test_caps_at_max_chars(self):
        class E:
            content = [{"value": "a" * (f.MAX_FULL_TEXT_CHARS + 10_000)}]
        out = f.extract_full_text(E())
        assert out is not None and len(out) == f.MAX_FULL_TEXT_CHARS

    async def test_fetch_feed_stores_content_text(self, mocked_aiohttp):
        url = "https://example.com/fullrss"
        body = "<p>" + "substantial newsletter prose " * 60 + "</p>"
        mocked_aiohttp.get(url, body=_rss_with_content(body), status=200)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            articles = await f._fetch_feed(
                session,
                {"name": "Net Interest (Marc Rubinstein)", "url": url, "category": "credit"},
                kind="letter",
            )

        assert len(articles) == 1
        assert articles[0]["content_text"] is not None
        assert "substantial newsletter prose" in articles[0]["content_text"]
        # Snippet still comes from the (short) description.
        assert articles[0]["snippet"] == "Short teaser only"

    async def test_fetch_feed_headline_only_has_no_content(self, mocked_aiohttp):
        url = "https://example.com/rss"
        mocked_aiohttp.get(url, body=SAMPLE_RSS, status=200)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            articles = await f._fetch_feed(
                session,
                {"name": "Bloomberg Markets", "url": url, "category": "macro"},
                kind="news",
            )

        assert all(a["content_text"] is None for a in articles)


# ─── _parse_date ─────────────────────────────────────────────────────────────
class TestParseDate:
    def test_uses_published_parsed_when_present(self):
        class E:
            published_parsed = (2026, 5, 28, 12, 0, 0, 0, 0, 0)
        assert f._parse_date(E()) == "2026-05-28T12:00:00+00:00"

    def test_falls_back_to_updated_parsed(self):
        class E:
            published_parsed = None
            updated_parsed = (2026, 5, 29, 8, 0, 0, 0, 0, 0)
        assert f._parse_date(E()) == "2026-05-29T08:00:00+00:00"

    def test_returns_none_when_both_missing(self):
        class E:
            pass
        assert f._parse_date(E()) is None


# ─── _fetch_feed ─────────────────────────────────────────────────────────────
class TestFetchFeed:
    async def test_parses_two_items_with_derived_source_type(self, mocked_aiohttp):
        url = "https://example.com/rss"
        mocked_aiohttp.get(url, body=SAMPLE_RSS, status=200)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            articles = await f._fetch_feed(
                session,
                {"name": "Bloomberg Markets", "url": url, "category": "macro"},
                kind="news",
            )

        assert len(articles) == 2
        a1, a2 = articles
        assert a1["title"] == "Headline One"
        assert a1["url"] == "https://example.com/one"
        # HTML in the description should be stripped.
        assert "<" not in a1["snippet"]
        assert "&" not in a1["snippet"] or " & " in a1["snippet"]
        assert a1["feed_name"] == "Bloomberg Markets"
        assert a1["feed_category"] == "macro"
        assert a1["source_type"] == "bloomberg"  # derived from name
        assert a1["published_at"] == "2026-05-28T12:00:00+00:00"
        # id is the URL hash
        assert a1["id"] == f._hash_url("https://example.com/one")

    async def test_kind_letter_overrides_source_type(self, mocked_aiohttp):
        url = "https://example.com/letter"
        mocked_aiohttp.get(url, body=SAMPLE_RSS, status=200)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            articles = await f._fetch_feed(
                session,
                {"name": "Bloomberg Briefing", "url": url, "category": "macro"},
                kind="letter",
            )
        assert all(a["source_type"] == "letter" for a in articles)

    async def test_missing_url_returns_empty(self, mocked_aiohttp):
        # No URL configured → returns [] without making any network call.
        import aiohttp
        async with aiohttp.ClientSession() as session:
            for empty in ("", "NEEDS_URL"):
                articles = await f._fetch_feed(
                    session,
                    {"name": "Pending Feed", "url": empty, "category": "macro"},
                )
                assert articles == []

    async def test_http_error_returns_empty(self, mocked_aiohttp):
        url = "https://example.com/broken"
        mocked_aiohttp.get(url, status=500)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            articles = await f._fetch_feed(
                session,
                {"name": "Broken Feed", "url": url, "category": "macro"},
            )
        assert articles == []

    async def test_network_exception_returns_empty(self, mocked_aiohttp):
        url = "https://example.com/timeout"
        mocked_aiohttp.get(url, exception=ConnectionError("boom"))

        import aiohttp
        async with aiohttp.ClientSession() as session:
            articles = await f._fetch_feed(
                session,
                {"name": "Timeout Feed", "url": url, "category": "macro"},
            )
        assert articles == []

    async def test_caps_at_20_entries(self, mocked_aiohttp):
        # Build a feed with 25 items and confirm only the first 20 are returned.
        items = "".join(
            f"<item><title>T{i}</title><link>https://x/{i}</link></item>"
            for i in range(25)
        )
        xml = (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
            f"{items}</channel></rss>"
        )
        url = "https://example.com/big"
        mocked_aiohttp.get(url, body=xml, status=200)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            articles = await f._fetch_feed(
                session, {"name": "X", "url": url, "category": "macro"},
            )
        assert len(articles) == 20

    async def test_skips_entries_without_link(self, mocked_aiohttp):
        xml = (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
            "<item><title>no link</title></item>"
            "<item><title>has link</title><link>https://x/yes</link></item>"
            "</channel></rss>"
        )
        url = "https://example.com/mix"
        mocked_aiohttp.get(url, body=xml, status=200)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            articles = await f._fetch_feed(
                session, {"name": "X", "url": url, "category": "macro"},
            )
        assert len(articles) == 1
        assert articles[0]["title"] == "has link"


# ─── _check_feed_health ──────────────────────────────────────────────────────
class TestCheckFeedHealth:
    async def test_needs_url_short_circuits(self, mocked_aiohttp):
        # No HEAD/GET should fire; result must be flagged needs_url=1, is_live=0.
        import aiohttp
        async with aiohttp.ClientSession() as session:
            row = await f._check_feed_health(
                session,
                {"name": "Pending", "url": "NEEDS_URL", "needs_url": True,
                 "platform": "beehiiv"},
            )
        assert row["needs_url"] == 1
        assert row["is_live"] == 0
        assert row["http_status"] is None
        assert row["platform"] == "beehiiv"

    async def test_head_200_is_live(self, mocked_aiohttp):
        url = "https://example.com/feed"
        mocked_aiohttp.head(url, status=200)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            row = await f._check_feed_health(
                session, {"name": "X", "url": url},
            )
        assert row["is_live"] == 1
        assert row["http_status"] == 200
        assert row["error_msg"] is None
        assert row["needs_url"] == 0
        # platform defaults to 'rss' when caller omits it.
        assert row["platform"] == "rss"

    async def test_head_403_falls_back_to_get_200(self, mocked_aiohttp):
        # CNBC behavior — HEAD denied but GET works.
        url = "https://example.com/cnbc"
        mocked_aiohttp.head(url, status=403)
        mocked_aiohttp.get(url, status=200)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            row = await f._check_feed_health(
                session, {"name": "CNBC", "url": url},
            )
        assert row["is_live"] == 1
        assert row["http_status"] == 200

    async def test_head_and_get_fail_marks_dead(self, mocked_aiohttp):
        url = "https://example.com/dead"
        mocked_aiohttp.head(url, status=500)
        mocked_aiohttp.get(url, status=500)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            row = await f._check_feed_health(
                session, {"name": "Dead", "url": url},
            )
        assert row["is_live"] == 0
        assert row["http_status"] == 500
        assert row["error_msg"] == "HTTP 500"

    async def test_network_exception_marks_dead(self, mocked_aiohttp):
        url = "https://example.com/oops"
        mocked_aiohttp.head(url, exception=ConnectionError("dns"))

        import aiohttp
        async with aiohttp.ClientSession() as session:
            row = await f._check_feed_health(
                session, {"name": "Oops", "url": url},
            )
        assert row["is_live"] == 0
        assert row["http_status"] is None
        assert "dns" in (row["error_msg"] or "")


# ─── run_health_checks (DB-coupled) ──────────────────────────────────────────
class TestRunHealthChecks:
    async def test_persists_rows_and_prunes_stale(
        self, fresh_db, mocked_aiohttp, monkeypatch
    ):
        # Pre-seed an old feed_health row that's no longer in the config — the
        # DELETE branch should sweep it out.
        db.upsert_feed_health({
            "feed_name": "Stale Feed", "url": "https://stale",
            "last_checked": "2026-01-01T00:00:00+00:00",
            "is_live": 1, "http_status": 200, "error_msg": None,
            "needs_url": 0, "platform": "rss",
        })

        cfg = {
            "news_feeds": [
                {"name": "Alpha", "url": "https://alpha.com/rss"},
            ],
            "newsletter_feeds": [
                {"name": "Beta Letter", "url": "NEEDS_URL", "needs_url": True},
            ],
        }
        monkeypatch.setattr(f, "load_feeds", lambda: cfg)
        mocked_aiohttp.head("https://alpha.com/rss", status=200)

        summary = await f.run_health_checks()
        assert summary["total"] == 2
        assert summary["live"] == 1
        assert summary["needs_url"] == 1
        assert summary["dead"] == 0

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT feed_name, is_live FROM feed_health ORDER BY feed_name"
            ).fetchall()
        names = {r["feed_name"] for r in rows}
        # Stale was pruned, configured ones persisted.
        assert names == {"Alpha", "Beta Letter"}


# ─── fetch_all_feeds (DB-coupled) ────────────────────────────────────────────
class TestFetchAllFeeds:
    async def test_articles_land_in_db_with_correct_source_type(
        self, fresh_db, mocked_aiohttp, monkeypatch
    ):
        news_url = "https://news.example/rss"
        letter_url = "https://letter.example/rss"

        # Build two distinct feeds so dedup doesn't clobber identical items.
        news_xml = SAMPLE_RSS.replace("example.com", "news.example")
        letter_xml = SAMPLE_RSS.replace("example.com", "letter.example")

        mocked_aiohttp.get(news_url, body=news_xml, status=200)
        mocked_aiohttp.get(letter_url, body=letter_xml, status=200)

        cfg = {
            "news_feeds": [
                {"name": "Bloomberg Markets", "url": news_url, "category": "macro"},
            ],
            "newsletter_feeds": [
                {"name": "Matt Levine", "url": letter_url, "category": "macro"},
            ],
        }
        monkeypatch.setattr(f, "load_feeds", lambda: cfg)

        n = await f.fetch_all_feeds()
        assert n == 4  # 2 items × 2 feeds

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT feed_name, source_type FROM articles "
                "ORDER BY feed_name, url"
            ).fetchall()
        # Bloomberg → 'bloomberg', newsletter → 'letter'.
        bloomberg = [r for r in rows if r["feed_name"] == "Bloomberg Markets"]
        letter = [r for r in rows if r["feed_name"] == "Matt Levine"]
        assert len(bloomberg) == 2 and all(r["source_type"] == "bloomberg" for r in bloomberg)
        assert len(letter) == 2 and all(r["source_type"] == "letter" for r in letter)

    async def test_empty_config_returns_zero(
        self, fresh_db, mocked_aiohttp, monkeypatch
    ):
        monkeypatch.setattr(
            f, "load_feeds",
            lambda: {"news_feeds": [], "newsletter_feeds": []},
        )
        assert await f.fetch_all_feeds() == 0


# ─── backfill_source_types (DB-coupled) ──────────────────────────────────────
class TestBackfillSourceTypes:
    @staticmethod
    def _seed(conn, id_, feed_name, source_type):
        conn.execute(
            "INSERT INTO articles (id, feed_name, feed_category, title, url, "
            "fetched_at, source_type) VALUES (?,?,?,?,?,?,?)",
            (id_, feed_name, "macro", "t", f"https://x/{id_}",
             "2026-06-15T00:00:00+00:00", source_type),
        )

    def test_reslugs_stale_rows_and_is_idempotent(self, fresh_db, monkeypatch):
        cfg = {
            "news_feeds": [{"name": "Bloomberg Markets", "url": "u"}],
            "newsletter_feeds": [
                {"name": "Apollo Academy (Torsten Slok)", "url": "u"},
                {"name": "Petition (Restructuring)", "url": "u"},
                {"name": "The Diff (Byrne Hobart)", "url": "u"},
            ],
        }
        monkeypatch.setattr(f, "load_feeds", lambda: cfg)

        with db.get_conn() as conn:
            # All seeded with the pre-split 'letter' slug (stale for the first two).
            self._seed(conn, "a", "Apollo Academy (Torsten Slok)", "letter")
            self._seed(conn, "b", "Petition (Restructuring)", "letter")
            self._seed(conn, "c", "The Diff (Byrne Hobart)", "letter")
            self._seed(conn, "d", "Bloomberg Markets", "bloomberg")

        assert f.backfill_source_types() == 2  # only Apollo + Petition change
        assert f.backfill_source_types() == 0  # idempotent — no-op second run

        with db.get_conn() as conn:
            got = {
                r["id"]: r["source_type"]
                for r in conn.execute("SELECT id, source_type FROM articles")
            }
        assert got == {
            "a": "research",
            "b": "restructuring",
            "c": "letter",
            "d": "bloomberg",
        }
