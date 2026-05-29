"""
Cover data/h8.py — Fed H.8 weekly bank-credit analytics computed on top of the
`metrics` table already populated by the FRED fetcher.

Strategy:
  * Seed the metrics table with synthetic weekly TOTLL / CONSUMER / etc. rows.
  * Run compute_h8_metrics() / compute_credit_impulse() and assert the derived
    fields (WoW, YoY, 4w MA, ΔQ/GDP × 400) match hand-computed values.
  * Pure helper `_round` gets a direct value-table test.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cache import db
from data import h8


NOW = "2026-05-29T12:00:00+00:00"


def _seed_weekly(series_id: str, values: list[float], label: str = "lbl",
                 start: str = "2024-05-31") -> list[str]:
    """Insert one weekly row per value, oldest first. Returns the dates used."""
    start_d = date.fromisoformat(start)
    dates: list[str] = []
    for i, v in enumerate(values):
        d = (start_d + timedelta(days=7 * i)).isoformat()
        dates.append(d)
        db.upsert_metric({
            "series_id": series_id, "label": label, "category": "h8_bank_credit",
            "date": d, "value": v, "fetched_at": NOW,
        })
    return dates


# ─── Pure helper: _round ──────────────────────────────────────────────────────
class TestRound:
    @pytest.mark.parametrize("v, digits, expected", [
        (1.23456, 2, 1.23),
        (1.23456, 4, 1.2346),
        (0.0, 2, 0.0),
        (None, 2, None),
    ])
    def test_value_table(self, v, digits, expected):
        assert h8._round(v, digits) == expected

    def test_nan_passes_through_as_none(self):
        # _round explicitly handles pd.notna(); NaN must return None.
        import math
        assert h8._round(math.nan, 2) is None


# ─── compute_h8_metrics ───────────────────────────────────────────────────────
class TestComputeH8Metrics:
    def test_empty_db_returns_empty_list(self, fresh_db):
        assert h8.compute_h8_metrics() == []

    def test_latest_value_and_wow_metrics(self, fresh_db):
        # Seed TOTLL with 3 weeks: 100, 102, 105 → latest=105, WoW=+3, WoW%=~2.94
        dates = _seed_weekly("TOTLL", [100.0, 102.0, 105.0],
                             label="Total Loans & Leases")
        out = h8.compute_h8_metrics()
        # Only TOTLL was seeded; the other 5 series in _H8_SERIES yield nothing.
        assert len(out) == 1
        r = out[0]
        assert r["series_id"] == "TOTLL"
        assert r["label"] == "Total Loans & Leases"
        assert r["latest_date"] == dates[-1]
        assert r["latest_value"] == 105.0
        assert r["wow_change"] == pytest.approx(3.0)
        assert r["wow_pct"] == pytest.approx((3.0 / 102.0) * 100.0, abs=1e-3)
        # 4-week MA over the 3 we have = mean(100, 102, 105) = 102.33
        assert r["ma4w"] == pytest.approx(102.33, abs=0.01)
        # YoY needs 52 prior weeks — with only 3 points it's NaN → None.
        assert r["yoy_pct"] is None

    def test_history_field_contains_all_rows_in_chronological_order(self, fresh_db):
        _seed_weekly("CONSUMER", [10.0, 11.0, 12.0], label="Consumer")
        out = h8.compute_h8_metrics()
        history = out[0]["history"]
        assert len(history) == 3
        # Oldest first
        assert history[0]["value"] == 10.0
        assert history[-1]["value"] == 12.0

    def test_yoy_with_full_year_of_data(self, fresh_db):
        # 53 weeks of data: linear ramp 100..152. Latest should have YoY ≈ 52%.
        values = [100.0 + i for i in range(53)]
        _seed_weekly("TOTLL", values)
        out = h8.compute_h8_metrics()
        r = out[0]
        assert r["yoy_pct"] == pytest.approx(52.0, abs=1e-3)

    def test_multiple_series_returned_in_h8_series_order(self, fresh_db):
        _seed_weekly("REALLN", [50.0, 51.0])
        _seed_weekly("TOTLL", [100.0, 102.0])
        out = h8.compute_h8_metrics()
        # h8._H8_SERIES order: TOTLL first, then CONSUMER, ..., then REALLN.
        ids = [r["series_id"] for r in out]
        assert ids == ["TOTLL", "REALLN"]

    def test_skips_series_with_no_data(self, fresh_db):
        _seed_weekly("DPSACBW027SBOG", [800.0, 810.0])
        out = h8.compute_h8_metrics()
        assert {r["series_id"] for r in out} == {"DPSACBW027SBOG"}


# ─── compute_credit_impulse ───────────────────────────────────────────────────
class TestCreditImpulse:
    def test_returns_empty_when_totll_missing(self, fresh_db):
        _seed_weekly("GDP", [25_000.0])
        assert h8.compute_credit_impulse() == []

    def test_returns_empty_when_gdp_missing(self, fresh_db):
        _seed_weekly("TOTLL", [100.0, 102.0])
        assert h8.compute_credit_impulse() == []

    def test_quarterly_resample_and_annualized_ratio(self, fresh_db):
        # 26 weeks of TOTLL spanning ~2 quarters: weekly +1 each week.
        # Q1 mean ≈ middle of first 13 weeks; Q2 mean ≈ middle of last 13 weeks.
        # ΔQ ≈ 13 (since means are 13 weeks apart at +1/wk).
        values = [100.0 + i for i in range(30)]
        _seed_weekly("TOTLL", values, start="2025-01-04")
        # Insert quarterly GDP values that resample picks up at QE.
        db.upsert_metric({
            "series_id": "GDP", "label": "gdp", "category": "macro",
            "date": "2025-03-31", "value": 1000.0, "fetched_at": NOW,
        })
        db.upsert_metric({
            "series_id": "GDP", "label": "gdp", "category": "macro",
            "date": "2025-06-30", "value": 1000.0, "fetched_at": NOW,
        })

        out = h8.compute_credit_impulse()
        assert len(out) >= 1
        # Each emitted row has shape {date, total_loans, qoq_change, credit_impulse}.
        last = out[-1]
        assert "credit_impulse" in last
        assert "total_loans" in last
        # Credit impulse should be a real finite number for the last quarter
        # where qoq_change is defined.
        non_null = [r for r in out if r["credit_impulse"] is not None]
        assert non_null, "expected at least one defined credit_impulse value"
        for r in non_null:
            # Hand-check: impulse = qoq / gdp * 400. With qoq ≈ 13 and gdp=1000
            # → impulse ≈ 5.2.
            assert r["credit_impulse"] == pytest.approx(
                r["qoq_change"] / 1000.0 * 400.0, abs=1e-3
            )

    def test_tail_limit_capped_at_20(self, fresh_db):
        # Seed 30 quarters worth of weekly data → resample yields ≥20 quarters
        # but compute_credit_impulse only returns the last 20 rows.
        values = [100.0 + i * 0.1 for i in range(30 * 13)]  # ~30 qtrs
        _seed_weekly("TOTLL", values, start="2018-01-05")
        db.upsert_metric({
            "series_id": "GDP", "label": "gdp", "category": "macro",
            "date": "2018-03-31", "value": 1000.0, "fetched_at": NOW,
        })
        db.upsert_metric({
            "series_id": "GDP", "label": "gdp", "category": "macro",
            "date": "2025-12-31", "value": 1000.0, "fetched_at": NOW,
        })
        out = h8.compute_credit_impulse()
        assert len(out) <= 20
