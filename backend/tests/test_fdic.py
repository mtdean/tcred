"""Tests for data/fdic.py — FDIC BankFind Call Report aggregates."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from cache import db
from data import fdic


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(fdic.time, "sleep", sleeps.append)
    return sleeps


def _bucket_of(request) -> str:
    filters = parse_qs(urlparse(request.url).query)["filters"][0]
    return "LARGEBANK" if f"ASSET:[{fdic.LARGE_BANK_ASSETS_K} TO *]" in filters else "SMALLBANK"


def _agg_callback(rows_by_bucket: dict[str, dict[str, dict]], seen: list | None = None):
    """Serve BankFind's aggregation shape: data[].data = {REPDTE, count, sum_*}.

    Honours the REPDTE lower bound in `filters` and the requested sum fields
    (unknown fields sum to 0, like the real API)."""

    def cb(request):
        q = parse_qs(urlparse(request.url).query)
        if seen is not None:
            seen.append(q)
        bucket = _bucket_of(request)
        start = q["filters"][0].split("REPDTE:[")[1].split(" ")[0]
        fields = q["agg_sum_fields"][0].split(",")
        data = []
        for repdte, vals in sorted(rows_by_bucket[bucket].items()):
            if repdte < start:
                continue
            row = {"REPDTE": repdte, "count": 10}
            row.update({f"sum_{f}": vals.get(f, 0) for f in fields})
            data.append({"data": row, "score": 0})
        return (200, {}, json.dumps({"meta": {"total": len(data)}, "data": data}))

    return cb


def _series(series_id: str) -> list[tuple[str, float]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics WHERE series_id = ? ORDER BY date",
            (series_id,),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


class TestHelpers:
    @pytest.mark.parametrize(
        "repdte,expected",
        [("20260630", "2026-04-01"), ("20260331", "2026-01-01"),
         ("20250930", "2025-07-01"), ("20251231", "2025-10-01")],
    )
    def test_repdte_to_quarter_start(self, repdte, expected):
        assert fdic.repdte_to_date(repdte) == expected

    def test_sbl_frequency_rules(self):
        # pre-2010: June only (other quarters are carry-forwards)
        assert fdic.sbl_reported("20080630", "LARGEBANK")
        assert not fdic.sbl_reported("20080930", "LARGEBANK")
        assert not fdic.sbl_reported("20091231", "SMALLBANK")
        # 2010-2016: quarterly for everyone
        assert fdic.sbl_reported("20130331", "SMALLBANK")
        assert fdic.sbl_reported("20160930", "TOTAL")
        # 2017+: small/total semiannual, large quarterly
        assert not fdic.sbl_reported("20250331", "SMALLBANK")
        assert not fdic.sbl_reported("20250930", "TOTAL")
        assert fdic.sbl_reported("20251231", "SMALLBANK")
        assert fdic.sbl_reported("20250930", "LARGEBANK")


SBL_ROWS = {
    "SMALLBANK": {
        "20080630": {"LNCI4": 150_000_000, "LNCI4N": 5_000_000},
        "20080930": {"LNCI4": 149_000_000, "LNCI4N": 4_900_000},  # carry-forward
        "20250331": {"LNCI4": 20_000_000, "LNCI4N": 100_000},     # 041 filers only
        "20250630": {"LNCI4": 120_000_000, "LNCI4N": 3_400_000},
        "20250930": {"LNCI4": 21_000_000, "LNCI4N": 100_000},
        "20251231": {"LNCI4": 118_000_000, "LNCI4N": 3_300_000},
    },
    "LARGEBANK": {
        "20080630": {"LNCI4": 180_000_000, "LNCI4N": 20_000_000},
        "20080930": {"LNCI4": 179_000_000, "LNCI4N": 19_900_000},
        "20250331": {"LNCI4": 300_000_000, "LNCI4N": 22_000_000},
        "20250630": {"LNCI4": 302_000_000, "LNCI4N": 22_500_000},
        "20250930": {"LNCI4": 305_000_000, "LNCI4N": 22_600_000},
        "20251231": {"LNCI4": 307_000_000, "LNCI4N": 22_700_000},
    },
}


class TestSmallBusiness:
    def test_stores_real_observations_only_in_bn(self, fresh_db, mocked_responses):
        seen: list = []
        mocked_responses.add_callback(
            "GET", fdic.API_URL, callback=_agg_callback(SBL_ROWS, seen)
        )
        n = fdic.fetch_small_business()

        assert _series("FDIC_SBL_CI_SMALLBANK") == [
            ("2008-04-01", 150.0), ("2025-04-01", 120.0), ("2025-10-01", 118.0),
        ]
        assert _series("FDIC_SBL_CI_LARGEBANK") == [
            ("2008-04-01", 180.0), ("2025-01-01", 300.0), ("2025-04-01", 302.0),
            ("2025-07-01", 305.0), ("2025-10-01", 307.0),
        ]
        assert _series("FDIC_SBL_CI_TOTAL") == [
            ("2008-04-01", 330.0), ("2025-04-01", 422.0), ("2025-10-01", 425.0),
        ]
        assert _series("FDIC_SBL_CI_NUM_TOTAL")[-1] == ("2025-10-01", 26.0)
        assert n == 2 * (3 + 5 + 3)

        # one aggregated request per size bucket, server-side sums
        assert len(seen) == 2
        q = seen[0]
        assert q["agg_by"] == ["REPDTE"]
        assert set(q["agg_sum_fields"][0].split(",")) == {"LNCI4", "LNCI4N"}
        assert q["filters"][0].startswith(f"REPDTE:[{fdic.START_REPDTE} TO")

    def test_incremental_window_rewinds_revision_quarters(self, fresh_db, mocked_responses):
        db.upsert_metric({
            "series_id": "FDIC_SBL_CI_LARGEBANK", "label": "x", "category": "x",
            "date": "2025-10-01", "value": 1.0, "fetched_at": "2026-01-01",
        })
        seen: list = []
        mocked_responses.add_callback(
            "GET", fdic.API_URL, callback=_agg_callback(SBL_ROWS, seen)
        )
        fdic.fetch_small_business()

        # last stored 2025Q4 → 3 revision quarters + 1 seed quarter back = 2024Q4
        assert seen[0]["filters"][0].startswith("REPDTE:[20241231 TO")
        # the stale seeded point was overwritten with the amended value
        assert _series("FDIC_SBL_CI_LARGEBANK")[-1] == ("2025-10-01", 307.0)
        assert ("2008-04-01", 180.0) not in _series("FDIC_SBL_CI_LARGEBANK")

    def test_ignored_aggregation_raises(self, fresh_db, mocked_responses):
        # If BankFind drops agg support it returns per-bank rows without sum_*.
        body = {"data": [{"data": {"REPDTE": "20250630", "CERT": 1, "LNCI4": 5}}]}
        mocked_responses.get(fdic.API_URL, json=body)
        with pytest.raises(RuntimeError, match="aggregation"):
            fdic.fetch_small_business()

    def test_http_error_raises(self, fresh_db, mocked_responses, no_sleep):
        mocked_responses.get(fdic.API_URL, status=503)
        with pytest.raises(Exception):
            fdic.fetch_small_business()
        assert len(no_sleep) == fdic.MAX_RETRIES


RATE_ROWS = {
    "SMALLBANK": {
        "20250331": {"LNCI": 1_000_000, "NCCI": 10_000, "NTCIQ": 1_000,
                     "LNAUTO": 100_000, "NCAUTO": 500, "NTAUTOQ": 250},
        "20250630": {"LNCI": 1_100_000, "NCCI": 13_200, "NTCIQ": 2_100,
                     "LNAUTO": 100_000, "NCAUTO": 600, "NTAUTOQ": 300},
    },
    "LARGEBANK": {
        "20250331": {"LNCI": 4_000_000, "NCCI": 20_000, "NTCIQ": 4_000,
                     "LNAUTO": 400_000, "NCAUTO": 1_500, "NTAUTOQ": 750},
        "20250630": {"LNCI": 4_000_000, "NCCI": 40_000, "NTCIQ": 6_000,
                     "LNAUTO": 400_000, "NCAUTO": 2_400, "NTAUTOQ": 700},
    },
}


class TestRates:
    def test_rates_are_ratios_of_summed_dollars(self, fresh_db, mocked_responses):
        mocked_responses.add_callback(
            "GET", fdic.API_URL, callback=_agg_callback(RATE_ROWS)
        )
        fdic.fetch_rates()

        assert _series("FDIC_CI_NCR_SMALLBANK") == [
            ("2025-01-01", 1.0), ("2025-04-01", 1.2),
        ]
        assert _series("FDIC_CI_NCR_LARGEBANK") == [
            ("2025-01-01", 0.5), ("2025-04-01", 1.0),
        ]
        # NCO: quarterly x4 over average loans (first quarter: period-end)
        assert _series("FDIC_CI_NCO_SMALLBANK") == [
            ("2025-01-01", 0.4), ("2025-04-01", 0.8),  # 2100*4/1.05mm
        ]
        # Auto is all-bank: (600+2400)/(100k+400k)
        assert _series("FDIC_AUTO_NCR")[-1] == ("2025-04-01", 0.6)
        # (300+700)*4 / 500k
        assert _series("FDIC_AUTO_NCO")[-1] == ("2025-04-01", 0.8)

    def test_zero_denominator_quarters_skipped(self, fresh_db, mocked_responses):
        rows = {b: {"20100630": {"LNCI": 1_000, "NCCI": 10}} for b in ("SMALLBANK", "LARGEBANK")}
        mocked_responses.add_callback("GET", fdic.API_URL, callback=_agg_callback(rows))
        fdic.fetch_rates()
        assert _series("FDIC_AUTO_NCR") == []  # auto split didn't exist yet
        assert _series("FDIC_CI_NCR_SMALLBANK") == [("2010-04-01", 1.0)]

    def test_incremental_does_not_overwrite_seed_quarter_nco(self, fresh_db, mocked_responses):
        db.upsert_metric({
            "series_id": "FDIC_CI_NCR_LARGEBANK", "label": "x", "category": "x",
            "date": "2026-01-01", "value": 1.0, "fetched_at": "2026-01-01",
        })
        db.upsert_metric({
            "series_id": "FDIC_CI_NCO_LARGEBANK", "label": "x", "category": "x",
            "date": "2025-01-01", "value": 9.99, "fetched_at": "2026-01-01",
        })
        mocked_responses.add_callback(
            "GET", fdic.API_URL, callback=_agg_callback(RATE_ROWS)
        )
        fdic.fetch_rates()
        # window starts at 2025Q1 (seed) — its NCO lacks a prior quarter, keep stored
        assert ("2025-01-01", 9.99) in _series("FDIC_CI_NCO_LARGEBANK")
        assert ("2025-04-01", 0.6) in _series("FDIC_CI_NCO_LARGEBANK")


def test_sources_declared():
    assert fdic.SOURCE.id == "fdic"
    assert fdic.SOURCE.every_hours == 24 * 7
    assert fdic.SOURCE.panel is None
    assert [s.id for s in fdic.SOURCES] == ["fdic_rates"]
    assert fdic.SOURCES[0].panel is None
