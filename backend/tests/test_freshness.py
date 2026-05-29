"""Cover data/freshness.py + /api/freshness."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from cache import db
from data import freshness


NOW = "2026-05-29T12:00:00+00:00"


def _seed(series_id, latest_date, value=1.0):
    db.upsert_metric({
        "series_id": series_id, "label": series_id, "category": "x",
        "date": latest_date, "value": value, "fetched_at": NOW,
    })


class TestStatusFor:
    @pytest.mark.parametrize("days, cadence, expected", [
        (0, "daily", "fresh"),
        (3, "daily", "fresh"),
        (10, "daily", "stale"),
        (20, "daily", "dead"),
        (10, "weekly", "fresh"),
        (20, "weekly", "stale"),
        (40, "weekly", "dead"),
        (40, "monthly", "fresh"),
        (75, "monthly", "stale"),
        (120, "monthly", "dead"),
        (90, "quarterly", "fresh"),
        (140, "quarterly", "stale"),
        (220, "quarterly", "dead"),
        # unknown cadence falls back to monthly thresholds.
        (10, "biweekly", "fresh"),
    ])
    def test_thresholds(self, days, cadence, expected):
        assert freshness._status_for(days, cadence) == expected


class TestFredSeriesFreshness:
    def test_missing_series_marked_missing(self, fresh_db):
        rows = freshness.fred_series_freshness()
        # Every configured FRED series is missing on a fresh DB.
        statuses = {r["status"] for r in rows}
        assert "missing" in statuses
        assert all(r["latest_date"] is None for r in rows if r["status"] == "missing")

    def test_fresh_daily_series(self, fresh_db):
        today = freshness._today().isoformat()
        _seed("DGS10", today, 4.5)
        rows = {r["series_id"]: r for r in freshness.fred_series_freshness()}
        assert rows["DGS10"]["status"] == "fresh"
        assert rows["DGS10"]["days_since"] == 0

    def test_stale_and_dead_daily(self, fresh_db):
        today = freshness._today()
        _seed("DGS10", (today - timedelta(days=10)).isoformat())
        _seed("DGS2",  (today - timedelta(days=60)).isoformat())
        rows = {r["series_id"]: r for r in freshness.fred_series_freshness()}
        assert rows["DGS10"]["status"] == "stale"
        assert rows["DGS2"]["status"] == "dead"


class TestSummarize:
    def test_counts_each_status(self):
        rows = [
            {"status": "fresh"}, {"status": "fresh"},
            {"status": "stale"}, {"status": "dead"}, {"status": "missing"},
        ]
        s = freshness._summarize(rows)
        assert s == {"fresh": 2, "stale": 1, "dead": 1, "missing": 1}


class TestJobFreshness:
    def test_returns_hours_since_latest_run(self, fresh_db):
        rid = db.start_job_run("market")
        db.finish_job_run(rid, "success", rows_ingested=5)
        out = freshness.job_freshness()
        assert len(out) == 1
        assert out[0]["job_id"] == "market"
        assert out[0]["status"] == "success"
        assert out[0]["hours_since"] is not None and out[0]["hours_since"] >= 0


class TestFreshnessReport:
    def test_top_level_shape(self, fresh_db):
        rep = freshness.freshness_report()
        assert set(rep) == {"as_of", "series", "summary", "jobs"}
        assert all(k in rep["summary"] for k in ("fresh", "stale", "dead", "missing"))


class TestFreshnessRoute:
    def test_endpoint_returns_report(self, api_client, fresh_db):
        resp = api_client.get("/api/freshness")
        assert resp.status_code == 200
        data = resp.json()
        assert "series" in data and "summary" in data and "jobs" in data
