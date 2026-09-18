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


# ─── NFIB SBET PDF-text parsing ──────────────────────────────────────────────
class TestNfibParse:
    SAMPLE = """OPTIMISM INDEX
Based on Ten Survey Indicators
(Seasonally Adjusted 1986=100)
Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec
2025 95.0 95.8 98.2 99.8 99.6 102.5 99.7 100.1 99.1 98.2 98.4 98.9
2026 97.1 95.7 93.2 93.2 93.1 89.5 89.9 98.7
other text
AVAILABILITY OF LOANS
Percent Borrowing at Least Once Every Three Months
Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec
2025 23 26 23 24 23 21 21 20 20 23 21 23
2026 25 25 24 22 27 22 27 25
Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec
2025 -1 -2 -1 -3 -2 -2 -2 -3 -4 -2 -1 -4
2026 -3 -5 -5 -3 -4 -3 -5 -3
"""

    def test_optimism_parsed(self):
        from data.nfib import parse_sbet_text
        s = parse_sbet_text(self.SAMPLE)
        pts = s["NFIB_OPTIMISM"]
        assert pts[0] == ("2025-01-01", 95.0)
        assert pts[-1] == ("2026-08-01", 98.7)

    def test_availability_takes_second_table(self):
        from data.nfib import parse_sbet_text
        s = parse_sbet_text(self.SAMPLE)
        pts = s["NFIB_LOAN_AVAILABILITY"]
        # Must be the NET table (negatives), not the borrower-share table (20s).
        assert pts[0] == ("2025-01-01", -1.0)
        assert pts[-1] == ("2026-08-01", -3.0)

    def test_missing_anchors_empty(self):
        from data.nfib import parse_sbet_text
        assert parse_sbet_text("nothing here") == {}


# ─── Scorecard derived series (spread / ratio) ───────────────────────────────
class TestScorecardDerived:
    def _seed(self, conn, sid, pairs):
        for d, v in pairs:
            conn.execute(
                "INSERT OR REPLACE INTO metrics (series_id, date, value, label, category, fetched_at) "
                "VALUES (?, ?, ?, ?, 'x', '2026-09-18')", (sid, d, v, sid),
            )
        conn.commit()

    def test_spread_of(self, db_conn):
        from data import scorecard
        self._seed(db_conn, "CCC", [("2026-09-01", 10.0), ("2026-09-02", 11.0)])
        self._seed(db_conn, "B", [("2026-09-01", 4.0), ("2026-09-02", 4.5)])
        spec = {"id": "X", "label": "x", "unit": "bp", "orient": +1,
                "trend_obs": 1, "scale": 100, "spread_of": ["CCC", "B"]}
        with db.get_conn() as conn:
            vals = scorecard._spec_values(conn, spec)
        assert vals == [("2026-09-01", 600.0), ("2026-09-02", 650.0)]

    def test_ratio_of(self, db_conn):
        from data import scorecard
        self._seed(db_conn, "JBBB", [("2026-09-01", 47.0)])
        self._seed(db_conn, "JAAA", [("2026-09-01", 50.0)])
        spec = {"id": "R", "label": "r", "unit": "", "orient": -1,
                "trend_obs": 1, "ratio_of": ["JBBB", "JAAA"]}
        with db.get_conn() as conn:
            vals = scorecard._spec_values(conn, spec)
        assert vals == [("2026-09-01", 0.94)]

    def test_smb_and_leveraged_compute(self, fresh_db):
        from data import scorecard
        # empty DB → empty scorecards, no crash
        assert scorecard.compute_smb_scorecard()["indicators"] == []
        assert scorecard.compute_leveraged_scorecard()["indicators"] == []
