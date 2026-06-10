"""
Characterization tests for data/abs_parser.py discovery paths (lines ~163-254):
424B5 EDGAR full-text discovery, date-range construction, per-keyword error
handling, and primary-document URL resolution via the filing's index.json.

These lock in CURRENT behavior ahead of a refactor of duplicated HTTP retry
logic and naive-vs-UTC datetime handling. Do not "fix" behavior here — update
tests only together with an intentional source change.

Known quirks locked in (see notes inline):
  * _discover_abs_424b5 builds startdt/enddt from naive datetime.now()
    (local clock) while the pipeline's fetched_at uses timezone.utc.
  * Unlike abs_pricing._search_fwp (3 attempts with backoff), discovery here
    makes exactly ONE attempt per keyword, sleeps 0.5s, and moves on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from data import abs_parser as ap


@pytest.fixture
def no_sleep(monkeypatch):
    """Capture time.sleep calls (module-level `time` is the stdlib module)."""
    calls: list[float] = []
    monkeypatch.setattr(ap.time, "sleep", lambda s: calls.append(s))
    return calls


def _efts_payload(hits):
    return {"hits": {"hits": hits}}


HONDA_HIT = {
    "_id": "0001193125-26-232093:doc.htm",
    "_source": {
        "display_names": ["HONDA AUTO RECEIVABLES TRUST 2026-1 (CIK 0001734329)"],
        "file_date": "2026-06-05",
    },
}

N_KEYWORDS = len(ap._DISCOVERY_KEYWORDS)  # currently 7


# ─── _discover_abs_424b5 ─────────────────────────────────────────────────────
class TestDiscoverAbs424b5:
    def test_one_search_per_keyword_dedup_to_unique_filings(
        self, mocked_responses, no_sleep
    ):
        # One registration repeats for every keyword query; the same hit
        # comes back each time and must dedup to a single filing record.
        mocked_responses.get(
            ap.EFTS_URL, json=_efts_payload([HONDA_HIT]), status=200,
        )

        out = ap._discover_abs_424b5(days_back=7)
        assert len(mocked_responses.calls) == N_KEYWORDS
        assert len(out) == 1

        rec = out[0]
        # Accession is the _id with the ":doc.htm" suffix stripped.
        assert rec["accession_no"] == "0001193125-26-232093"
        assert rec["hit_id"] == "0001193125-26-232093:doc.htm"
        assert rec["company_name"] == (
            "HONDA AUTO RECEIVABLES TRUST 2026-1 (CIK 0001734329)"
        )
        assert rec["filed_at"] == "2026-06-05"
        # filing_url built by edgar_index_url: CIK leading zeros stripped,
        # accession without dashes in the folder, with dashes in the filename.
        assert rec["filing_url"] == (
            "https://www.sec.gov/Archives/edgar/data/1734329/"
            "000119312526232093/0001193125-26-232093-index.htm"
        )

    def test_query_params_use_naive_local_now_for_date_range(
        self, mocked_responses, no_sleep
    ):
        mocked_responses.get(ap.EFTS_URL, json=_efts_payload([]), status=200)

        # NOTE(bug): range built from naive datetime.now() (local time), not
        # UTC — abs_parser.py:163-164. Same pattern as abs_pricing._search_fwp.
        before = datetime.now(timezone.utc)
        ap._discover_abs_424b5(days_back=7)
        after = datetime.now(timezone.utc)

        q = parse_qs(urlparse(mocked_responses.calls[0].request.url).query)
        assert q["q"] == [f'"{ap._DISCOVERY_KEYWORDS[0]}"']  # quoted keyword
        assert q["forms"] == ["424B5"]
        assert q["enddt"][0] in {
            before.strftime("%Y-%m-%d"), after.strftime("%Y-%m-%d")
        }
        assert q["startdt"][0] in {
            (before - timedelta(days=7)).strftime("%Y-%m-%d"),
            (after - timedelta(days=7)).strftime("%Y-%m-%d"),
        }

    def test_keyword_failure_is_swallowed_and_later_keywords_still_searched(
        self, mocked_responses, no_sleep
    ):
        # First keyword 500s; remaining keywords succeed with a hit.
        mocked_responses.get(ap.EFTS_URL, status=500)
        mocked_responses.get(
            ap.EFTS_URL, json=_efts_payload([HONDA_HIT]), status=200,
        )

        out = ap._discover_abs_424b5(days_back=7)
        assert len(out) == 1
        assert out[0]["accession_no"] == "0001193125-26-232093"
        # Still exactly one request per keyword — NO retry on the failed one
        # (contrast with abs_pricing._search_fwp's 3 attempts).
        assert len(mocked_responses.calls) == N_KEYWORDS
        # Error path sleeps 0.5; success path sleeps 0.15 per keyword.
        assert no_sleep == [0.5] + [0.15] * (N_KEYWORDS - 1)

    def test_all_keywords_failing_returns_empty(self, mocked_responses, no_sleep):
        mocked_responses.get(ap.EFTS_URL, status=500)
        out = ap._discover_abs_424b5(days_back=7)
        assert out == []
        assert len(mocked_responses.calls) == N_KEYWORDS
        assert no_sleep == [0.5] * N_KEYWORDS

    def test_hit_with_empty_id_is_skipped(self, mocked_responses, no_sleep):
        bad_hit = {"_id": "", "_source": {"display_names": ["X"], "file_date": "d"}}
        mocked_responses.get(
            ap.EFTS_URL, json=_efts_payload([bad_hit]), status=200,
        )
        assert ap._discover_abs_424b5(days_back=7) == []

    def test_multiple_display_names_join_with_comma(
        self, mocked_responses, no_sleep
    ):
        hit = {
            "_id": "acc-9:doc.htm",
            "_source": {
                "display_names": ["TRUST A (CIK 111)", "DEPOSITOR B (CIK 222)"],
                "file_date": "2026-06-01",
            },
        }
        mocked_responses.get(ap.EFTS_URL, json=_efts_payload([hit]), status=200)
        out = ap._discover_abs_424b5(days_back=7)
        assert out[0]["company_name"] == "TRUST A (CIK 111), DEPOSITOR B (CIK 222)"


# ─── _get_primary_document_url ───────────────────────────────────────────────
ACC = "0001193125-26-232093"
COMPANY = "HONDA AUTO RECEIVABLES TRUST 2026-1 (CIK 0001734329)"
INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/data/1734329/000119312526232093/index.json"
)


def _manifest(items):
    return {"directory": {"item": items}}


class TestGetPrimaryDocumentUrl:
    def test_returns_none_without_cik_in_company_string(self, mocked_responses):
        # No HTTP call is made at all.
        assert ap._get_primary_document_url(ACC, "No CIK Here") is None
        assert ap._get_primary_document_url(ACC, "") is None
        assert len(mocked_responses.calls) == 0

    def test_prefers_explicit_424b5_manifest_entry(self, mocked_responses):
        mocked_responses.get(INDEX_URL, json=_manifest([
            {"type": "GRAPHIC", "name": "logo.jpg"},
            {"type": "424B5", "name": "prospectus.htm"},
            {"type": "", "name": "other.htm"},
        ]), status=200)

        url = ap._get_primary_document_url(ACC, COMPANY)
        assert url == (
            "https://www.sec.gov/Archives/edgar/data/1734329/"
            "000119312526232093/prospectus.htm"
        )

    def test_accepts_424b2_entry(self, mocked_responses):
        mocked_responses.get(INDEX_URL, json=_manifest([
            {"type": "424B2", "name": "supp.htm"},
        ]), status=200)
        url = ap._get_primary_document_url(ACC, COMPANY)
        assert url.endswith("/supp.htm")

    def test_falls_back_to_first_non_index_htm(self, mocked_responses):
        mocked_responses.get(INDEX_URL, json=_manifest([
            {"type": "", "name": "0001193125-26-232093-index.htm"},
            {"type": "", "name": "filing-doc.htm"},
            {"type": "", "name": "exhibit.htm"},  # fallback takes the FIRST match
        ]), status=200)
        url = ap._get_primary_document_url(ACC, COMPANY)
        assert url.endswith("/filing-doc.htm")

    def test_returns_none_when_only_index_pages_exist(self, mocked_responses):
        mocked_responses.get(INDEX_URL, json=_manifest([
            {"type": "", "name": "form-index.htm"},
            {"type": "GRAPHIC", "name": "img.jpg"},
        ]), status=200)
        assert ap._get_primary_document_url(ACC, COMPANY) is None

    def test_returns_none_on_manifest_fetch_error_without_retry(
        self, mocked_responses
    ):
        # Single attempt only — errors are logged at debug and yield None.
        mocked_responses.get(INDEX_URL, status=404)
        assert ap._get_primary_document_url(ACC, COMPANY) is None
        assert len(mocked_responses.calls) == 1

    def test_returns_none_on_empty_manifest(self, mocked_responses):
        mocked_responses.get(INDEX_URL, json={}, status=200)
        assert ap._get_primary_document_url(ACC, COMPANY) is None
