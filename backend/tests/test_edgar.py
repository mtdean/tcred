"""Cover data/edgar.py — SEC EDGAR full-text search + classification helpers.

Strategy:
  * `_classify_asset_class` and `_classify_issuance_type` are pure regex tables —
    parametrize the documented buckets.
  * `edgar_index_url` builds a canonical URL from EFTS bits.
  * `_search_filings` and `fetch_abs_filings` hit EFTS; mock with `responses`.
  * `get_recent_filings` and `get_edgar_facets` are DB queries — seed and verify.
"""

from __future__ import annotations

import pytest

from cache import db
from data import edgar as e


NOW = "2026-05-29T12:00:00+00:00"


def _filing(accession_no="acc-1", **overrides):
    base = {
        "accession_no": accession_no,
        "company_name": "Honda Auto Receivables 2026-1",
        "form_type": "424B5",
        "filed_at": "2026-05-28",
        "description": "Pricing supplement",
        "url": "https://sec.gov/x",
        "asset_class": "auto loan",
        "issuance_type": "debt",
        "fetched_at": NOW,
    }
    base.update(overrides)
    return base


# ─── edgar_index_url ──────────────────────────────────────────────────────────
class TestEdgarIndexUrl:
    def test_builds_canonical_archives_url(self):
        url = e.edgar_index_url(
            "0001193125-26-232093:doc.htm",
            "HONDA AUTO RECEIVABLES TRUST 2026-1 (CIK 0001734329)",
        )
        # CIK stripped of leading zeros; accession without dashes in path,
        # accession-with-dashes in filename.
        assert url == (
            "https://www.sec.gov/Archives/edgar/data/1734329/"
            "000119312526232093/0001193125-26-232093-index.htm"
        )

    def test_returns_empty_when_no_cik(self):
        assert e.edgar_index_url("0001193125-26-232093:doc.htm", "No CIK Here") == ""

    def test_returns_empty_when_no_accession(self):
        assert e.edgar_index_url("", "X (CIK 12345)") == ""


# ─── _classify_asset_class ────────────────────────────────────────────────────
class TestClassifyAssetClass:
    @pytest.mark.parametrize("name, expected", [
        ("Wells Fargo Commercial Mortgage Trust 2026-5C9", "mortgage"),
        ("BANK 2026-BNK52", "mortgage"),
        ("Fannie Mae 2026-XYZ", "mortgage"),
        ("Drug Royalty III LP", "royalty"),
        ("ROYALTY PHARMA", "royalty"),
        ("Carlyle CLO 2026-1", "CLO"),
        ("Honda Auto Receivables Owner Trust 2026-1", "auto loan"),
        ("Chase Credit Card Issuance Trust 2026-A", "credit card"),
        ("Verizon Master Trust Card Funding", "credit card"),
        ("Deere Equipment Trust 2026-A", "equipment finance"),
        ("AASET Aircraft Series 2026-1", "aircraft"),
        ("Navient Private Education Loan Trust 2026", "student loan"),
        ("Marlette Marketplace Lending Trust", "marketplace lending"),
        ("SoFi Consumer Loan Program 2026", "consumer loan"),
        ("Sunrun Atlas Issuer Solar ABS 2026", "solar"),
        ("Switch Data Center Issuer 2026", "data center"),
        ("Triton Container Lease 2026", "container"),
        ("CF Hippolyta Issuer Whole Business 2026", "whole business"),
        ("Ford Dealer Floorplan Master Owner Trust", "floorplan"),
    ])
    def test_matches_specific_pattern(self, name, expected):
        assert e._classify_asset_class(name, "fallback-kw") == expected

    def test_falls_back_when_no_rule_matches(self):
        assert e._classify_asset_class("Some Random Issuer Corp", "esoteric") == "esoteric"

    def test_empty_name_uses_fallback(self):
        assert e._classify_asset_class("", "auto loan") == "auto loan"

    def test_first_matching_rule_wins(self):
        # 'commercial mortgage' should win over a later 'auto' substring.
        out = e._classify_asset_class(
            "Commercial Mortgage Auto Pass-Through", "anything",
        )
        assert out == "mortgage"


# ─── _classify_issuance_type ──────────────────────────────────────────────────
class TestClassifyIssuanceType:
    @pytest.mark.parametrize("name, form, expected", [
        # ABS-specific forms are always debt regardless of name.
        ("Whatever Corp", "ABS-15G", "debt"),
        ("Whatever Corp", "ABS-EE", "debt"),
        # Trust / receivables / funding LLC names → debt under any form.
        ("Honda Auto Receivables Trust 2026-1", "424B5", "debt"),
        ("Carmax Funding LLC", "S-3", "debt"),
        ("Master Trust III", "8-K", "debt"),
        ("Chase Issuance Trust", "424B5", "debt"),
        # Plain corporates without trust naming → equity.
        ("Boeing Co", "S-3", "equity"),
        ("Apple Inc", "424B5", "equity"),
        ("", "424B5", "equity"),
    ])
    def test_buckets(self, name, form, expected):
        assert e._classify_issuance_type(name, form) == expected


# ─── _search_filings (mocked EFTS) ───────────────────────────────────────────
class TestSearchFilings:
    def _efts_payload(self, hits):
        return {"hits": {"hits": hits}}

    def test_parses_hit_into_record(self, mocked_responses):
        body = self._efts_payload([{
            "_id": "0001193125-26-232093:doc.htm",
            "_source": {
                "display_names": ["HONDA AUTO RECEIVABLES TRUST 2026-1 (CIK 0001734329)"],
                "file_date": "2026-05-28",
            },
        }])
        mocked_responses.get(e.BASE_EFTS, json=body, status=200)

        results = e._search_filings("424B5", ["auto loan"], days_back=7)
        assert len(results) == 1
        r = results[0]
        assert r["accession_no"] == "0001193125-26-232093:doc.htm"
        assert r["form_type"] == "424B5"
        assert r["filed_at"] == "2026-05-28"
        # Auto-loan rule matches the trust name.
        assert r["asset_class"] == "auto loan"
        # Trust naming → debt.
        assert r["issuance_type"] == "debt"
        # URL is built from the hit id + CIK.
        assert "0001734329" in r["url"] or "1734329" in r["url"]

    def test_empty_hits_returns_empty_list(self, mocked_responses):
        mocked_responses.get(
            e.BASE_EFTS, json={"hits": {"hits": []}}, status=200,
        )
        out = e._search_filings("424B5", ["auto loan"], days_back=7)
        assert out == []

    def test_http_error_swallowed_returns_partial(self, mocked_responses):
        # One keyword fails, another succeeds. The error is logged but the
        # second keyword's hits still come back.
        mocked_responses.get(
            e.BASE_EFTS, status=500,
        )
        mocked_responses.get(
            e.BASE_EFTS,
            json=self._efts_payload([{
                "_id": "acc:doc.htm",
                "_source": {
                    "display_names": ["X Trust (CIK 1234)"],
                    "file_date": "2026-05-20",
                },
            }]),
            status=200,
        )
        out = e._search_filings("424B5", ["bad", "good"], days_back=7)
        assert len(out) == 1
        assert out[0]["accession_no"] == "acc:doc.htm"

    def test_multiple_keywords_concatenate_results(self, mocked_responses):
        for i in range(2):
            mocked_responses.get(
                e.BASE_EFTS,
                json=self._efts_payload([{
                    "_id": f"acc-{i}:doc.htm",
                    "_source": {
                        "display_names": [f"Trust {i} (CIK 100{i})"],
                        "file_date": "2026-05-20",
                    },
                }]),
                status=200,
            )
        out = e._search_filings("424B5", ["a", "b"], days_back=7)
        assert {r["accession_no"] for r in out} == {"acc-0:doc.htm", "acc-1:doc.htm"}


# ─── fetch_abs_filings (DB-coupled) ──────────────────────────────────────────
class TestFetchAbsFilings:
    def test_inserts_rows_into_edgar_filings(
        self, fresh_db, mocked_responses, monkeypatch
    ):
        # Tiny config — one form type, one keyword. EFTS returns one hit.
        monkeypatch.setattr(e, "load_data_sources", lambda: {
            "edgar": {
                "form_types": ["ABS-EE"],
                "asset_class_keywords": ["auto loan"],
            },
        })
        body = {"hits": {"hits": [{
            "_id": "0001193125-26-232093:doc.htm",
            "_source": {
                "display_names": ["HONDA AUTO RECEIVABLES TRUST 2026-1 (CIK 1734329)"],
                "file_date": "2026-05-28",
            },
        }]}}
        mocked_responses.get(e.BASE_EFTS, json=body, status=200)

        n = e.fetch_abs_filings(days_back=7)
        assert n == 1

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT accession_no, asset_class, issuance_type FROM edgar_filings"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["asset_class"] == "auto loan"
        assert rows[0]["issuance_type"] == "debt"

    def test_empty_config_returns_zero(self, fresh_db, monkeypatch):
        # form_types absent → uses the default ['ABS-15G', 'ABS-EE'], but
        # no keywords means no inner-loop iterations at all → 0 inserts.
        monkeypatch.setattr(e, "load_data_sources", lambda: {"edgar": {}})
        # No network calls expected, so we don't register any responses.
        # Use a default form types and zero keywords.
        n = e.fetch_abs_filings(days_back=7)
        assert n == 0


# ─── get_recent_filings ──────────────────────────────────────────────────────
class TestGetRecentFilings:
    def _seed(self):
        db.upsert_edgar_filing(_filing(
            accession_no="a", company_name="Honda Auto Receivables",
            form_type="424B5", asset_class="auto loan",
            issuance_type="debt", filed_at="2026-05-28",
        ))
        db.upsert_edgar_filing(_filing(
            accession_no="b", company_name="Chase Card",
            form_type="ABS-EE", asset_class="credit card",
            issuance_type="debt", filed_at="2026-05-29",
        ))
        db.upsert_edgar_filing(_filing(
            accession_no="c", company_name="Boeing Co",
            form_type="S-3", asset_class="esoteric",
            issuance_type="equity", filed_at="2026-05-27",
        ))

    def test_orders_by_filed_at_desc(self, fresh_db):
        self._seed()
        rows = e.get_recent_filings(limit=10)
        assert [r["accession_no"] for r in rows] == ["b", "a", "c"]

    def test_form_type_filter(self, fresh_db):
        self._seed()
        rows = e.get_recent_filings(form_type="424B5")
        assert [r["accession_no"] for r in rows] == ["a"]

    def test_asset_class_filter(self, fresh_db):
        self._seed()
        rows = e.get_recent_filings(asset_class="credit card")
        assert [r["accession_no"] for r in rows] == ["b"]

    def test_issuance_type_filter(self, fresh_db):
        self._seed()
        rows = e.get_recent_filings(issuance_type="equity")
        assert [r["accession_no"] for r in rows] == ["c"]

    def test_limit_and_offset(self, fresh_db):
        self._seed()
        # limit=1 + offset=1 → second-newest, which is 'a'.
        rows = e.get_recent_filings(limit=1, offset=1)
        assert [r["accession_no"] for r in rows] == ["a"]


# ─── get_edgar_facets ────────────────────────────────────────────────────────
class TestGetEdgarFacets:
    def test_returns_distinct_sorted_values(self, fresh_db):
        db.upsert_edgar_filing(_filing(
            accession_no="a", form_type="424B5",
            asset_class="auto loan", issuance_type="debt",
        ))
        db.upsert_edgar_filing(_filing(
            accession_no="b", form_type="ABS-EE",
            asset_class="credit card", issuance_type="debt",
        ))
        db.upsert_edgar_filing(_filing(
            accession_no="c", form_type="424B5",  # duplicate
            asset_class="auto loan",                # duplicate
            issuance_type="equity",
        ))

        facets = e.get_edgar_facets()
        assert facets["form_types"] == ["424B5", "ABS-EE"]
        assert facets["asset_classes"] == ["auto loan", "credit card"]
        assert facets["issuance_types"] == ["debt", "equity"]

    def test_empty_db_returns_empty_lists(self, fresh_db):
        facets = e.get_edgar_facets()
        assert facets == {
            "form_types": [], "asset_classes": [], "issuance_types": [],
        }
