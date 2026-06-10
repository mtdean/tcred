"""Tests for data/cfpb.py — CFPB complaint-volume ingest."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from cache import db
from data import cfpb


def _payload(buckets):
    return {
        "aggregations": {
            "dateRangeArea": {"dateRangeArea": {"buckets": buckets}}
        }
    }


def _bucket(date: str, n: int):
    return {"key_as_string": f"{date}T00:00:00.000Z", "doc_count": n}


class TestFetch:
    def test_stores_monthly_counts_and_drops_partial_month(
        self, fresh_db, mocked_responses, monkeypatch
    ):
        import datetime as dt

        class FakeDatetime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 6, 10, tzinfo=tz)

        monkeypatch.setattr(cfpb, "datetime", FakeDatetime)
        # one response per SERIES entry
        for _ in cfpb.SERIES:
            mocked_responses.get(
                cfpb.TRENDS_URL,
                json=_payload(
                    [
                        _bucket("2026-04-01", 8145),
                        _bucket("2026-05-01", 6886),
                        _bucket("2026-06-01", 275),  # partial month
                    ]
                ),
            )

        n = cfpb.fetch_cfpb_complaints()
        assert n == 2 * len(cfpb.SERIES)

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT series_id, date, value FROM metrics "
                "WHERE series_id = 'CFPB_COMPLAINTS_CARD' ORDER BY date"
            ).fetchall()
        assert [(r[1], r[2]) for r in rows] == [
            ("2026-04-01", 8145.0),
            ("2026-05-01", 6886.0),
        ]

    def test_product_names_sent_as_repeated_params(self, fresh_db, mocked_responses):
        for _ in cfpb.SERIES:
            mocked_responses.get(cfpb.TRENDS_URL, json=_payload([]))
        cfpb.fetch_cfpb_complaints()

        card_call = mocked_responses.calls[0]
        q = parse_qs(urlparse(card_call.request.url).query)
        assert q["lens"] == ["overview"]
        assert q["trend_interval"] == ["month"]
        assert set(q["product"]) == set(cfpb.SERIES[0]["products"])

    def test_one_series_failure_does_not_block_others(
        self, fresh_db, mocked_responses
    ):
        mocked_responses.get(cfpb.TRENDS_URL, status=500)  # CARD fails
        for _ in cfpb.SERIES[1:]:
            mocked_responses.get(
                cfpb.TRENDS_URL, json=_payload([_bucket("2026-04-01", 10)])
            )
        n = cfpb.fetch_cfpb_complaints()
        assert n == len(cfpb.SERIES) - 1

    def test_unnested_histogram_shape_tolerated(self, fresh_db, mocked_responses):
        # Without filters the histogram is not double-nested.
        flat = {
            "aggregations": {
                "dateRangeArea": {"buckets": [_bucket("2026-04-01", 5)]}
            }
        }
        for _ in cfpb.SERIES:
            mocked_responses.get(cfpb.TRENDS_URL, json=flat)
        assert cfpb.fetch_cfpb_complaints() == len(cfpb.SERIES)
