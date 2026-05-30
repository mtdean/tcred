"""Cover data/percentiles.py + /api/percentiles."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from cache import db
from data import percentiles


NOW = "2026-05-29T12:00:00+00:00"


def _seed(series_id, date_value_pairs):
    for d, v in date_value_pairs:
        db.upsert_metric({
            "series_id": series_id, "label": series_id, "category": "x",
            "date": d, "value": v, "fetched_at": NOW,
        })


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class TestComputePercentile:
    def test_returns_none_for_missing_series(self, fresh_db):
        assert percentiles.compute_percentile("MISSING") is None

    def test_single_observation_returns_n_1_pct_50(self, fresh_db):
        _seed("X", [(_today_iso(), 4.5)])
        out = percentiles.compute_percentile("X")
        assert out["n_obs"] == 1
        assert out["percentile"] == 50.0
        assert out["value"] == 4.5

    def test_strict_less_than_semantics(self, fresh_db):
        # Latest 4.5 with priors [1, 2, 3, 4] → 4 of 5 are strictly less → 80.
        days = [(date.fromisoformat(_today_iso()) - timedelta(days=i)).isoformat()
                for i in range(5)]
        # Order so the latest date carries value 4.5.
        _seed("X", list(zip(days[::-1], [1.0, 2.0, 3.0, 4.0, 4.5])))
        out = percentiles.compute_percentile("X")
        assert out["n_obs"] == 5
        assert out["percentile"] == 80.0

    def test_ties_at_top_pull_below_100(self, fresh_db):
        # Latest tied with 1 of 4 priors: [2, 3, 3, 3] → 2 strictly below → 50.
        days = [(date.fromisoformat(_today_iso()) - timedelta(days=i)).isoformat()
                for i in range(4)]
        _seed("X", list(zip(days[::-1], [2.0, 3.0, 3.0, 3.0])))
        out = percentiles.compute_percentile("X")
        assert out["n_obs"] == 4
        assert out["percentile"] == 25.0  # 1 of 4 strictly less

    def test_constant_series_yields_zero(self, fresh_db):
        # All values equal → no value is strictly less → 0.
        days = [(date.fromisoformat(_today_iso()) - timedelta(days=i)).isoformat()
                for i in range(5)]
        _seed("X", list(zip(days[::-1], [3.0] * 5)))
        out = percentiles.compute_percentile("X")
        assert out["percentile"] == 0.0
        assert out["min"] == out["max"] == out["median"] == 3.0

    def test_window_clips_old_observations(self, fresh_db):
        today = date.fromisoformat(_today_iso())
        # An old very-high value outside the window must NOT push percentile down.
        far_past = (today - timedelta(days=3000)).isoformat()
        recent = [(today - timedelta(days=i)).isoformat() for i in range(5)]
        _seed("X", [(far_past, 999.0)] + list(zip(recent[::-1], [1, 2, 3, 4, 4.5])))
        out = percentiles.compute_percentile("X", window_days=1825)
        assert out["n_obs"] == 5  # the 999 is outside the 5y window
        assert out["percentile"] == 80.0

    def test_reports_min_max_median(self, fresh_db):
        today = date.fromisoformat(_today_iso())
        recent = [(today - timedelta(days=i)).isoformat() for i in range(5)]
        _seed("X", list(zip(recent[::-1], [1.0, 2.0, 3.0, 4.0, 5.0])))
        out = percentiles.compute_percentile("X")
        assert out["min"] == 1.0
        assert out["max"] == 5.0
        assert out["median"] == 3.0


class TestBatch:
    def test_returns_dict_keyed_by_series_id_skipping_missing(self, fresh_db):
        today = date.fromisoformat(_today_iso())
        _seed("A", [(today.isoformat(), 1.0)])
        out = percentiles.compute_percentiles(["A", "MISSING", "B"])
        assert set(out) == {"A"}


class TestPercentilesRoute:
    def test_endpoint_returns_payload(self, api_client, fresh_db):
        today = date.fromisoformat(_today_iso())
        for d, v in [
            ((today - timedelta(days=2)).isoformat(), 1.0),
            ((today - timedelta(days=1)).isoformat(), 2.0),
            (today.isoformat(), 3.0),
        ]:
            db.upsert_metric({
                "series_id": "X", "label": "x", "category": "rates",
                "date": d, "value": v, "fetched_at": NOW,
            })
        resp = api_client.get("/api/percentiles?series_ids=X")
        assert resp.status_code == 200
        data = resp.json()
        assert "X" in data["series"]
        assert data["series"]["X"]["percentile"] == pytest.approx(66.7, abs=0.5)

    def test_window_param_validated(self, api_client, fresh_db):
        resp = api_client.get("/api/percentiles?series_ids=X&window_days=5")
        assert resp.status_code == 422  # below minimum
