"""
Cover data/scorecard.py — consumer health scorecard.
"""

from __future__ import annotations

import pytest

from cache import db
from data import scorecard

NOW = "2026-09-18T12:00:00+00:00"


def _seed_series(conn, series_id, values, start_year=2021, freq_month_step=1):
    y, m = start_year, 1
    for v in values:
        conn.execute(
            "INSERT OR REPLACE INTO metrics (series_id, date, value, label, category, fetched_at) "
            "VALUES (?, ?, ?, ?, 'macro', ?)",
            (series_id, f"{y:04d}-{m:02d}-01", v, series_id, NOW),
        )
        m += freq_month_step
        while m > 12:
            m -= 12
            y += 1
    conn.commit()


class TestHelpers:
    def test_stress_percentile_orientation(self):
        hist = list(range(100))
        # higher = worse: value at the top of the range ≈ p100
        assert scorecard._stress_percentile(hist, 99, +1) > 95
        # lower = worse: same value ≈ p0
        assert scorecard._stress_percentile(hist, 99, -1) < 5

    def test_percentile_needs_history(self):
        assert scorecard._stress_percentile([1.0] * 5, 1.0, +1) is None

    def test_trend_orientation(self):
        rising = [(f"2026-{m:02d}-01", float(m)) for m in range(1, 10)]
        assert scorecard._trend(rising, 3, +1) == "worsening"
        assert scorecard._trend(rising, 3, -1) == "improving"


class TestCompute:
    def test_composite_and_indicators(self, db_conn):
        # 60 months of saving-rate history ending low → high stress (orient -1).
        vals = [8.0] * 59 + [3.0]
        _seed_series(db_conn, "PSAVERT", vals)
        out = scorecard.compute_consumer_scorecard()
        sav = next(i for i in out["indicators"] if i["id"] == "PSAVERT")
        assert sav["value"] == 3.0
        assert sav["stress_percentile"] > 90
        assert sav["trend"] == "worsening"
        assert out["composite_stress"] == pytest.approx(sav["stress_percentile"])

    def test_trust_aggregate_included(self, db_conn):
        for i, period in enumerate(["2026-06-30", "2026-07-31", "2026-08-31"]):
            db.upsert_trust_performance({
                "accession_no": f"acc-{i}", "cik": 1,
                "trust_name": "CHASE ISSUANCE TRUST", "segment": "credit_card",
                "period_end": period, "filed_at": period,
                "metric": "delinq_30plus_rate", "value": 1.0 + i * 0.2,
                "url": "http://x", "fetched_at": NOW,
            })
        out = scorecard.compute_consumer_scorecard()
        card = next(
            i for i in out["indicators"] if i["id"] == "trust_credit_card_delinq_30plus_rate"
        )
        assert card["value"] == pytest.approx(1.4)
        assert card["trend"] == "worsening"
        assert card["stress_percentile"] is None  # only 3 obs

    def test_empty_db(self, fresh_db):
        out = scorecard.compute_consumer_scorecard()
        assert out["indicators"] == []
        assert out["composite_stress"] is None
