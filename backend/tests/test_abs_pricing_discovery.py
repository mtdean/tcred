"""
Characterization tests for data/abs_pricing.py discovery/fetch paths
(lines ~220-338): EDGAR full-text search with retry, trust-CIK discovery,
trust FWP enumeration, and the end-to-end fetch_abs_pricing flow.

These tests lock in CURRENT behavior ahead of a refactor of the duplicated
HTTP retry logic and naive-vs-UTC datetime handling. Do not "fix" behavior
here — update tests only together with an intentional source change.

Known quirks locked in (see notes inline):
  * _search_fwp builds the EDGAR date range from naive datetime.now()
    (local time), while storage timestamps use datetime.now(timezone.utc).
    Around midnight local time the range can disagree with EDGAR's dates.
  * _search_fwp retries 3 times with 0.5/1.0s sleeps; the per-document fetch
    inside fetch_abs_pricing does NOT retry (single attempt, skip on error).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from cache import db
from data import abs_pricing as apr


NOW = "2026-06-10T12:00:00+00:00"


@pytest.fixture
def no_sleep(monkeypatch):
    """Capture time.sleep calls (module-level `time` is the stdlib module)."""
    calls: list[float] = []
    monkeypatch.setattr(apr.time, "sleep", lambda s: calls.append(s))
    return calls


def _efts_payload(hits):
    return {"hits": {"hits": hits}}


def _hit(display_name):
    return {"_source": {"display_names": [display_name]}}


# ─── _search_fwp: success, date range, retry, exhaustion ─────────────────────
class TestSearchFwp:
    def test_success_returns_hits_list(self, mocked_responses, no_sleep):
        hits = [_hit("HONDA AUTO OWNER TRUST 2026-2 (CIK 0001734329)")]
        mocked_responses.get(apr.BASE_EFTS, json=_efts_payload(hits), status=200)

        out = apr._search_fwp("auto receivables", days_back=30)
        assert out == hits
        assert len(mocked_responses.calls) == 1
        assert no_sleep == []  # no retry, no sleep

    def test_query_params_use_naive_local_now_for_date_range(
        self, mocked_responses, no_sleep
    ):
        mocked_responses.get(apr.BASE_EFTS, json=_efts_payload([]), status=200)

        # NOTE(bug): the range comes from naive datetime.now() (local clock),
        # not datetime.now(timezone.utc) — refactor target. Computing the
        # expected strings before AND after the call tolerates a midnight
        # rollover during the test.
        before = datetime.now()
        apr._search_fwp("floorplan", days_back=7)
        after = datetime.now()

        q = parse_qs(urlparse(mocked_responses.calls[0].request.url).query)
        assert q["q"] == ['"floorplan"']      # keyword is quoted for FTS
        assert q["forms"] == ["FWP"]
        assert q["enddt"][0] in {
            before.strftime("%Y-%m-%d"), after.strftime("%Y-%m-%d")
        }
        assert q["startdt"][0] in {
            (before - timedelta(days=7)).strftime("%Y-%m-%d"),
            (after - timedelta(days=7)).strftime("%Y-%m-%d"),
        }

    def test_retries_then_succeeds_on_second_attempt(
        self, mocked_responses, no_sleep
    ):
        hits = [_hit("EXETER AUTOMOBILE RECEIVABLES TRUST 2026-2 (CIK 999)")]
        # responses consumes registrations in order: first call 500, then 200.
        mocked_responses.get(apr.BASE_EFTS, status=500)
        mocked_responses.get(apr.BASE_EFTS, json=_efts_payload(hits), status=200)

        out = apr._search_fwp("auto receivables", days_back=30)
        assert out == hits
        assert len(mocked_responses.calls) == 2
        # Backoff is 0.5 * (attempt + 1) → 0.5s after the first failure.
        assert no_sleep == [0.5]

    def test_returns_empty_after_three_failed_attempts(
        self, mocked_responses, no_sleep
    ):
        mocked_responses.get(apr.BASE_EFTS, status=500)  # repeats for all calls

        out = apr._search_fwp("auto receivables", days_back=30)
        assert out == []
        assert len(mocked_responses.calls) == 3
        # Sleeps after attempts 0 and 1; the final failure logs, no sleep.
        assert no_sleep == [0.5, 1.0]

    def test_malformed_json_counts_as_failure(self, mocked_responses, no_sleep):
        # .json() raising is caught by the same `except Exception` as HTTP errors.
        mocked_responses.get(apr.BASE_EFTS, body="not json", status=200)
        out = apr._search_fwp("auto receivables", days_back=30)
        assert out == []
        assert len(mocked_responses.calls) == 3


# ─── _already_parsed ─────────────────────────────────────────────────────────
class TestAlreadyParsed:
    def test_false_on_empty_db_true_after_insert(self, fresh_db):
        assert apr._already_parsed("acc-1") is False
        db.upsert_abs_pricing_tranche({
            "accession_no": "acc-1", "deal_name": "d", "issuer": "i",
            "segment": "prime_auto", "pricing_date": "2026-06-01",
            "url": "https://x", "fetched_at": NOW, "class_name": "A-1",
            "spread_bps": 50.0, "coupon": None, "wal": 1.0,
            "benchmark": None, "rating": "AAA",
        })
        assert apr._already_parsed("acc-1") is True
        assert apr._already_parsed("acc-2") is False


# ─── _discover_trust_ciks: candidate filtering ───────────────────────────────
class TestDiscoverTrustCiks:
    def test_keeps_only_trust_filers_and_parses_cik(
        self, mocked_responses, no_sleep
    ):
        hits = [
            _hit("CARVANA AUTO RECEIVABLES TRUST 2026-1 (CIK 0001234567)"),
            # Non-trust filer (the sponsor) is filtered out.
            _hit("FORD MOTOR CREDIT CO LLC (CIK 0000038009)"),
            # Display name without a CIK is filtered out.
            _hit("MYSTERY AUTO TRUST 2026-9"),
        ]
        mocked_responses.get(apr.BASE_EFTS, json=_efts_payload(hits), status=200)

        ciks = apr._discover_trust_ciks(["auto receivables"], days_back=30)
        # Name is the part before "(CIK", stripped of trailing space/comma.
        assert ciks == {1234567: "CARVANA AUTO RECEIVABLES TRUST 2026-1"}

    def test_one_search_per_keyword_with_dedup_across_keywords(
        self, mocked_responses, no_sleep
    ):
        hits = [_hit("HONDA AUTO OWNER TRUST 2026-2 (CIK 0001734329)")]
        mocked_responses.get(apr.BASE_EFTS, json=_efts_payload(hits), status=200)

        ciks = apr._discover_trust_ciks(["kw-a", "kw-b"], days_back=30)
        assert ciks == {1734329: "HONDA AUTO OWNER TRUST 2026-2"}
        assert len(mocked_responses.calls) == 2  # one EFTS query per keyword

    def test_search_failure_yields_empty_dict(self, mocked_responses, no_sleep):
        mocked_responses.get(apr.BASE_EFTS, status=500)
        assert apr._discover_trust_ciks(["kw"], days_back=30) == {}
        # _search_fwp burned its 3 retries on the single keyword.
        assert len(mocked_responses.calls) == 3


# ─── _list_trust_fwps: submissions parsing + cutoff filter ───────────────────
class TestListTrustFwps:
    def _submissions(self, forms, dates, accs, docs):
        return {"filings": {"recent": {
            "form": forms, "filingDate": dates,
            "accessionNumber": accs, "primaryDocument": docs,
        }}}

    def test_returns_recent_fwps_only(self, mocked_responses):
        url = apr.BASE_SUBMISSIONS.format(cik=1234567)
        mocked_responses.get(url, json=self._submissions(
            forms=["FWP", "424B5", "FWP", "FWP"],
            dates=["2026-06-05", "2026-06-05", "2026-06-01", "2025-01-01"],
            accs=["a-1", "a-2", "a-3", "a-4"],
            docs=["d1.htm", "d2.htm", "d3.htm", "d4.htm"],
        ), status=200)

        out = apr._list_trust_fwps(1234567, cutoff="2026-05-15")
        # 424B5 excluded (wrong form); a-4 excluded (before cutoff, string >=).
        assert out == [
            ("a-1", "d1.htm", "2026-06-05"),
            ("a-3", "d3.htm", "2026-06-01"),
        ]
        # CIK is zero-padded to 10 digits in the submissions URL.
        assert "CIK0001234567.json" in mocked_responses.calls[0].request.url

    def test_http_error_returns_empty_list(self, mocked_responses):
        url = apr.BASE_SUBMISSIONS.format(cik=42)
        mocked_responses.get(url, status=404)
        assert apr._list_trust_fwps(42, cutoff="2026-01-01") == []

    def test_empty_submissions_returns_empty(self, mocked_responses):
        url = apr.BASE_SUBMISSIONS.format(cik=42)
        mocked_responses.get(url, json={}, status=200)
        assert apr._list_trust_fwps(42, cutoff="2026-01-01") == []


# ─── fetch_abs_pricing: end-to-end flow ──────────────────────────────────────
PRICING_FWP_HTML = """
<table>
  <tr><td>Pricing Term Sheet</td><td></td><td></td><td></td><td></td></tr>
  <tr><td>Class</td><td>Spread</td><td>Coupon</td><td>WAL</td><td>Rating</td></tr>
  <tr><td>A-1</td><td>+ 25</td><td>5.10</td><td>0.5</td><td>AAA</td></tr>
  <tr><td>A-2</td><td>+ 55</td><td>4.85</td><td>2.5</td><td>AAA</td></tr>
  <tr><td>B</td><td>+ 100</td><td>5.20</td><td>3.0</td><td>AA</td></tr>
</table>
"""

RATINGS_FWP_HTML = """
<table>
  <tr><td>Ratings</td><td></td></tr>
  <tr><td>Class</td><td>Rating</td></tr>
  <tr><td>A-1</td><td>AAA</td></tr>
</table>
"""

TRUST_NAME = "CARVANA AUTO RECEIVABLES TRUST 2026-1 (CIK 0001234567)"
ACC = "0001234567-26-000001"
DOC = "pricing.htm"


class TestFetchAbsPricing:
    def _wire_discovery(self, mocked_responses, monkeypatch, filing_date):
        """Single keyword → one trust → one recent FWP filing."""
        monkeypatch.setattr(apr, "load_data_sources", lambda: {
            "abs_pricing": {"discovery_keywords": ["auto receivables"]},
        })
        mocked_responses.get(
            apr.BASE_EFTS, json=_efts_payload([_hit(TRUST_NAME)]), status=200,
        )
        mocked_responses.get(
            apr.BASE_SUBMISSIONS.format(cik=1234567),
            json={"filings": {"recent": {
                "form": ["FWP"], "filingDate": [filing_date],
                "accessionNumber": [ACC], "primaryDocument": [DOC],
            }}},
            status=200,
        )

    @pytest.fixture
    def recent_date(self):
        return (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

    def test_discovers_parses_and_stores_tranches(
        self, fresh_db, mocked_responses, monkeypatch, no_sleep, recent_date
    ):
        self._wire_discovery(mocked_responses, monkeypatch, recent_date)
        doc_url = apr._archive_url(1234567, ACC, DOC)
        mocked_responses.get(doc_url, body=PRICING_FWP_HTML, status=200)

        n = apr.fetch_abs_pricing(days_back=30)
        assert n == 3

        with db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM abs_pricing ORDER BY wal"
            ).fetchall()]
        assert [r["class_name"] for r in rows] == ["A-1", "A-2", "B"]
        r = rows[1]
        assert r["accession_no"] == ACC
        # Trust name (sans CIK suffix) is used as both deal_name and issuer.
        assert r["deal_name"] == "CARVANA AUTO RECEIVABLES TRUST 2026-1"
        assert r["segment"] == "subprime_auto"  # "carvana" keyword
        assert r["pricing_date"] == recent_date
        assert r["url"] == doc_url
        assert r["spread_bps"] == 55.0
        assert r["rating"] == "AAA"

    def test_already_parsed_accession_is_skipped_without_doc_fetch(
        self, fresh_db, mocked_responses, monkeypatch, no_sleep, recent_date
    ):
        db.upsert_abs_pricing_tranche({
            "accession_no": ACC, "deal_name": "d", "issuer": "i",
            "segment": "subprime_auto", "pricing_date": recent_date,
            "url": "https://x", "fetched_at": NOW, "class_name": "A-1",
            "spread_bps": 25.0, "coupon": None, "wal": 0.5,
            "benchmark": None, "rating": "AAA",
        })
        self._wire_discovery(mocked_responses, monkeypatch, recent_date)

        n = apr.fetch_abs_pricing(days_back=30)
        assert n == 0
        # Only the EFTS search + submissions calls — no archive document fetch.
        urls = [c.request.url for c in mocked_responses.calls]
        assert not any("Archives" in u for u in urls)

    def test_doc_fetch_failure_is_skipped_without_retry(
        self, fresh_db, mocked_responses, monkeypatch, no_sleep, recent_date
    ):
        # NOTE: unlike _search_fwp, the document fetch makes exactly ONE
        # attempt and skips the filing on failure — the asymmetry the
        # upcoming refactor targets.
        self._wire_discovery(mocked_responses, monkeypatch, recent_date)
        doc_url = apr._archive_url(1234567, ACC, DOC)
        mocked_responses.get(doc_url, status=500)

        n = apr.fetch_abs_pricing(days_back=30)
        assert n == 0
        doc_calls = [c for c in mocked_responses.calls
                     if c.request.url.startswith(doc_url)]
        assert len(doc_calls) == 1

    def test_non_pricing_fwp_stores_nothing(
        self, fresh_db, mocked_responses, monkeypatch, no_sleep, recent_date
    ):
        # A ratings FWP (no spread column) parses to [] and is silently skipped.
        self._wire_discovery(mocked_responses, monkeypatch, recent_date)
        doc_url = apr._archive_url(1234567, ACC, DOC)
        mocked_responses.get(doc_url, body=RATINGS_FWP_HTML, status=200)

        n = apr.fetch_abs_pricing(days_back=30)
        assert n == 0
        with db.get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM abs_pricing").fetchone()[0]
        assert count == 0

    def test_filing_older_than_cutoff_is_not_fetched(
        self, fresh_db, mocked_responses, monkeypatch, no_sleep
    ):
        old = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        self._wire_discovery(mocked_responses, monkeypatch, old)

        n = apr.fetch_abs_pricing(days_back=30)
        assert n == 0
        urls = [c.request.url for c in mocked_responses.calls]
        assert not any("Archives" in u for u in urls)
