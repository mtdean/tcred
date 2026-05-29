"""
Cover data/clo.py — read-only analytics over CLO ETF prices in `metrics` and
CLO-tagged EDGAR filings.

Strategy:
  * Seed CLO ETF prices into metrics; assert compute_clo_spread_proxy() joins
    them by date and yields correct price ratios.
  * Seed edgar_filings with a mix of CLO and non-CLO rows; assert the LIKE
    filter in get_clo_edgar_filings() returns the right subset.
"""

from __future__ import annotations

import pytest

from cache import db
from data import clo


NOW = "2026-05-29T12:00:00+00:00"


def _seed_etf(series_id: str, points: list[tuple[str, float]]) -> None:
    for d, v in points:
        db.upsert_metric({
            "series_id": series_id, "label": series_id, "category": "market",
            "date": d, "value": v, "fetched_at": NOW,
        })


def _seed_edgar(accession_no: str, asset_class: str, description: str,
                company_name: str = "X", filed_at: str = "2026-05-28") -> None:
    db.upsert_edgar_filing({
        "accession_no": accession_no, "company_name": company_name,
        "form_type": "424B5", "filed_at": filed_at,
        "description": description, "url": f"https://sec.gov/{accession_no}",
        "asset_class": asset_class, "issuance_type": "debt",
        "fetched_at": NOW,
    })


# ─── compute_clo_spread_proxy ─────────────────────────────────────────────────
class TestComputeCloSpreadProxy:
    def test_returns_empty_when_no_prices(self, fresh_db):
        assert clo.compute_clo_spread_proxy() == []

    def test_skips_pair_when_one_leg_missing(self, fresh_db):
        # Only the JAAA leg present — JBBB missing → skip the JAAA/JBBB pair.
        _seed_etf("mkt_JAAA", [("2026-05-20", 50.0)])
        assert clo.compute_clo_spread_proxy() == []

    def test_emits_one_row_per_common_date_with_ratio(self, fresh_db):
        _seed_etf("mkt_JAAA", [
            ("2026-05-19", 50.0),
            ("2026-05-20", 50.5),
            ("2026-05-21", 50.0),
        ])
        _seed_etf("mkt_JBBB", [
            ("2026-05-19", 48.0),
            ("2026-05-20", 49.0),
            # 2026-05-21 missing → that date must be dropped.
        ])

        out = clo.compute_clo_spread_proxy()
        # We only seeded one pair so this is the JAAA/JBBB rows.
        jaaa_jbbb = [r for r in out if r["aaa_series"] == "mkt_JAAA"
                     and r["mezz_series"] == "mkt_JBBB"]
        # Common dates: 2026-05-19, 2026-05-20.
        assert {r["date"] for r in jaaa_jbbb} == {"2026-05-19", "2026-05-20"}
        by_date = {r["date"]: r for r in jaaa_jbbb}
        # Ratio = mezz / AAA = 48 / 50 = 0.96
        assert by_date["2026-05-19"]["price_ratio"] == pytest.approx(0.96)
        assert by_date["2026-05-19"]["aaa_price"] == 50.0
        assert by_date["2026-05-19"]["mezz_price"] == 48.0
        # 49 / 50.5
        assert by_date["2026-05-20"]["price_ratio"] == pytest.approx(
            round(49.0 / 50.5, 6)
        )

    def test_both_pairs_emit_when_data_present(self, fresh_db):
        _seed_etf("mkt_JAAA", [("2026-05-20", 50.0)])
        _seed_etf("mkt_JBBB", [("2026-05-20", 49.0)])
        _seed_etf("mkt_CLOA", [("2026-05-20", 100.0)])
        out = clo.compute_clo_spread_proxy()
        pairs = {(r["aaa_series"], r["mezz_series"]) for r in out}
        assert pairs == {
            ("mkt_JAAA", "mkt_JBBB"),
            ("mkt_CLOA", "mkt_JBBB"),
        }

    def test_lookback_caps_at_252_rows_per_series(self, fresh_db):
        # The function pulls LIMIT 252 per series. With more inputs only the
        # most-recent 252 are intersected; verify the function returns at most
        # 252 rows for a single pair.
        from datetime import date, timedelta
        start = date(2024, 1, 1)
        points = [
            ((start + timedelta(days=i)).isoformat(), 50.0 + i * 0.01)
            for i in range(300)
        ]
        _seed_etf("mkt_JAAA", points)
        _seed_etf("mkt_JBBB", points)
        out = clo.compute_clo_spread_proxy()
        # JAAA/JBBB pair contributes at most 252 dates.
        jaaa_jbbb = [r for r in out if r["aaa_series"] == "mkt_JAAA"]
        assert len(jaaa_jbbb) <= 252


# ─── get_clo_edgar_filings ────────────────────────────────────────────────────
class TestGetCloEdgarFilings:
    def test_returns_empty_when_nothing_matches(self, fresh_db):
        _seed_edgar("a1", asset_class="auto loan",
                    description="Carvana auto loan pricing supplement")
        assert clo.get_clo_edgar_filings() == []

    def test_matches_clo_in_asset_class(self, fresh_db):
        _seed_edgar("c1", asset_class="clo",
                    description="Some pricing")
        _seed_edgar("c2", asset_class="auto loan", description="other")
        rows = clo.get_clo_edgar_filings()
        accs = {r["accession_no"] for r in rows}
        assert accs == {"c1"}

    def test_matches_collateralized_loan_in_description(self, fresh_db):
        _seed_edgar("c1", asset_class="other",
                    description="Collateralized loan obligation prospectus")
        _seed_edgar("c2", asset_class="other",
                    description="Auto retail loan pricing")
        rows = clo.get_clo_edgar_filings()
        accs = {r["accession_no"] for r in rows}
        assert accs == {"c1"}

    def test_matches_broadly_syndicated_keyword_in_asset_class(self, fresh_db):
        _seed_edgar("c1", asset_class="broadly syndicated loan",
                    description="prospectus")
        rows = clo.get_clo_edgar_filings()
        assert {r["accession_no"] for r in rows} == {"c1"}

    def test_orders_by_filed_at_descending(self, fresh_db):
        _seed_edgar("c-old", asset_class="clo",
                    description="x", filed_at="2026-01-05")
        _seed_edgar("c-new", asset_class="clo",
                    description="x", filed_at="2026-05-28")
        _seed_edgar("c-mid", asset_class="clo",
                    description="x", filed_at="2026-03-15")
        rows = clo.get_clo_edgar_filings()
        assert [r["accession_no"] for r in rows] == ["c-new", "c-mid", "c-old"]

    def test_limit_caps_results(self, fresh_db):
        for i in range(5):
            _seed_edgar(f"c{i}", asset_class="clo",
                        description="x", filed_at=f"2026-05-{20+i:02d}")
        rows = clo.get_clo_edgar_filings(limit=2)
        assert len(rows) == 2

    def test_returns_selected_columns(self, fresh_db):
        _seed_edgar("c1", asset_class="clo",
                    description="CLO prospectus", company_name="CLO Trust 2026-1")
        rows = clo.get_clo_edgar_filings()
        assert rows
        row = rows[0]
        expected_keys = {
            "accession_no", "company_name", "form_type", "filed_at",
            "description", "url", "asset_class",
        }
        assert expected_keys <= set(row.keys())
