"""
Cover data/philly_cards.py — Philly Fed large-bank (Y-14M) credit card CSVs.
"""

from __future__ import annotations

import pytest

from cache import db
from data import philly_cards as pc

BASE = "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/Y14/2026/Q2"
BAL_URL = f"{BASE}/26Q2-CreditCardBalances.csv"
ORIG_URL = f"{BASE}/26Q2-CreditCardOriginations.csv"

LANDING = """
<a href="/-/media/FRBP/Assets/Surveys-And-Data/Y14/2026/Q2/26Q2-CreditCardBalances.csv">Card balances</a>
<a href="/-/media/FRBP/Assets/Surveys-And-Data/Y14/2026/Q2/26Q2-CreditCardOriginations.csv">Card originations</a>
<a href="/-/media/FRBP/Assets/Surveys-And-Data/Y14/2026/Q2/26Q2-First-LienBalances.csv">Mortgage</a>
"""

# Mirrors the real layout: BOM, quoted cells, '$'/'%' formatting, 'null'
# cells, extra columns we ignore, and trailing source/footer rows.
BAL_CSV = (
    '﻿"YRQTR","Total Balances ($Billions)","Number of Accounts (Millions)",'
    '"Utilization (Active Accounts Only) (50th percentile)",'
    '"Percentage of Accounts with Credit Line Decrease",'
    '"30+ Days Past Due Rates: Accounts Based","30+ Days Past Due Rates: Balances Based",'
    '"90+ Days Past Due Rates: Accounts Based",'
    '"Share of Accounts Making the Minimum Payment",'
    '"Share of Accounts Making the Full Balance Payment",'
    '"Net Charge-Off Rate","Average Purchase APR: General Purpose"\n'
    '"2012Q3","$579.13","517.47","9.43 %","null","1.44 %","3.18 %","0.66 %",'
    '"8.76 %","26.25 %","4.51 %","16.87 %"\n'
    '"2026Q2","$1,123.45","600.00","13.10 %","0.80 %","2.10 %","4.05 %","1.02 %",'
    '"11.20 %","34.50 %","4.10 %","22.30 %"\n'
    '"Source: Philadelphia Fed FR Y-14M Data","","","","","","","","","","",""\n'
    '"Email: Phil.LargeBankData@phil.frb.org","","","","","","","","","","",""\n'
).encode("utf-8")

ORIG_CSV = (
    '﻿"YRQTR","New Originations ($Billions)",'
    '"Average Original Purchase APR: General Purpose",'
    '"Percentage of New Accounts with < 660 Credit Score"\n'
    '"2012Q3","$57.78","19.03 %","20.49 %"\n'
    '"2026Q2","$80.00","24.10 %","18.00 %"\n'
).encode("utf-8")


def _series(series_id):
    with db.get_conn() as conn:
        return [
            (r["date"], r["value"]) for r in conn.execute(
                "SELECT date, value FROM metrics WHERE series_id = ? ORDER BY date",
                (series_id,),
            )
        ]


class TestHelpers:
    @pytest.mark.parametrize("label,expected", [
        ("2012Q3", "2012-07-01"), ("2026Q2", "2026-04-01"),
        ("2020Q1", "2020-01-01"), ("2020Q4", "2020-10-01"),
        ("Source: Philadelphia Fed", None), ("", None),
    ])
    def test_quarter_date(self, label, expected):
        assert pc.quarter_date(label) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("$1,123.45", 1123.45), ("12.30 %", 12.3), ("517.47", 517.47),
        ("null", None), ("", None), (None, None),
    ])
    def test_parse_value(self, raw, expected):
        assert pc.parse_value(raw) == expected

    def test_find_csv_urls_absolute(self):
        assert pc.find_csv_urls(LANDING) == (BAL_URL, ORIG_URL)
        assert pc.find_csv_urls("<html>nothing</html>") == (None, None)

    def test_missing_column_is_skipped_not_fatal(self):
        out = pc.parse_csv(ORIG_CSV, pc.BALANCE_SERIES)
        assert out == {}


class TestFetch:
    def test_full_ingest(self, fresh_db, mocked_responses):
        mocked_responses.get(pc.PAGE_URL, body=LANDING, status=200)
        mocked_responses.get(BAL_URL, body=BAL_CSV, status=200)
        mocked_responses.get(ORIG_URL, body=ORIG_CSV, status=200)

        n = pc.fetch_philly_cards()
        # 10 balance series × 2 quarters, minus the one 'null' line-decrease cell,
        # plus 2 origination series × 2 quarters.
        assert n == 19 + 4

        assert _series("PHIL_CC_MIN_PAY") == [("2012-07-01", 8.76), ("2026-04-01", 11.2)]
        assert _series("PHIL_CC_FULL_PAY") == [("2012-07-01", 26.25), ("2026-04-01", 34.5)]
        assert _series("PHIL_CC_DPD30") == [("2012-07-01", 1.44), ("2026-04-01", 2.1)]
        assert _series("PHIL_CC_DPD90")[-1] == ("2026-04-01", 1.02)
        assert _series("PHIL_CC_LINE_DECR") == [("2026-04-01", 0.8)]
        assert _series("PHIL_CC_BALANCES")[-1] == ("2026-04-01", 1123.45)
        assert _series("PHIL_CC_ORIG_SUBPRIME") == [("2012-07-01", 20.49), ("2026-04-01", 18.0)]

    def test_refetch_is_idempotent(self, fresh_db, mocked_responses):
        for _ in range(2):
            mocked_responses.get(pc.PAGE_URL, body=LANDING, status=200)
            mocked_responses.get(BAL_URL, body=BAL_CSV, status=200)
            mocked_responses.get(ORIG_URL, body=ORIG_CSV, status=200)
            pc.fetch_philly_cards()
        assert len(_series("PHIL_CC_MIN_PAY")) == 2

    def test_originations_failure_keeps_balances(self, fresh_db, mocked_responses):
        mocked_responses.get(pc.PAGE_URL, body=LANDING, status=200)
        mocked_responses.get(BAL_URL, body=BAL_CSV, status=200)
        mocked_responses.get(ORIG_URL, status=500)
        assert pc.fetch_philly_cards() == 19
        assert _series("PHIL_CC_ORIG_SUBPRIME") == []

    def test_no_link_raises(self, fresh_db, mocked_responses):
        mocked_responses.get(pc.PAGE_URL, body="<html></html>", status=200)
        with pytest.raises(RuntimeError):
            pc.fetch_philly_cards()

    def test_balances_http_error_raises(self, fresh_db, mocked_responses):
        mocked_responses.get(pc.PAGE_URL, body=LANDING, status=200)
        mocked_responses.get(BAL_URL, status=403)
        with pytest.raises(Exception):
            pc.fetch_philly_cards()


def test_source_declaration():
    s = pc.SOURCE
    assert s.id == "philly_cards"
    # Charted by a hand-built Macro panel, not the generic Custom Sources one.
    assert s.panel is None
    assert s.every_hours == 24 * 7
