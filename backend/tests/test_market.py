"""Cover data/market.py — yfinance batch download + snapshot helper.

Strategy:
  * Patch yfinance.download to return a hand-built pandas DataFrame that mimics
    both the single-ticker (Close column) and multi-ticker (group_by='ticker')
    shapes.
  * fetch_market_data → confirm metrics table fills with mkt_<TICKER> rows.
  * get_market_snapshot → seed the DB directly and check 1d/5d/30d math.
"""

from __future__ import annotations

import sys
import types
from datetime import date as date_cls, timedelta

import pandas as pd
import pytest

from cache import db
from data import market as m


NOW = "2026-05-29T12:00:00+00:00"


def _make_multi_df(tickers: list[str], dates: list[str], values: dict) -> pd.DataFrame:
    """
    Build the multi-index column shape yfinance produces with group_by='ticker':
        columns = MultiIndex[(ticker, field)], rows indexed by date.
    """
    fields = ["Close"]
    cols = pd.MultiIndex.from_product([tickers, fields])
    idx = pd.to_datetime(dates)
    data = {}
    for t in tickers:
        for f in fields:
            data[(t, f)] = [values[t][i] for i, _ in enumerate(dates)]
    return pd.DataFrame(data, index=idx, columns=cols)


def _make_single_df(dates: list[str], closes: list[float]) -> pd.DataFrame:
    """Single-ticker shape: top-level Close column."""
    idx = pd.to_datetime(dates)
    return pd.DataFrame({"Close": closes}, index=idx)


def _install_fake_yfinance(monkeypatch, df: pd.DataFrame, raise_exc=None):
    """Inject a fake yfinance module so `import yfinance as yf` resolves to it."""
    fake = types.ModuleType("yfinance")

    def download(tickers, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        return df

    fake.download = download
    monkeypatch.setitem(sys.modules, "yfinance", fake)


# ─── fetch_market_data ───────────────────────────────────────────────────────
class TestFetchMarketData:
    def test_persists_multi_ticker_close_prices(self, fresh_db, monkeypatch):
        tickers = ["HYG", "LQD"]
        dates = ["2026-05-27", "2026-05-28"]
        values = {"HYG": [82.50, 82.65], "LQD": [110.10, 110.30]}
        df = _make_multi_df(tickers, dates, values)
        _install_fake_yfinance(monkeypatch, df)

        monkeypatch.setattr(m, "load_data_sources", lambda: {
            "market_tickers": [
                {"ticker": "HYG", "label": "HY Corp", "category": "credit_etf"},
                {"ticker": "LQD", "label": "IG Corp", "category": "credit_etf"},
            ],
        })

        n = m.fetch_market_data()
        # 2 tickers × 2 dates = 4 rows.
        assert n == 4

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT series_id, date, value FROM metrics ORDER BY series_id, date"
            ).fetchall()
        by_sid = {}
        for r in rows:
            by_sid.setdefault(r["series_id"], []).append((r["date"], r["value"]))
        assert by_sid["mkt_HYG"] == [("2026-05-27", 82.50), ("2026-05-28", 82.65)]
        assert by_sid["mkt_LQD"] == [("2026-05-27", 110.10), ("2026-05-28", 110.30)]

    def test_single_ticker_shape_reads_top_level_close(self, fresh_db, monkeypatch):
        # With one ticker yfinance returns a regular Close column.
        dates = ["2026-05-27", "2026-05-28"]
        df = _make_single_df(dates, [120.0, 121.5])
        _install_fake_yfinance(monkeypatch, df)
        monkeypatch.setattr(m, "load_data_sources", lambda: {
            "market_tickers": [
                {"ticker": "ABC", "label": "abc", "category": "credit_etf"},
            ],
        })

        n = m.fetch_market_data()
        assert n == 2
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, value FROM metrics WHERE series_id='mkt_ABC' ORDER BY date"
            ).fetchall()
        assert [(r["date"], r["value"]) for r in rows] == [
            ("2026-05-27", 120.0), ("2026-05-28", 121.5),
        ]

    def test_download_exception_returns_zero(self, fresh_db, monkeypatch):
        _install_fake_yfinance(
            monkeypatch, pd.DataFrame(), raise_exc=RuntimeError("net down"),
        )
        monkeypatch.setattr(m, "load_data_sources", lambda: {
            "market_tickers": [
                {"ticker": "HYG", "label": "HY Corp", "category": "credit_etf"},
            ],
        })
        assert m.fetch_market_data() == 0

    def test_per_ticker_processing_error_does_not_block_others(
        self, fresh_db, monkeypatch
    ):
        # Build a multi-DF where only one ticker has data, the other column
        # doesn't exist → accessing it raises and the loop continues.
        tickers = ["HYG"]
        dates = ["2026-05-28"]
        df = _make_multi_df(tickers, dates, {"HYG": [82.0]})
        _install_fake_yfinance(monkeypatch, df)
        monkeypatch.setattr(m, "load_data_sources", lambda: {
            "market_tickers": [
                {"ticker": "HYG", "label": "HY", "category": "credit_etf"},
                # MISSING is not in the DF — column lookup raises but is caught.
                {"ticker": "MISSING", "label": "x", "category": "credit_etf"},
            ],
        })
        # Function pulls tickers list from config — but if a 2-ticker config
        # generates a single-ticker path it would access data['Close']. The
        # code branches on len(tickers): with 2 it does data['MISSING']['Close']
        # which KeyErrors. That's caught and logged.
        n = m.fetch_market_data()
        assert n == 1  # only HYG landed

    def test_yfinance_missing_returns_zero(self, fresh_db, monkeypatch):
        # Force the `import yfinance as yf` to fail by injecting a module that
        # raises ImportError on attribute lookup. The cleanest way is to delete
        # the module and prevent re-import.
        monkeypatch.setitem(sys.modules, "yfinance", None)
        assert m.fetch_market_data() == 0


# ─── get_market_snapshot ──────────────────────────────────────────────────────
class TestGetMarketSnapshot:
    def test_computes_pct_changes_correctly(self, fresh_db, monkeypatch):
        monkeypatch.setattr(m, "load_data_sources", lambda: {
            "market_tickers": [
                {"ticker": "HYG", "label": "HY Corp", "category": "credit_etf"},
            ],
        })
        # Seed 35 days so 30d lookup resolves. Index 0 is the newest.
        # Make prices simple: today=100, 1d ago=98 → +2.04%; 5d=95 → +5.26%;
        # 30d=80 → +25.00%.
        for i in range(35):
            d = (date_cls(2026, 5, 28) - timedelta(days=i)).isoformat()
            # The snapshot reads DESC by date, so prices[0] is today (i=0)…
            # Choose values so the math falls out cleanly:
            #   i=0 → 100, i=1 → 98, i=5 → 95, i=30 → 80, others → 99.
            if i == 0:
                v = 100.0
            elif i == 1:
                v = 98.0
            elif i == 5:
                v = 95.0
            elif i == 30:
                v = 80.0
            else:
                v = 99.0
            db.upsert_metric({
                "series_id": "mkt_HYG", "label": "HY", "category": "credit_etf",
                "date": d, "value": v, "fetched_at": NOW,
            })

        out = m.get_market_snapshot()
        assert len(out) == 1
        r = out[0]
        assert r["ticker"] == "HYG"
        assert r["price"] == 100.0
        # (100 - 98) / 98 * 100 = 2.04
        assert r["chg_1d"] == pytest.approx(2.04, abs=0.01)
        assert r["chg_5d"] == pytest.approx(5.26, abs=0.01)
        assert r["chg_30d"] == pytest.approx(25.00, abs=0.01)

    def test_missing_history_returns_nulls_for_lookbacks(
        self, fresh_db, monkeypatch
    ):
        # Only one row → 1d/5d/30d cannot be computed.
        monkeypatch.setattr(m, "load_data_sources", lambda: {
            "market_tickers": [
                {"ticker": "X", "label": "x", "category": "credit_etf"},
            ],
        })
        db.upsert_metric({
            "series_id": "mkt_X", "label": "x", "category": "credit_etf",
            "date": "2026-05-28", "value": 50.0, "fetched_at": NOW,
        })

        out = m.get_market_snapshot()
        assert len(out) == 1
        r = out[0]
        assert r["price"] == 50.0
        assert r["chg_1d"] is None
        assert r["chg_5d"] is None
        assert r["chg_30d"] is None

    def test_ticker_with_no_rows_is_skipped(self, fresh_db, monkeypatch):
        monkeypatch.setattr(m, "load_data_sources", lambda: {
            "market_tickers": [
                {"ticker": "HAS", "label": "h", "category": "credit_etf"},
                {"ticker": "NONE", "label": "n", "category": "credit_etf"},
            ],
        })
        db.upsert_metric({
            "series_id": "mkt_HAS", "label": "h", "category": "credit_etf",
            "date": "2026-05-28", "value": 10.0, "fetched_at": NOW,
        })

        out = m.get_market_snapshot()
        tickers = {r["ticker"] for r in out}
        assert tickers == {"HAS"}
