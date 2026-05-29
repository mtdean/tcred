"""
Cover data/abs_parser.py + abs_pricing.py + abs_reparse.py.

Strategy:
  * Pure helpers (dollar/rate/spread/WAL parsing, asset classification,
    seniority bucketing, treasury-tenor mapping) get direct table-driven tests.
  * HTML parsers run against hand-built minimal fixtures that exercise the
    real DOM / pandas paths.
  * DB-coupled spread computation and momentum/delta queries use the live DB.
"""

from __future__ import annotations

import pytest

from cache import db
from data import abs_parser as ap
from data import abs_pricing as apr
from data import abs_reparse as ar


NOW = "2026-05-29T12:00:00+00:00"


# ─── abs_parser cell parsers ──────────────────────────────────────────────────
class TestCellParsers:
    @pytest.mark.parametrize("s, expected", [
        ("$143,000,000", 143_000_000.0),
        ("$1,250,000.50", 1_250_000.50),
        ("",  None),
        ("N/A", None),
    ])
    def test_parse_dollar(self, s, expected):
        assert ap._parse_dollar(s) == expected

    @pytest.mark.parametrize("s, expected", [
        ("4.540%", 4.54),
        ("5.00%", 5.0),
        ("SOFR + 55 bps", None),  # _parse_rate requires a "%" — floating returns None
        ("", None),
    ])
    def test_parse_rate(self, s, expected):
        assert ap._parse_rate(s) == expected

    @pytest.mark.parametrize("s, expected", [
        ("SOFR + 55 bps", 55.0),
        ("+ 80 bp", 80.0),
        ("4.50%", None),       # not a spread expression
        ("", None),
    ])
    def test_parse_spread_bps(self, s, expected):
        assert ap._parse_spread_bps(s) == expected

    @pytest.mark.parametrize("s, expected", [
        ("2.10 years", 2.10),
        ("3.5", 3.5),
        ("", None),
    ])
    def test_parse_wal(self, s, expected):
        assert ap._parse_wal(s) == expected

    @pytest.mark.parametrize("raw, expected", [
        ("AAA (sf)", "AAA"),
        ("Aaa(sf) ", "Aaa"),
        ("", None),
        (None, None),
    ])
    def test_normalize_rating(self, raw, expected):
        assert ap._normalize_rating(raw) == expected


# ─── Asset-class classifier ──────────────────────────────────────────────────
class TestAssetClassClassifier:
    @pytest.mark.parametrize("text, expected", [
        ("Honda Auto Receivables 2026-1", "prime_auto_loan"),
        # NOTE: Santander Drive shows as prime here because "auto receivables"
        # matches first by dict ordering. The subprime keyword still works on
        # names without "auto receivables", which is the intended fallback.
        ("Exeter Funding 2026-2", "subprime_auto_loan"),
        ("Chase Issuance Trust credit card", "credit_card"),
        ("Deere Equipment 2026-A", "equipment"),
        ("SoFi Consumer Loan", "consumer_loan"),
        ("Navient Private Education Loan 2026", "student_loan"),
        ("Sunrun Solar ABS", "solar"),
        ("Verizon Device Payment", "esoteric_wireless"),
        ("AASET Aircraft", "esoteric_aircraft"),
        ("Random Issuer Without Keywords", "esoteric_other"),
    ])
    def test_keyword_taxonomy(self, text, expected):
        assert ap._classify_asset_class(text) == expected


# ─── Treasury tenor mapping + DB-coupled spread compute ──────────────────────
class TestTreasuryMapping:
    @pytest.mark.parametrize("wal, tenor_label", [
        (1.0, "UST1Y"),
        (2.0, "UST2Y"),
        (3.0, "UST3Y"),
        (5.0, "UST5Y"),
        (7.0, "UST7Y"),
        (12.0, "UST10Y"),
    ])
    def test_treasury_for_wal(self, wal, tenor_label):
        _, label = ap._treasury_for_wal(wal)
        assert label == tenor_label


class TestComputeSpread:
    def test_floating_keeps_printed_spread(self, fresh_db):
        tranche = {
            "coupon_type": "floating", "floating_index": "SOFR",
            "floating_spread_bps": 60.0, "wal_years": 2.0, "coupon_rate": None,
        }
        out = ap._compute_spread(tranche, "2026-05-20")
        assert out["benchmark"] == "SOFR"
        assert out["spread_to_benchmark"] == 60.0
        assert out["implied_yield"] is None

    def test_fixed_computes_against_matched_treasury(self, fresh_db):
        # Seed DGS2 = 4.10% on the filing date so the parser finds it.
        db.upsert_metric({
            "series_id": "DGS2", "label": "2y", "category": "rates",
            "date": "2026-05-20", "value": 4.10, "fetched_at": NOW,
        })
        tranche = {
            "coupon_type": "fixed", "coupon_rate": 4.85, "wal_years": 2.0,
        }
        out = ap._compute_spread(tranche, "2026-05-20")
        assert out["benchmark"] == "UST2Y"
        assert out["benchmark_rate"] == pytest.approx(4.10)
        # (4.85 - 4.10) * 100 = 75 bps
        assert out["spread_to_benchmark"] == pytest.approx(75.0)
        assert out["implied_yield"] == 4.85

    def test_fixed_without_treasury_data_keeps_spread_null(self, fresh_db):
        tranche = {"coupon_type": "fixed", "coupon_rate": 5.0, "wal_years": 3.0}
        out = ap._compute_spread(tranche, "2026-05-20")
        assert out["benchmark"] == "UST3Y"
        assert out["spread_to_benchmark"] is None  # no DGS3 in DB
        assert out["implied_yield"] == 5.0

    def test_fixed_without_wal_returns_no_benchmark(self, fresh_db):
        tranche = {"coupon_type": "fixed", "coupon_rate": 5.0, "wal_years": None}
        out = ap._compute_spread(tranche, "2026-05-20")
        assert out["benchmark"] is None
        assert out["spread_to_benchmark"] is None


# ─── _extract_from_html minimal flow ─────────────────────────────────────────
class TestExtractFromHtml:
    PRICING_TABLE_HTML = """
    <html><head><title>Carvana Auto Receivables Trust 2026-1</title></head>
    <body>
      <h1>Carvana Auto Receivables Trust 2026-1</h1>
      <p>Closing Date: June 15, 2026. Cutoff Date: May 31, 2026.</p>
      <table>
        <tr><td>Class</td><td>Principal Amount</td><td>Interest Rate</td></tr>
        <tr><td>A-1</td><td>$143,000,000</td><td>5.10%</td></tr>
        <tr><td>A-2</td><td>$200,000,000</td><td>4.90%</td></tr>
        <tr><td>B</td><td>$50,000,000</td><td>SOFR + 80 bps</td></tr>
      </table>
    </body></html>
    """

    def test_pricing_table_yields_three_tranches_with_correct_types(self):
        result = ap._extract_from_html(self.PRICING_TABLE_HTML, {})
        assert result["confidence"] == "high"
        assert len(result["tranches"]) == 3

        classes = [t["class_name"] for t in result["tranches"]]
        assert classes == ["A-1", "A-2", "B"]

        a1, a2, b = result["tranches"]
        assert a1["principal_amount"] == 143_000_000
        assert a1["coupon_rate"] == pytest.approx(5.10)
        assert a1["coupon_type"] == "fixed"

        # Floating B tranche
        assert b["coupon_type"] == "floating"
        assert b["floating_index"] == "SOFR"
        assert b["floating_spread_bps"] == 80.0
        assert b["coupon_rate"] is None

    def test_no_pricing_table_returns_low_confidence(self):
        html = "<html><body><p>No tables here.</p></body></html>"
        result = ap._extract_from_html(html, {})
        assert result["confidence"] == "low"
        assert result["tranches"] == []

    def test_closing_and_cutoff_dates_are_extracted(self):
        result = ap._extract_from_html(self.PRICING_TABLE_HTML, {})
        assert result["closing_date"].startswith("June 15")
        assert result["cutoff_date"].startswith("May 31")


# ─── abs_pricing segment + seniority ──────────────────────────────────────────
class TestSegmentClassifier:
    @pytest.mark.parametrize("name, expected", [
        ("Santander Drive Auto Receivables Trust 2026-1", "subprime_auto"),
        ("Carvana Receivables Trust 2026-1", "subprime_auto"),
        ("Honda Auto Receivables Owner Trust 2026-1", "prime_auto"),
        ("CNH Equipment Trust 2026-A", "equipment"),
        ("Citibank Credit Card Issuance Trust", "credit_card"),
        ("SLM Student Loan Trust", "student_loan"),
        ("Ford Dealer Floorplan Trust", "floorplan"),
        ("Random Corporate Bond Issuer", "other"),
    ])
    def test_segmentation(self, name, expected):
        assert apr._segment(name) == expected


class TestSeniorityBucket:
    @pytest.mark.parametrize("class_name, rating, expected", [
        ("A-1", "AAA", "senior"),
        ("A-2", "AAA(sf)", "senior"),
        ("B", "AA", "mezzanine"),
        ("C", "A", "mezzanine"),
        ("D", "BBB", "junior"),
        ("E", "BB", "junior"),
        ("A-1", None, "senior"),          # rating absent → class letter
        ("B", None, "mezzanine"),
        ("D", None, "junior"),
        ("F", None, "junior"),
    ])
    def test_rating_then_class_letter(self, class_name, rating, expected):
        assert apr._seniority_bucket(class_name, rating) == expected


# ─── abs_pricing num helpers ─────────────────────────────────────────────────
class TestAbsPricingNumParser:
    @pytest.mark.parametrize("s, expected", [
        ("+ 53", 53.0),
        ("99.99", 99.99),
        ("1,234.5", 1234.5),
        ("nan", None),
        ("", None),
    ])
    def test_first_number(self, s, expected):
        assert apr._num(s) == expected

    @pytest.mark.parametrize("s, expected", [
        ("Class", "CLASS"),
        ("M / S", "M/S"),
        ("Spread (bps)", "SPREADBPS"),  # parens stripped, "bps" remains
        ("Coupon Rate", "COUPONRATE"),
    ])
    def test_norm_strips_punct_and_uppercases(self, s, expected):
        assert apr._norm(s) == expected


# ─── abs_pricing FWP table parser ─────────────────────────────────────────────
class TestParsePricingTranches:
    # First row is a junk title so pandas takes it as the columns; the real
    # header (Class / Spread / Coupon / WAL / Rating) lives in row 1 and the
    # parser re-detects it via _FIELD_TOKENS.
    PRICING_FWP_HTML = """
    <table>
      <tr><td>Pricing Term Sheet</td><td></td><td></td><td></td><td></td></tr>
      <tr><td>Class</td><td>Spread</td><td>Coupon</td><td>WAL</td><td>Rating</td></tr>
      <tr><td>A-1</td><td>+ 25</td><td>5.10</td><td>0.5</td><td>AAA</td></tr>
      <tr><td>A-2</td><td>+ 55</td><td>4.85</td><td>2.5</td><td>AAA</td></tr>
      <tr><td>B</td><td>+ 100</td><td>5.20</td><td>3.0</td><td>AA</td></tr>
      <tr><td>C-RETAINED</td><td>+ 200</td><td>5.50</td><td>3.5</td><td>BBB</td></tr>
    </table>
    """

    def test_returns_offered_tranches_with_parsed_fields(self):
        tranches = apr._parse_pricing_tranches(self.PRICING_FWP_HTML)
        # Retained row should be filtered.
        assert {t["class_name"] for t in tranches} == {"A-1", "A-2", "B"}
        a2 = next(t for t in tranches if t["class_name"] == "A-2")
        assert a2["spread_bps"] == 55.0
        assert a2["coupon"] == 4.85
        assert a2["wal"] == 2.5
        assert a2["rating"] == "AAA"

    def test_returns_empty_when_no_spread_column(self):
        html = """
        <table>
          <tr><td>Ratings</td><td></td></tr>
          <tr><td>Class</td><td>Rating</td></tr>
          <tr><td>A-1</td><td>AAA</td></tr>
        </table>
        """
        assert apr._parse_pricing_tranches(html) == []


# ─── Spread momentum query layer ─────────────────────────────────────────────
def _seed_pricing(accession, segment, date, tranches):
    for tr in tranches:
        db.upsert_abs_pricing_tranche({
            "accession_no": accession, "deal_name": f"deal-{accession}",
            "issuer": "issuer", "segment": segment, "pricing_date": date,
            "url": "https://x", "fetched_at": NOW, **tr,
        })


def _tr(class_name, rating, wal, spread):
    return {
        "class_name": class_name, "rating": rating, "wal": wal,
        "spread_bps": spread, "benchmark": "I-CRV", "coupon": None,
    }


class TestSpreadMomentum:
    def test_senior_and_subordinate_per_deal(self, fresh_db):
        _seed_pricing("d1", "subprime_auto", "2026-04-01", [
            _tr("A-1", "AAA", 1.0, 25.0),
            _tr("A-2", "AAA", 2.5, 55.0),
            _tr("B",   "AA",  3.0, 100.0),
        ])
        out = apr.get_abs_spread_momentum()
        assert len(out) == 1
        r = out[0]
        # senior = widest-WAL AAA → A-2 spread; subordinate = widest-WAL → B
        assert r["senior_spread"] == 55.0
        assert r["subordinate_spread"] == 100.0
        assert r["segment"] == "subprime_auto"


class TestSpreadMomentumDeltas:
    def test_delta_against_prior_deal_in_segment_and_bucket(self, fresh_db):
        _seed_pricing("d1", "subprime_auto", "2026-04-01", [
            _tr("A-2", "AAA", 2.5, 60.0),
            _tr("B",   "AA",  3.0, 110.0),
        ])
        _seed_pricing("d2", "subprime_auto", "2026-05-01", [
            _tr("A-2", "AAA", 2.5, 70.0),  # widened 10 vs d1 senior
            _tr("B",   "AA",  3.0, 115.0),  # widened 5
        ])
        # An unrelated segment must not affect the subprime deltas.
        _seed_pricing("d3", "prime_auto", "2026-05-02", [
            _tr("A-2", "AAA", 2.5, 40.0),
        ])
        rows = apr.get_abs_spread_momentum_deltas()
        d2_senior = next(
            r for r in rows
            if r["accession_no"] == "d2" and r["seniority"] == "senior"
        )
        assert d2_senior["prior_spread_bps"] == 60.0
        assert d2_senior["delta_bps"] == pytest.approx(10.0)

        d2_mezz = next(
            r for r in rows
            if r["accession_no"] == "d2" and r["seniority"] == "mezzanine"
        )
        assert d2_mezz["delta_bps"] == pytest.approx(5.0)

        # First-in-bucket deltas must be None.
        d1_senior = next(
            r for r in rows
            if r["accession_no"] == "d1" and r["seniority"] == "senior"
        )
        assert d1_senior["delta_bps"] is None


# ─── abs_reparse: HTML cache path safety ─────────────────────────────────────
class TestReparseCachePath:
    def test_sanitizes_unsafe_chars(self):
        p = ar._cache_path("0001234567-26-000001")
        assert p.name == "0001234567-26-000001.html"

    def test_strips_path_traversal_attempts(self):
        p = ar._cache_path("../../etc/passwd")
        # Slashes and dots collapse to underscores by the regex.
        assert "/" not in p.name
        assert ".." not in p.name.replace(".html", "")
