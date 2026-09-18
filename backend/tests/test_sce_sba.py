"""
Cover data/sce.py and data/sba.py — NY Fed SCE + SBA activity ingests.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from cache import db
from data import sba, sce


def _xlsx(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, header=False, index=False)
    return buf.getvalue()


def _series(series_id):
    with db.get_conn() as conn:
        return [
            (r["date"], r["value"]) for r in conn.execute(
                "SELECT date, value FROM metrics WHERE series_id = ? ORDER BY date",
                (series_id,),
            )
        ]


class TestSce:
    def _core_xlsx(self):
        pad = [["h"] + [None], ["h"] + [None], [None, None], [None, "label"]]
        delinq = pd.DataFrame(pad + [[201306, 13.4], [201307, 14.7]])
        jobsep = pd.DataFrame(pad + [[201306, 15.0], [201307, 15.5]])
        wide_pad = [[None] * 11] * 4
        credit = pd.DataFrame(wide_pad + [
            [201306, 1, 2, 3, 4, 5, 10.0, 20.0, 40, 25, 5],
        ])
        finsit = pd.DataFrame(wide_pad + [
            [201306, 1, 2, 3, 4, 5, 4.0, 16.0, 50, 28, 2],
        ])
        return _xlsx({
            "Delinquency expectations": delinq,
            "Job separation expectation": jobsep,
            "Credit availability": credit,
            "Household financial situation": finsit,
        })

    def _credit_xlsx(self):
        rows = [
            [None] * 8,
            ["date", "group", "category", "Observations", "x", "y",
             "Applied_Accepted", "Applied_Rejected"],
            [201310, "all", "Overall", 100, 0, 0, 40.0, 11.0],
            [201402, "all", "Overall", 100, 0, 0, 40.0, 10.0],
        ]
        df = pd.DataFrame(rows)
        return _xlsx({"overall": df})

    def test_core_series_stored(self, fresh_db, mocked_responses):
        mocked_responses.get(sce.CORE_URL, body=self._core_xlsx(), status=200)
        mocked_responses.get(sce.CREDIT_URL, body=b"not an xlsx", status=200)
        n = sce.fetch_sce()
        assert n >= 6
        assert _series("SCE_MISS_PAYMENT_PROB") == [
            ("2013-06-01", 13.4), ("2013-07-01", 14.7),
        ]
        # year-ahead "harder" = cols 6+7 summed
        assert _series("SCE_CREDIT_HARDER_YA") == [("2013-06-01", 30.0)]

    def test_credit_access_rejection_rate(self, fresh_db, mocked_responses):
        mocked_responses.get(sce.CORE_URL, body=b"broken", status=200)
        mocked_responses.get(sce.CREDIT_URL, body=self._credit_xlsx(), status=200)
        sce.fetch_sce()
        assert _series("SCE_REJECTION_RATE") == [
            ("2013-10-01", 11.0), ("2014-02-01", 10.0),
        ]

    def test_network_failure_is_zero_not_crash(self, fresh_db, mocked_responses):
        mocked_responses.get(sce.CORE_URL, status=500)
        mocked_responses.get(sce.CREDIT_URL, status=500)
        assert sce.fetch_sce() == 0


class TestSba:
    def _sba_xlsx(self):
        pad = [[None] * 12] * 4
        rows = pad + [
            [1991, 199010, "7(a)", 1467, 318850281, 260042953, None,
             1991, 199010, "504", 109, 29882000],
            [1991, 199011, "7(a)", 1328, 307635836, 248158054, None,
             1991, 199011, "504", 101, 29383000],
        ]
        return _xlsx({"Monthly": pd.DataFrame(rows)})

    def test_monthly_series_stored(self, fresh_db, mocked_responses):
        mocked_responses.get(sba.XLSX_URL, body=self._sba_xlsx(), status=200)
        n = sba.fetch_sba_activity()
        assert n == 8  # 4 series × 2 months
        assert _series("SBA_7A_LOANS") == [
            ("1990-10-01", 1467.0), ("1990-11-01", 1328.0),
        ]
        dollars = _series("SBA_7A_DOLLARS")
        assert dollars[0] == ("1990-10-01", pytest.approx(318.850281))
        assert _series("SBA_504_LOANS")[0] == ("1990-10-01", 109.0)

    def test_network_failure_is_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(sba.XLSX_URL, status=500)
        assert sba.fetch_sba_activity() == 0
