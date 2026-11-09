"""Tests for data/cfpb.py — CFPB complaint-volume ingest."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from cache import db
from data import cfpb


@pytest.fixture
def frozen_now(monkeypatch):
    import datetime as dt

    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 10, tzinfo=tz)

    monkeypatch.setattr(cfpb, "datetime", FakeDatetime)


def _seed(series_id: str, month: str, value: float = 1.0):
    db.upsert_metric(
        {
            "series_id": series_id,
            "label": "x",
            "category": "consumer_stress",
            "date": month,
            "value": value,
            "fetched_at": "2026-01-01T00:00:00+00:00",
        }
    )


def _seed_all(month: str):
    for s in cfpb.SERIES:
        _seed(s["series_id"], month)


def _query(call) -> dict:
    return parse_qs(urlparse(call.request.url).query)


def _count_by_month(counts: dict[str, int], fail_product: str | None = None):
    """responses callback: hits.total keyed on date_received_min."""

    def cb(request):
        q = parse_qs(urlparse(request.url).query)
        if fail_product and fail_product in q["product"]:
            return (500, {}, "")
        n = counts.get(q["date_received_min"][0], 0)
        return (200, {}, json.dumps({"hits": {"total": {"value": n, "relation": "eq"}}}))

    return cb


def _months(rows_for: str) -> list[tuple[str, float]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics WHERE series_id = ? ORDER BY date",
            (rows_for,),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


class TestFetch:
    def test_incremental_refetches_revision_window_and_skips_partial_month(
        self, fresh_db, mocked_responses, frozen_now
    ):
        _seed_all("2026-03-01")
        mocked_responses.add_callback(
            "GET",
            cfpb.SEARCH_URL,
            callback=_count_by_month(
                {"2026-01-01": 7000, "2026-02-01": 7500, "2026-03-01": 8000,
                 "2026-04-01": 8145, "2026-05-01": 6886}
            ),
        )

        n = cfpb.fetch_cfpb_complaints()

        # Last stored 2026-03 → re-pull Jan..Mar, then Apr, May; June is partial.
        assert n == 5 * len(cfpb.SERIES)
        assert _months("CFPB_COMPLAINTS_CARD") == [
            ("2026-01-01", 7000.0),
            ("2026-02-01", 7500.0),
            ("2026-03-01", 8000.0),
            ("2026-04-01", 8145.0),
            ("2026-05-01", 6886.0),
        ]
        sent = {_query(c)["date_received_min"][0] for c in mocked_responses.calls}
        assert "2026-06-01" not in sent

    def test_month_bounds_are_inclusive_calendar_month(
        self, fresh_db, mocked_responses, frozen_now
    ):
        _seed_all("2026-05-01")
        mocked_responses.add_callback(
            "GET", cfpb.SEARCH_URL, callback=_count_by_month({})
        )
        cfpb.fetch_cfpb_complaints()

        q = _query(mocked_responses.calls[0])
        assert q["date_received_min"] == ["2026-03-01"]
        assert q["date_received_max"] == ["2026-03-31"]
        assert q["size"] == ["0"]

    def test_product_names_sent_as_repeated_params(
        self, fresh_db, mocked_responses, frozen_now
    ):
        _seed_all("2026-05-01")
        mocked_responses.add_callback(
            "GET", cfpb.SEARCH_URL, callback=_count_by_month({})
        )
        cfpb.fetch_cfpb_complaints()

        q = _query(mocked_responses.calls[0])
        assert set(q["product"]) == set(cfpb.SERIES[0]["products"])

    def test_backfill_skips_leading_zero_months(
        self, fresh_db, mocked_responses, monkeypatch, frozen_now
    ):
        monkeypatch.setattr(cfpb, "START_DATE", "2026-01-01")
        mocked_responses.add_callback(
            "GET",
            cfpb.SEARCH_URL,
            callback=_count_by_month({"2026-03-01": 4, "2026-05-01": 9}),
        )
        cfpb.fetch_cfpb_complaints()

        # Jan/Feb leading zeros dropped; the interior zero (Apr) is real.
        assert _months("CFPB_COMPLAINTS_AUTO") == [
            ("2026-03-01", 4.0),
            ("2026-04-01", 0.0),
            ("2026-05-01", 9.0),
        ]

    def test_one_series_failure_does_not_block_others(
        self, fresh_db, mocked_responses, frozen_now
    ):
        _seed_all("2026-05-01")
        mocked_responses.add_callback(
            "GET",
            cfpb.SEARCH_URL,
            callback=_count_by_month(
                {"2026-03-01": 1, "2026-04-01": 1, "2026-05-01": 1},
                fail_product=cfpb.SERIES[0]["products"][0],
            ),
        )
        n = cfpb.fetch_cfpb_complaints()
        assert n == 3 * (len(cfpb.SERIES) - 1)

    def test_html_shell_response_is_an_error_not_a_crash(
        self, fresh_db, mocked_responses, frozen_now
    ):
        # The retired /trends/ endpoint failure mode: 200 + HTML.
        _seed_all("2026-05-01")
        mocked_responses.get(cfpb.SEARCH_URL, body="<!DOCTYPE html>", status=200)
        assert cfpb.fetch_cfpb_complaints() == 0
