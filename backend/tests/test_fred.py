"""Cover data/fred.py — FRED API fetcher + forward curve + SOFR helpers.

Strategy:
  * Pure `_compounded_sofr_1y` gets hand-computed value tests.
  * `fetch_fred_series` is exercised against a stub Fred class injected via
    monkeypatch — no real fredapi call.
  * DB query helpers seed the metrics table and check the returned shape.
"""

from __future__ import annotations

import importlib
import sys
from datetime import date as date_cls

import pandas as pd
import pytest

from cache import db
from data import fred as fr


NOW = "2026-05-29T12:00:00+00:00"


# ─── _compounded_sofr_1y ──────────────────────────────────────────────────────
class TestCompoundedSofr1y:
    def test_returns_none_when_no_anchor_observation(self):
        # Only one obs total — bisect can't find an obs <= target.
        idx_dates = ["2026-05-01"]
        idx_map = {"2026-05-01": 1.10}
        # Target = 2026-05-01 - 365 days = 2025-05-01; no obs at-or-before that.
        out = fr._compounded_sofr_1y(idx_dates, idx_map, "2026-05-01", 1.10)
        assert out is None

    def test_known_value(self):
        # Build a synthetic index: 1.00 at t-365, 1.05 today → 5% growth.
        # Formula: (1.05 / 1.00 - 1) * (360 / 365) * 100 ≈ 4.9315%.
        idx_dates = ["2025-05-29", "2026-05-29"]
        idx_map = {"2025-05-29": 1.00, "2026-05-29": 1.05}
        out = fr._compounded_sofr_1y(idx_dates, idx_map, "2026-05-29", 1.05)
        expected = (1.05 / 1.00 - 1.0) * (360.0 / 365.0) * 100.0
        assert out == pytest.approx(expected, abs=1e-6)

    def test_zero_start_value_returns_none(self):
        idx_dates = ["2025-05-29", "2026-05-29"]
        idx_map = {"2025-05-29": 0.0, "2026-05-29": 1.05}
        assert fr._compounded_sofr_1y(idx_dates, idx_map, "2026-05-29", 1.05) is None

    def test_picks_most_recent_obs_at_or_before_target(self):
        # Target is 2026-05-29 - 365 days = 2025-05-29. The function should
        # pick the 2025-05-25 anchor (last <= target), not 2025-06-01.
        idx_dates = ["2025-05-25", "2025-06-01", "2026-05-29"]
        idx_map = {"2025-05-25": 1.00, "2025-06-01": 1.01, "2026-05-29": 1.05}
        out = fr._compounded_sofr_1y(idx_dates, idx_map, "2026-05-29", 1.05)
        # Anchor is 2025-05-25 → days = 369.
        days = (date_cls.fromisoformat("2026-05-29")
                - date_cls.fromisoformat("2025-05-25")).days
        expected = (1.05 / 1.00 - 1.0) * (360.0 / days) * 100.0
        assert out == pytest.approx(expected, abs=1e-6)


# ─── fetch_fred_series ───────────────────────────────────────────────────────
class TestFetchFredSeries:
    def test_returns_zero_when_key_empty(self, fresh_db, monkeypatch):
        from config import settings as s
        monkeypatch.setattr(s, "FRED_API_KEY", "")
        n = fr.fetch_fred_series()
        assert n == 0

    def test_persists_rows_for_each_series(self, fresh_db, monkeypatch):
        """A stub Fred returns a pandas series; verify metrics rows land."""
        # The function calls `from fredapi import Fred` at runtime; we patch
        # the fredapi module so import returns our stub class.
        idx = pd.to_datetime(["2026-05-27", "2026-05-28"])
        series_data = pd.Series([4.5, 4.6], index=idx)

        class StubFred:
            def __init__(self, *args, **kwargs):
                pass

            def get_series(self, series_id, observation_start=None):
                return series_data

        # Inject a fake `fredapi` module so the `from fredapi import Fred`
        # inside fetch_fred_series resolves to our stub.
        import types
        fake_mod = types.ModuleType("fredapi")
        fake_mod.Fred = StubFred
        monkeypatch.setitem(sys.modules, "fredapi", fake_mod)

        monkeypatch.setattr(fr, "load_data_sources", lambda: {
            "fred_series": [
                {"id": "DGS10", "label": "10Y Treasury", "category": "rates"},
                {"id": "DGS2", "label": "2Y Treasury", "category": "rates"},
            ],
        })

        n = fr.fetch_fred_series()
        # 2 series × 2 obs each = 4 rows.
        assert n == 4

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT series_id, date, value FROM metrics ORDER BY series_id, date"
            ).fetchall()
        by_series = {}
        for r in rows:
            by_series.setdefault(r["series_id"], []).append((r["date"], r["value"]))
        assert by_series["DGS10"] == [("2026-05-27", 4.5), ("2026-05-28", 4.6)]
        assert by_series["DGS2"] == [("2026-05-27", 4.5), ("2026-05-28", 4.6)]

    def test_one_series_error_does_not_block_others(self, fresh_db, monkeypatch):
        idx = pd.to_datetime(["2026-05-28"])
        ok_series = pd.Series([4.5], index=idx)

        class StubFred:
            def __init__(self, *args, **kwargs):
                pass

            def get_series(self, series_id, observation_start=None):
                if series_id == "BAD":
                    raise RuntimeError("not found")
                return ok_series

        import types
        fake_mod = types.ModuleType("fredapi")
        fake_mod.Fred = StubFred
        monkeypatch.setitem(sys.modules, "fredapi", fake_mod)
        monkeypatch.setattr(fr, "load_data_sources", lambda: {
            "fred_series": [
                {"id": "BAD", "label": "x", "category": "rates"},
                {"id": "GOOD", "label": "y", "category": "rates"},
            ],
        })

        n = fr.fetch_fred_series()
        assert n == 1  # only GOOD landed
        with db.get_conn() as conn:
            rows = conn.execute("SELECT series_id FROM metrics").fetchall()
        assert {r["series_id"] for r in rows} == {"GOOD"}


# ─── get_latest_fred_values ──────────────────────────────────────────────────
class TestGetLatestFredValues:
    def test_picks_most_recent_per_series(self, fresh_db):
        # Two series, multiple dates each — must return the latest per series.
        for d, v in [("2026-05-27", 4.5), ("2026-05-28", 4.6)]:
            db.upsert_metric({
                "series_id": "DGS10", "label": "10Y", "category": "rates",
                "date": d, "value": v, "fetched_at": NOW,
            })
        db.upsert_metric({
            "series_id": "DGS2", "label": "2Y", "category": "rates",
            "date": "2026-05-26", "value": 4.10, "fetched_at": NOW,
        })

        rows = fr.get_latest_fred_values()
        by_sid = {r["series_id"]: r for r in rows}
        assert by_sid["DGS10"]["value"] == 4.6
        assert by_sid["DGS10"]["date"] == "2026-05-28"
        assert by_sid["DGS2"]["value"] == 4.10

    def test_empty_db_returns_empty_list(self, fresh_db):
        assert fr.get_latest_fred_values() == []


# ─── get_series_history ──────────────────────────────────────────────────────
class TestGetSeriesHistory:
    def test_returns_ascending_within_limit(self, fresh_db):
        for d, v in [("2026-05-25", 1.0), ("2026-05-26", 2.0), ("2026-05-27", 3.0)]:
            db.upsert_metric({
                "series_id": "X", "label": "x", "category": "rates",
                "date": d, "value": v, "fetched_at": NOW,
            })
        out = fr.get_series_history("X", limit=10)
        # DB pulls DESC then the function reverses → ascending by date.
        assert [r["date"] for r in out] == ["2026-05-25", "2026-05-26", "2026-05-27"]
        assert [r["value"] for r in out] == [1.0, 2.0, 3.0]

    def test_limit_clamps_to_most_recent(self, fresh_db):
        for d, v in [("2026-05-25", 1.0), ("2026-05-26", 2.0), ("2026-05-27", 3.0)]:
            db.upsert_metric({
                "series_id": "X", "label": "x", "category": "rates",
                "date": d, "value": v, "fetched_at": NOW,
            })
        out = fr.get_series_history("X", limit=2)
        # The 2 most-recent rows, then reversed → ascending order: 26, 27.
        assert [r["date"] for r in out] == ["2026-05-26", "2026-05-27"]

    def test_unknown_series_returns_empty(self, fresh_db):
        assert fr.get_series_history("MISSING") == []


# ─── get_forward_curve ───────────────────────────────────────────────────────
class TestGetForwardCurve:
    def test_returns_three_snapshots_per_tenor(self, fresh_db):
        # Seed one tenor (DGS10) with enough daily obs to populate today,
        # 6mo and 1yr buckets (_OBS_1YR + 1 = 253 rows minimum at the far end).
        # We seed exactly 253 obs.
        base = date_cls(2025, 1, 1)
        from datetime import timedelta as td
        for i in range(260):
            d = (base + td(days=i)).isoformat()
            db.upsert_metric({
                "series_id": "DGS10", "label": "10Y", "category": "rates",
                "date": d, "value": 4.0 + i * 0.001, "fetched_at": NOW,
            })

        out = fr.get_forward_curve()
        # Today bucket should be populated; others may or may not be depending
        # on _OBS counts. With 260 obs we get all three.
        assert out["today"]["10Y"] is not None
        assert out["six_months_ago"]["10Y"] is not None
        assert out["one_year_ago"]["10Y"] is not None
        # Sanity: today's value > one-year-ago's (ascending series).
        assert out["today"]["10Y"] > out["one_year_ago"]["10Y"]
        # Tenors include the configured list.
        assert "10Y" in out["tenors"]
        assert out["as_of"]["today"] is not None

    def test_missing_tenor_returns_none(self, fresh_db):
        # No obs at all — every tenor should be None and as_of mostly null.
        out = fr.get_forward_curve()
        for tenor in out["tenors"]:
            assert out["today"][tenor] is None
            assert out["six_months_ago"][tenor] is None
            assert out["one_year_ago"][tenor] is None


# ─── get_sofr_rates ──────────────────────────────────────────────────────────
class TestGetSofrRates:
    def test_merges_indices_with_term_averages(self, fresh_db):
        # Build a 366-day SOFR index so the 1Y compounded value can resolve
        # for the last observation.
        base = date_cls(2025, 5, 28)
        from datetime import timedelta as td
        idx_value = 1.0
        last_date = None
        for i in range(366):
            d = (base + td(days=i)).isoformat()
            idx_value *= 1.0001  # ~3.6% annualized
            last_date = d
            db.upsert_metric({
                "series_id": "SOFRINDEX", "label": "SOFR index",
                "category": "rates", "date": d, "value": idx_value,
                "fetched_at": NOW,
            })
        # Add a 1M average on the last date so 'm1' lights up.
        db.upsert_metric({
            "series_id": "SOFR30DAYAVG", "label": "1M", "category": "rates",
            "date": last_date, "value": 5.10, "fetched_at": NOW,
        })

        out = fr.get_sofr_rates(limit=400)
        # The last row must have all three components populated.
        last = out[-1]
        assert last["m1"] == pytest.approx(5.10)
        assert last["m3"] is None  # not seeded
        assert last["y1"] is not None
        # Should be roughly in the 3-4% region given our growth rate.
        assert 2.0 < last["y1"] < 6.0

    def test_skips_rows_with_no_components(self, fresh_db):
        # Only index without 1M/3M averages AND too short to compound 1Y.
        db.upsert_metric({
            "series_id": "SOFRINDEX", "label": "SOFR index", "category": "rates",
            "date": "2026-05-28", "value": 1.0, "fetched_at": NOW,
        })
        # No 1M/3M/y1 → row should not be emitted at all.
        out = fr.get_sofr_rates()
        assert out == []

    def test_limit_returns_tail(self, fresh_db):
        # 5 dates of m1 only → out should have 5 rows; limit=2 → tail 2.
        for d, v in [
            ("2026-05-24", 5.0), ("2026-05-25", 5.05),
            ("2026-05-26", 5.10), ("2026-05-27", 5.15),
            ("2026-05-28", 5.20),
        ]:
            # SOFRINDEX is the driver of the row loop — seed both.
            db.upsert_metric({
                "series_id": "SOFRINDEX", "label": "idx", "category": "rates",
                "date": d, "value": 1.0, "fetched_at": NOW,
            })
            db.upsert_metric({
                "series_id": "SOFR30DAYAVG", "label": "m1", "category": "rates",
                "date": d, "value": v, "fetched_at": NOW,
            })

        out = fr.get_sofr_rates(limit=2)
        assert len(out) == 2
        assert [r["date"] for r in out] == ["2026-05-27", "2026-05-28"]
