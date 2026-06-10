"""
Characterization tests for data/abs_reparse.py — the Claude-assisted
re-extraction pass over stored abs_new_issues rows.

These lock in CURRENT behavior ahead of a refactor. Do not "fix" behavior
here — update tests only together with an intentional source change.

Known quirks locked in (see notes inline):
  * reparse_all queries the DB for candidate rows BEFORE checking
    ANTHROPIC_API_KEY; with no key it returns {"scanned": 0, ...} even when
    qualifying rows exist (the query was wasted work).
  * reparse_all's `if limit:` treats limit=0 as "no limit".
  * reparse_row reads claude["closing_date"] / ["cutoff_date"] but the
    _REPARSE_PROMPT schema never asks Claude for those fields — dead merge
    keys carried over from the live parser's prompt (abs_reparse.py:330-332).
  * _persist_row writes ANY differing field (not just NULL→value fills);
    the null-preserving guarantee lives entirely in the merge step.
"""

from __future__ import annotations

import pytest

from cache import db
from data import abs_parser as ap
from data import abs_reparse as ar


NOW = "2026-06-10T12:00:00+00:00"
EDGAR_URL = "https://www.sec.gov/Archives/edgar/data/123/doc.htm"


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Point the on-disk HTML cache at a per-test temp dir."""
    d = tmp_path / "abs_424b5"
    d.mkdir()
    monkeypatch.setattr(ar, "HTML_CACHE_DIR", d)
    return d


@pytest.fixture
def claude(mock_anthropic, monkeypatch):
    """mock_anthropic + reset the abs_parser module-level client singleton."""
    monkeypatch.setattr(ap, "_claude_client", None)
    return mock_anthropic


def make_row(**over) -> dict:
    """A dict shaped like an abs_new_issues row (all merge fields present)."""
    base = {
        "id": "row-1",
        "accession_no": "acc-1",
        "edgar_url": EDGAR_URL,
        "filing_date": "2026-06-03",
        "issuer_name": None,
        "asset_class": "auto_loan",
        "closing_date": None,
        "cutoff_date": None,
        "class_name": "Class A-2 Notes",
        "coupon_type": None,
        "coupon_rate": None,
        "floating_index": None,
        "floating_spread_bps": None,
        "wal_years": None,
        "final_payment_date": None,
        "rating_sp": None,
        "rating_moodys": None,
        "rating_kbra": None,
        "rating_fitch": None,
        "benchmark": None,
        "benchmark_rate": None,
        "spread_to_benchmark": None,
        "spread_source": None,
        "implied_yield": None,
        "fetched_at": NOW,
    }
    base.update(over)
    return base


def seed_row(**over) -> dict:
    row = make_row(**over)
    db.upsert_abs_tranche(row)
    return row


def seed_treasury(series_id: str, date: str, value: float) -> None:
    db.upsert_metric({
        "series_id": series_id, "label": series_id, "category": "rates",
        "date": date, "value": value, "fetched_at": NOW,
    })


def db_row(row_id: str) -> dict:
    with db.get_conn() as conn:
        r = conn.execute(
            "SELECT * FROM abs_new_issues WHERE id = ?", (row_id,)
        ).fetchone()
    return dict(r)


# ─── _get_html: disk cache + EDGAR fetch ─────────────────────────────────────
class TestGetHtml:
    def test_cache_hit_serves_from_disk_without_http(self, cache_dir):
        (cache_dir / "acc-1.html").write_text("<html>cached</html>")
        # No responses mock active — a real HTTP attempt would error out.
        assert ar._get_html("acc-1", EDGAR_URL) == "<html>cached</html>"

    def test_cache_miss_fetches_and_writes_cache(self, cache_dir, mocked_responses):
        mocked_responses.get(EDGAR_URL, body="<html>fresh</html>", status=200)
        out = ar._get_html("acc-1", EDGAR_URL)
        assert out == "<html>fresh</html>"
        assert (cache_dir / "acc-1.html").read_text() == "<html>fresh</html>"
        # EDGAR-mandated User-Agent header is sent.
        assert (
            mocked_responses.calls[0].request.headers["User-Agent"]
            == ap.HTML_HEADERS["User-Agent"]
        )

    def test_fetch_failure_returns_none_and_caches_nothing(
        self, cache_dir, mocked_responses
    ):
        # Single attempt, no retry.
        mocked_responses.get(EDGAR_URL, status=500)
        assert ar._get_html("acc-1", EDGAR_URL) is None
        assert len(mocked_responses.calls) == 1
        assert not (cache_dir / "acc-1.html").exists()

    def test_unreadable_cache_falls_through_to_fetch(
        self, cache_dir, mocked_responses
    ):
        path = cache_dir / "acc-1.html"
        path.write_text("<html>cached</html>")
        path.chmod(0o000)  # is_file() True but read_text raises
        mocked_responses.get(EDGAR_URL, body="<html>fetched</html>", status=200)
        try:
            out = ar._get_html("acc-1", EDGAR_URL)
        finally:
            path.chmod(0o600)
        assert out == "<html>fetched</html>"

    def test_cache_write_failure_still_returns_html(
        self, cache_dir, mocked_responses
    ):
        mocked_responses.get(EDGAR_URL, body="<html>doc</html>", status=200)
        cache_dir.chmod(0o500)  # read/execute only — write_text raises
        try:
            out = ar._get_html("acc-1", EDGAR_URL)
        finally:
            cache_dir.chmod(0o700)
        assert out == "<html>doc</html>"

    def test_accession_no_is_sanitized_for_filename(self, cache_dir):
        # '/' and ':' collapse to '_' — cache hit served under the safe name.
        (cache_dir / "0001234_26_000001.html").write_text("x")
        assert ar._get_html("0001234/26:000001", EDGAR_URL) == "x"


class TestCachePathSanitation:
    def test_unsafe_chars_become_underscores(self):
        assert ar._cache_path("0001234/26:000001").name == "0001234_26_000001.html"

    def test_dash_and_alnum_preserved(self):
        assert ar._cache_path("0001234567-26-000001").name == (
            "0001234567-26-000001.html"
        )


# ─── _build_focused_excerpt ──────────────────────────────────────────────────
class TestBuildFocusedExcerpt:
    def test_short_text_is_one_cover_chunk(self):
        out = ar._build_focused_excerpt("hello world")
        assert out == "--- chars 0–11 ---\nhello world"

    def test_long_text_without_anchors_is_cover_only(self):
        text = "x" * 50_000
        out = ar._build_focused_excerpt(text)
        assert out.startswith("--- chars 0–30000 ---\n")
        assert len(out) == len("--- chars 0–30000 ---\n") + 30_000

    def test_anchor_past_cover_adds_window(self):
        # Anchor pattern requires a digit within 80 chars of the phrase —
        # bare risk-factor boilerplate ("may extend the weighted average
        # life of the notes.") does NOT anchor.
        pad = "x" * 40_000
        anchor = "weighted average life of 2.50 years"
        text = pad + anchor + "y" * 20_000
        out = ar._build_focused_excerpt(text)
        parts = out.split("\n\n")
        assert len(parts) == 2
        m_start = 40_000  # anchor match starts here
        # Window: 2000 chars before match start, 5000 after match end.
        # Match end = position of the digit '2' + 1 (regex ends at first \d).
        match_end = text.index("2.50") + 1
        expected_start = m_start - 2_000
        expected_end = match_end + 5_000
        assert parts[1].startswith(
            f"--- chars {expected_start}–{expected_end} ---\n"
        )

    def test_boilerplate_wal_mention_without_digit_does_not_anchor(self):
        text = (
            "x" * 40_000
            + "delays may extend the weighted average life of the notes."
            + "y" * 20_000
        )
        out = ar._build_focused_excerpt(text)
        assert out.count("--- chars") == 1  # cover only

    def test_overlapping_anchor_windows_are_merged(self):
        pad = "x" * 40_000
        text = (
            pad
            + "weighted average life of 2.50 years ... "
            + "pricing date of June 3"
            + "y" * 20_000
        )
        out = ar._build_focused_excerpt(text)
        # Two anchors within 7K of each other merge into ONE window.
        assert out.count("--- chars") == 2

    def test_excerpt_cap_truncates_chunks(self, monkeypatch):
        # The 90K cap is unreachable with real constants (30K cover + ≤3
        # ~7K windows ≈ 51K max); shrink the cap to exercise the budget path.
        monkeypatch.setattr(ar, "_REPARSE_EXCERPT_CHARS", 10)
        out = ar._build_focused_excerpt("a" * 50_000)
        assert out == "--- chars 0–10 ---\n" + "a" * 10


# ─── _extract_json_object ────────────────────────────────────────────────────
class TestExtractJsonObject:
    def test_plain_object(self):
        assert ar._extract_json_object('{"a": 1}') == '{"a": 1}'

    def test_preamble_and_trailer_stripped(self):
        raw = 'I need to find the table. {"a": 1} Hope that helps!'
        assert ar._extract_json_object(raw) == '{"a": 1}'

    def test_nested_objects_balanced(self):
        raw = '{"a": {"b": {"c": 1}}} trailing'
        assert ar._extract_json_object(raw) == '{"a": {"b": {"c": 1}}}'

    def test_braces_inside_strings_ignored(self):
        raw = '{"a": "}{", "b": 1}'
        assert ar._extract_json_object(raw) == raw

    def test_escaped_quotes_inside_strings(self):
        raw = '{"a": "say \\"hi\\" {ok}"}'
        assert ar._extract_json_object(raw) == raw

    def test_no_brace_returns_none(self):
        assert ar._extract_json_object("no json here") is None

    def test_unterminated_object_returns_none(self):
        assert ar._extract_json_object('{"a": 1') is None


# ─── _reparse_claude ─────────────────────────────────────────────────────────
class TestReparseClaude:
    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.setattr(ar.settings, "ANTHROPIC_API_KEY", "")
        assert ar._reparse_claude("some text") is None

    def test_plain_json_reply_parsed(self, claude):
        claude.next_text('{"issuer_name": "X Trust", "tranches": []}')
        assert ar._reparse_claude("doc text") == {
            "issuer_name": "X Trust", "tranches": [],
        }

    def test_markdown_fenced_reply_parsed(self, claude):
        claude.next_text('```json\n{"issuer_name": "Y"}\n```')
        assert ar._reparse_claude("doc") == {"issuer_name": "Y"}

    def test_reasoning_preamble_before_json_is_stripped(self, claude):
        claude.next_text('I need to find the pricing table.\n{"tranches": []}')
        assert ar._reparse_claude("doc") == {"tranches": []}

    def test_reply_without_json_object_returns_none(self, claude):
        claude.next_text("Sorry, I cannot find a pricing table.")
        assert ar._reparse_claude("doc") is None

    def test_malformed_json_returns_none(self, claude):
        claude.next_text("{bad json}")
        assert ar._reparse_claude("doc") is None

    def test_client_exception_returns_none(self, monkeypatch):
        class _Boom:
            class messages:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("api down")
        monkeypatch.setattr(ap, "_claude_client", _Boom())
        assert ar._reparse_claude("doc") is None


# ─── _canonical_class / _match_tranche / _merge_nulls ────────────────────────
class TestCanonicalClass:
    @pytest.mark.parametrize("s, expected", [
        ("Class A-2 Notes", "A2"),
        ("Class A-2", "A2"),
        ("A-2", "A2"),
        ("Class B Certificates", "B"),
        ("Collateral Interest", "COLLATERALINTEREST"),
        ("", ""),
        (None, ""),
    ])
    def test_canonical(self, s, expected):
        assert ar._canonical_class(s) == expected


class TestMatchTranche:
    def test_db_wrapper_words_match_bare_claude_name(self):
        tranches = [{"class_name": "A-1"}, {"class_name": "A-2"}]
        assert ar._match_tranche("Class A-2 Notes", tranches) == {
            "class_name": "A-2",
        }

    def test_no_substring_fallback_for_single_letter(self):
        # 'A' must NOT match 'Collateral Interest' (the removed fallback bug).
        tranches = [{"class_name": "Collateral Interest"}]
        assert ar._match_tranche("Class A Notes", tranches) is None

    def test_empty_tranches_returns_none(self):
        assert ar._match_tranche("A-1", []) is None

    def test_empty_target_returns_none(self):
        assert ar._match_tranche("", [{"class_name": "A-1"}]) is None

    def test_tranche_with_null_class_name_skipped(self):
        assert ar._match_tranche("A-1", [{"class_name": None}]) is None


class TestMergeNulls:
    def test_only_null_fields_filled(self):
        stored = {"coupon_rate": 4.5, "wal_years": None, "rating_sp": None}
        new = {"coupon_rate": 9.9, "wal_years": 2.1, "rating_sp": None}
        out = ar._merge_nulls(stored, new, ["coupon_rate", "wal_years", "rating_sp"])
        assert out == {"coupon_rate": 4.5, "wal_years": 2.1, "rating_sp": None}
        # Input is not mutated.
        assert stored["wal_years"] is None


# ─── reparse_row ─────────────────────────────────────────────────────────────
CACHED_HTML = "<html><body><p>Prospectus supplement text.</p></body></html>"


class TestReparseRow:
    def _cache(self, cache_dir, accession_no="acc-1"):
        (cache_dir / f"{accession_no}.html").write_text(CACHED_HTML)

    def test_html_fetch_failure_returns_row_unchanged(
        self, cache_dir, mocked_responses
    ):
        mocked_responses.get(EDGAR_URL, status=500)
        row = make_row()
        assert ar.reparse_row(row) is row  # the very same object back

    def test_claude_failure_returns_row_unchanged(self, cache_dir, claude):
        self._cache(cache_dir)
        claude.next_text("no json")
        row = make_row()
        assert ar.reparse_row(row) is row

    def test_merges_tranche_fields_and_implies_fixed_spread(
        self, fresh_db, cache_dir, claude
    ):
        self._cache(cache_dir)
        seed_treasury("DGS2", "2026-06-01", 4.10)
        claude.next_text("""
        {"issuer_name": "Widget Auto Trust 2026-1",
         "tranches": [
           {"class_name": "A-2", "coupon_type": "fixed", "coupon_rate": 5.10,
            "floating_index": null, "floating_spread_bps": null,
            "wal_years": 2.0}
         ]}
        """)
        merged = ar.reparse_row(make_row())
        assert merged["issuer_name"] == "Widget Auto Trust 2026-1"
        assert merged["coupon_type"] == "fixed"
        assert merged["coupon_rate"] == 5.10
        assert merged["wal_years"] == 2.0
        # Implied spread: (5.10 - 4.10) * 100, matched-tenor UST2Y.
        assert merged["spread_to_benchmark"] == 100.0
        assert merged["benchmark"] == "UST2Y"
        assert merged["benchmark_rate"] == 4.10
        assert merged["spread_source"] == "implied"
        assert merged["implied_yield"] == 5.10

    def test_floating_tranche_spread_is_parsed_source(
        self, fresh_db, cache_dir, claude
    ):
        self._cache(cache_dir)
        claude.next_text("""
        {"issuer_name": null,
         "tranches": [
           {"class_name": "A-2", "coupon_type": "floating", "coupon_rate": null,
            "floating_index": "SOFR", "floating_spread_bps": 85.0,
            "wal_years": 1.0}
         ]}
        """)
        merged = ar.reparse_row(make_row())
        assert merged["spread_to_benchmark"] == 85.0
        assert merged["benchmark"] == "SOFR"
        assert merged["benchmark_rate"] is None
        assert merged["spread_source"] == "parsed"
        assert merged["implied_yield"] is None

    def test_existing_values_are_preserved_over_claude(
        self, fresh_db, cache_dir, claude
    ):
        self._cache(cache_dir)
        claude.next_text("""
        {"issuer_name": "Claude Trust",
         "tranches": [
           {"class_name": "A-2", "coupon_type": "fixed", "coupon_rate": 9.99,
            "wal_years": 5.0}
         ]}
        """)
        row = make_row(issuer_name="Real Issuer", coupon_rate=4.50)
        merged = ar.reparse_row(row)
        assert merged["issuer_name"] == "Real Issuer"   # non-NULL kept
        assert merged["coupon_rate"] == 4.50            # non-NULL kept
        assert merged["wal_years"] == 5.0               # NULL filled

    def test_existing_spread_is_not_overwritten(
        self, fresh_db, cache_dir, claude
    ):
        self._cache(cache_dir)
        seed_treasury("DGS2", "2026-06-01", 4.10)
        claude.next_text("""
        {"tranches": [
           {"class_name": "A-2", "coupon_type": "fixed", "coupon_rate": 5.10,
            "wal_years": 2.0}
         ]}
        """)
        row = make_row(
            spread_to_benchmark=72.0, spread_source="parsed", benchmark="UST2Y",
        )
        merged = ar.reparse_row(row)
        assert merged["spread_to_benchmark"] == 72.0
        assert merged["spread_source"] == "parsed"
        # implied_yield is filled independently of the spread guard.
        assert merged["implied_yield"] == 5.10

    def test_no_matching_tranche_leaves_tranche_fields_null(
        self, fresh_db, cache_dir, claude
    ):
        self._cache(cache_dir)
        claude.next_text("""
        {"issuer_name": "Some Trust",
         "tranches": [{"class_name": "B", "coupon_rate": 6.0, "wal_years": 3.0}]}
        """)
        merged = ar.reparse_row(make_row(class_name="Class A-2 Notes"))
        assert merged["issuer_name"] == "Some Trust"  # deal-level still merged
        assert merged["coupon_rate"] is None
        assert merged["wal_years"] is None
        assert merged["spread_to_benchmark"] is None

    def test_fixed_without_treasury_data_gets_no_spread_but_keeps_yield(
        self, fresh_db, cache_dir, claude
    ):
        # No DGS2 metric seeded → spread None; implied_yield = coupon anyway.
        self._cache(cache_dir)
        claude.next_text("""
        {"tranches": [
           {"class_name": "A-2", "coupon_type": "fixed", "coupon_rate": 5.10,
            "wal_years": 2.0}
         ]}
        """)
        merged = ar.reparse_row(make_row())
        assert merged["spread_to_benchmark"] is None
        assert merged["spread_source"] is None  # never set without a spread
        assert merged["benchmark"] is None
        assert merged["implied_yield"] == 5.10


# ─── _persist_row ────────────────────────────────────────────────────────────
class TestPersistRow:
    def test_no_changes_returns_false(self, fresh_db):
        original = seed_row()
        assert ar._persist_row(dict(original), original) is False

    def test_changed_fields_are_updated(self, fresh_db):
        original = seed_row()
        merged = dict(original)
        merged["wal_years"] = 2.4
        merged["rating_sp"] = "AAA"
        assert ar._persist_row(merged, original) is True
        stored = db_row(original["id"])
        assert stored["wal_years"] == 2.4
        assert stored["rating_sp"] == "AAA"
        assert stored["coupon_rate"] is None  # untouched columns stay

    def test_overwrites_non_null_differences_too(self, fresh_db):
        # NOTE: _persist_row has no NULL-only guard — it writes any diff.
        # The null-preserving behavior lives in reparse_row's merge step.
        original = seed_row(coupon_rate=4.5)
        merged = dict(original)
        merged["coupon_rate"] = 9.9
        assert ar._persist_row(merged, original) is True
        assert db_row(original["id"])["coupon_rate"] == 9.9

    def test_id_is_never_in_set_clause(self, fresh_db):
        original = seed_row()
        merged = dict(original)
        merged["id"] = "evil-new-id"  # diff on id alone → no change persisted
        assert ar._persist_row(merged, original) is False
        assert db_row(original["id"])["id"] == original["id"]


# ─── apply_implied_spread ────────────────────────────────────────────────────
class TestApplyImpliedSpread:
    def test_fills_spread_for_fixed_rows_with_coupon_and_wal(self, fresh_db):
        seed_treasury("DGS2", "2026-06-01", 4.10)
        seed_row(id="r1", coupon_type="fixed", coupon_rate=5.10, wal_years=2.0)
        n = ar.apply_implied_spread()
        assert n == 1
        stored = db_row("r1")
        assert stored["spread_to_benchmark"] == 100.0
        assert stored["benchmark"] == "UST2Y"
        assert stored["benchmark_rate"] == 4.10
        assert stored["spread_source"] == "implied"

    def test_skips_floating_and_already_spread_and_incomplete_rows(
        self, fresh_db
    ):
        seed_treasury("DGS2", "2026-06-01", 4.10)
        seed_row(id="float", accession_no="a1", coupon_type="floating",
                 floating_spread_bps=80.0, wal_years=2.0, coupon_rate=None)
        seed_row(id="has-spread", accession_no="a2", coupon_type="fixed",
                 coupon_rate=5.0, wal_years=2.0, spread_to_benchmark=55.0,
                 spread_source="parsed")
        seed_row(id="no-wal", accession_no="a3", coupon_type="fixed",
                 coupon_rate=5.0)
        seed_row(id="no-coupon", accession_no="a4", coupon_type="fixed",
                 wal_years=2.0)
        assert ar.apply_implied_spread() == 0
        assert db_row("has-spread")["spread_to_benchmark"] == 55.0
        assert db_row("has-spread")["spread_source"] == "parsed"
        assert db_row("float")["spread_to_benchmark"] is None

    def test_row_without_treasury_data_is_skipped_not_counted(self, fresh_db):
        # No DGS series in metrics → _compute_spread yields None → skip.
        seed_row(id="r1", coupon_type="fixed", coupon_rate=5.10, wal_years=2.0)
        assert ar.apply_implied_spread() == 0
        assert db_row("r1")["spread_to_benchmark"] is None

    def test_idempotent_second_run_updates_nothing(self, fresh_db):
        seed_treasury("DGS2", "2026-06-01", 4.10)
        seed_row(id="r1", coupon_type="fixed", coupon_rate=5.10, wal_years=2.0)
        assert ar.apply_implied_spread() == 1
        assert ar.apply_implied_spread() == 0


# ─── reparse_all ─────────────────────────────────────────────────────────────
class TestReparseAll:
    @pytest.fixture
    def spy_reparse(self, monkeypatch):
        """Replace reparse_row with a pass-through spy (no HTTP/Claude)."""
        calls: list[dict] = []

        def _spy(row):
            calls.append(row)
            return row

        monkeypatch.setattr(ar, "reparse_row", _spy)
        return calls

    def test_no_api_key_returns_zeros_without_processing(
        self, fresh_db, spy_reparse, monkeypatch
    ):
        # NOTE(quirk): the candidate query still runs before the key check;
        # qualifying rows exist but the result reports scanned=0.
        seed_row(id="r1")
        monkeypatch.setattr(ar.settings, "ANTHROPIC_API_KEY", "")
        out = ar.reparse_all()
        assert out == {"scanned": 0, "updated": 0, "errors": 0}
        assert spy_reparse == []

    def test_default_selects_only_missing_wal_and_skips_credit_card(
        self, fresh_db, spy_reparse
    ):
        seed_row(id="want-1", accession_no="a1", filing_date="2026-01-02")
        seed_row(id="want-2", accession_no="a2", filing_date="2026-01-01",
                 asset_class=None)  # NULL asset_class still qualifies
        seed_row(id="has-wal", accession_no="a3", wal_years=2.0)
        seed_row(id="cc", accession_no="a4", asset_class="credit_card")
        out = ar.reparse_all()
        assert out == {"scanned": 2, "updated": 0, "errors": 0}
        # Ordered filing_date ASC, id ASC.
        assert [r["id"] for r in spy_reparse] == ["want-2", "want-1"]

    def test_only_missing_wal_false_walks_every_row(self, fresh_db, spy_reparse):
        seed_row(id="r1", accession_no="a1")
        seed_row(id="r2", accession_no="a2", wal_years=2.0)
        seed_row(id="r3", accession_no="a3", asset_class="credit_card")
        out = ar.reparse_all(only_missing_wal=False)
        assert out["scanned"] == 3

    def test_limit_truncates_candidates(self, fresh_db, spy_reparse):
        seed_row(id="r1", accession_no="a1", filing_date="2026-01-01")
        seed_row(id="r2", accession_no="a2", filing_date="2026-01-02")
        out = ar.reparse_all(limit=1)
        assert out["scanned"] == 1
        assert [r["id"] for r in spy_reparse] == ["r1"]

    def test_limit_zero_means_no_limit(self, fresh_db, spy_reparse):
        # NOTE(quirk): `if limit:` — limit=0 is falsy, so it walks everything.
        seed_row(id="r1", accession_no="a1")
        seed_row(id="r2", accession_no="a2")
        assert ar.reparse_all(limit=0)["scanned"] == 2

    def test_updated_counts_persisted_rows(self, fresh_db, monkeypatch):
        seed_row(id="r1", accession_no="a1")
        seed_row(id="r2", accession_no="a2")

        def _fill_one(row):
            if row["id"] == "r1":
                return {**row, "wal_years": 2.5}
            return row

        monkeypatch.setattr(ar, "reparse_row", _fill_one)
        out = ar.reparse_all()
        assert out == {"scanned": 2, "updated": 1, "errors": 0}
        assert db_row("r1")["wal_years"] == 2.5
        assert db_row("r2")["wal_years"] is None

    def test_row_exception_counts_error_and_continues(
        self, fresh_db, monkeypatch
    ):
        seed_row(id="r1", accession_no="a1", filing_date="2026-01-01")
        seed_row(id="r2", accession_no="a2", filing_date="2026-01-02")

        def _boom_then_fill(row):
            if row["id"] == "r1":
                raise ValueError("parse blew up")
            return {**row, "rating_sp": "AAA"}

        monkeypatch.setattr(ar, "reparse_row", _boom_then_fill)
        out = ar.reparse_all()
        assert out == {"scanned": 2, "updated": 1, "errors": 1}
        assert db_row("r2")["rating_sp"] == "AAA"

    def test_empty_db_returns_zeros(self, fresh_db):
        assert ar.reparse_all() == {"scanned": 0, "updated": 0, "errors": 0}
