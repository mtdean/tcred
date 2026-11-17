"""Tests for data/edgar_distress.py — 8-K Item 2.04 / 1.03 counts."""

from __future__ import annotations

import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from cache import db
from data import edgar_distress as ed


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr(ed.time, "sleep", lambda s: None)


def _hit(adsh, form, items, fdate, file_type=None, seq=1):
    return {
        "_id": f"{adsh}:doc{seq}.htm",
        "_source": {
            "adsh": adsh,
            "form": form,
            "root_forms": ["8-K"],
            "file_type": file_type or form,
            "items": items,
            "file_date": fdate,
        },
    }


def _series(series_id):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics WHERE series_id = ? ORDER BY date",
            (series_id,),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


class TestFilter:
    def test_keeps_original_8k_with_item_and_dedups_exhibits(self):
        hits = [
            _hit("A", "8-K", ["2.04", "9.01"], "2026-09-02"),
            _hit("A", "8-K", ["2.04", "9.01"], "2026-09-02", file_type="EX-4.1", seq=2),
            _hit("B", "8-K/A", ["2.04"], "2026-09-03"),            # amendment
            _hit("C", "8-K", ["1.01", "9.01"], "2026-09-04"),       # text mention only
            _hit("D", "8-K", None, "2026-09-04"),                    # no items
            _hit("E", "8-K", ["1.03", "2.04"], "2026-09-05"),
        ]
        assert ed.filter_filings(hits, "2.04") == {"A": "2026-09-02", "E": "2026-09-05"}
        assert ed.filter_filings(hits, "1.03") == {"E": "2026-09-05"}

    def test_adsh_falls_back_to_id(self):
        h = _hit("X", "8-K", ["1.03"], "2026-01-05")
        del h["_source"]["adsh"]
        assert ed.filter_filings([h], "1.03") == {"X": "2026-01-05"}


class TestBucketing:
    def test_weekly_monday_start_zero_fill_drops_current_week(self):
        dates = ["2026-11-02", "2026-11-08", "2026-11-09", "2026-11-16"]
        # today Tue 2026-11-17 -> current week (Mon 11-16) dropped
        out = ed.weekly_counts(dates, date(2026, 10, 26), date(2026, 11, 17))
        assert out == [("2026-10-26", 0), ("2026-11-02", 2), ("2026-11-09", 1)]

    def test_weekly_skips_week_partially_before_start(self):
        out = ed.weekly_counts(["2026-10-01"], date(2026, 10, 1), date(2026, 10, 13))
        # 10-01 is a Thursday: its week is incomplete in the window -> skipped
        assert out == [("2026-10-05", 0)]

    def test_monthly_first_of_month_drops_current_month(self):
        dates = ["2026-09-01", "2026-09-30", "2026-10-15", "2026-11-03"]
        out = ed.monthly_counts(dates, date(2026, 9, 1), date(2026, 11, 17))
        assert out == [("2026-09-01", 2), ("2026-10-01", 1)]

    def test_monthly_skips_partial_start_month(self):
        out = ed.monthly_counts([], ed.HISTORY_START, date(2004, 11, 2))
        assert out == [("2004-09-01", 0), ("2004-10-01", 0)]

    def test_month_windows(self):
        assert ed.month_windows(date(2026, 1, 20), date(2026, 3, 4)) == [
            (date(2026, 1, 20), date(2026, 1, 31)),
            (date(2026, 2, 1), date(2026, 2, 28)),
            (date(2026, 3, 1), date(2026, 3, 4)),
        ]


class TestSearchPaging:
    def test_pages_until_short_page(self, mocked_responses):
        offsets = []

        def cb(request):
            q = parse_qs(urlparse(request.url).query)
            off = int(q["from"][0])
            offsets.append(off)
            n = 100 if off == 0 else 7
            hits = [_hit(f"{off}-{i}", "8-K", ["2.04"], "2026-09-02") for i in range(n)]
            return (200, {}, json.dumps({"hits": {"total": {"value": 107}, "hits": hits}}))

        mocked_responses.add_callback("GET", ed.EFTS_URL, callback=cb)
        import requests

        with requests.Session() as s:
            hits = ed.search_hits('"Item 2.04"', date(2026, 9, 1), date(2026, 9, 30), s)
        assert len(hits) == 107 and offsets == [0, 100]
        req = mocked_responses.calls[0].request
        assert req.headers["User-Agent"]
        q = parse_qs(urlparse(req.url).query)
        assert q["forms"] == ["8-K"] and q["startdt"] == ["2026-09-01"]

    def test_retries_server_error(self, mocked_responses):
        mocked_responses.add("GET", ed.EFTS_URL, status=500)
        mocked_responses.add("GET", ed.EFTS_URL, json={"hits": {"total": {"value": 0}, "hits": []}})
        import requests

        with requests.Session() as s:
            assert ed.search_hits('"x"', date(2026, 9, 1), date(2026, 9, 2), s) == []
        assert len(mocked_responses.calls) == 2


def _efts_callback(filings_by_item, calls):
    """Serve phrase-query results: every filing for an item comes back on
    both of its queries (dups across queries + an exhibit hit each)."""

    def cb(request):
        q = parse_qs(urlparse(request.url).query)
        calls.append(q)
        start, end = q["startdt"][0], q["enddt"][0]
        phrase = q["q"][0]
        item = next(i for i, (_, _, qs) in ed.ITEMS.items() if phrase in qs)
        hits = []
        for h in filings_by_item[item]:
            if start <= h["_source"]["file_date"] <= end:
                hits.append(h)
                ex = json.loads(json.dumps(h))
                ex["_source"]["file_type"] = "EX-99.1"
                hits.append(ex)
        return (200, {}, json.dumps({"hits": {"total": {"value": len(hits)}, "hits": hits}}))

    return cb


class TestFetch:
    FILINGS = {
        "2.04": [
            _hit("a1", "8-K", ["2.04"], "2026-09-08"),
            _hit("a2", "8-K", ["2.04", "9.01"], "2026-09-10"),
            _hit("a3", "8-K/A", ["2.04"], "2026-09-11"),        # amendment: excluded
            _hit("a4", "8-K", ["8.01"], "2026-09-11"),          # mention only
            _hit("a5", "8-K", ["2.04"], "2026-10-20"),
            _hit("a6", "8-K", ["2.04"], "2026-11-16"),          # current week/month
        ],
        "1.03": [
            _hit("b1", "8-K", ["1.03", "2.04"], "2026-10-05"),
        ],
    }

    def test_full_backfill_on_empty_db(self, fresh_db, monkeypatch):
        seen = {}

        def fake_collect(item, start, end, session):
            seen[item] = (start, end)
            return {}

        monkeypatch.setattr(ed, "collect_filings", fake_collect)
        ed.fetch(today=date(2026, 11, 17))
        assert seen["2.04"] == (ed.HISTORY_START, date(2026, 11, 16))
        weekly = _series("EDGAR_8K_204_WEEKLY")
        assert weekly[0][0] == "2004-08-23" and weekly[-1][0] == "2026-11-09"
        monthly = _series("EDGAR_8K_103_MONTHLY")
        assert monthly[0][0] == "2004-09-01" and monthly[-1][0] == "2026-10-01"

    def test_incremental_counts_dedup_and_partial_drop(self, fresh_db, mocked_responses):
        # Seed so the run is incremental.
        for prefix in ("EDGAR_8K_204", "EDGAR_8K_103"):
            ed.store_series(f"{prefix}_WEEKLY", "x", [("2026-11-02", 99)])
            ed.store_series(f"{prefix}_MONTHLY", "x", [("2026-08-01", 42)])
        calls = []
        mocked_responses.add_callback("GET", ed.EFTS_URL, callback=_efts_callback(self.FILINGS, calls))

        ed.fetch(today=date(2026, 11, 17))

        # 56 days before 11-17 is 09-22 -> re-pull from 09-01 in monthly windows
        assert min(c["startdt"][0] for c in calls) == "2026-09-01"
        assert {c["startdt"][0] for c in calls} == {"2026-09-01", "2026-10-01", "2026-11-01"}
        assert {c["q"][0] for c in calls} == {q for _, _, qs in ed.ITEMS.values() for q in qs}

        m204 = dict(_series("EDGAR_8K_204_MONTHLY"))
        assert m204 == {"2026-08-01": 42, "2026-09-01": 2, "2026-10-01": 1}  # no Nov
        m103 = dict(_series("EDGAR_8K_103_MONTHLY"))
        assert m103["2026-09-01"] == 0 and m103["2026-10-01"] == 1

        w204 = dict(_series("EDGAR_8K_204_WEEKLY"))
        assert w204["2026-09-07"] == 2         # a1 + a2; a3 amendment, a4 mention out
        assert w204["2026-10-19"] == 1
        assert w204["2026-11-02"] == 0         # stale seed overwritten by upsert
        assert "2026-11-16" not in w204        # current week dropped
        assert min(w204) == "2026-09-07"       # 09-01 is Tue -> first full week

    def test_fetch_start_backs_up_over_gap(self, fresh_db):
        for prefix in ("EDGAR_8K_204", "EDGAR_8K_103"):
            ed.store_series(f"{prefix}_WEEKLY", "x", [("2026-03-16", 1)])
        assert ed.fetch_start(date(2026, 11, 17)) == date(2026, 3, 1)


def test_source_declaration():
    s = ed.SOURCE
    assert s.id == "edgar_distress" and s.every_hours == 24
    # Charted by a hand-built Macro panel, not the generic Custom Sources one.
    assert s.panel is None
