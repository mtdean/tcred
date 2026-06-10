"""Tests for data/manheim.py — Manheim UVVI scrape + XLSX parse."""

from __future__ import annotations

import io

import pandas as pd
import pytest

from cache import db
from data import manheim


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        df.to_excel(w, sheet_name="DATA", index=False)
    return buf.getvalue()


def _frame(**overrides) -> pd.DataFrame:
    base = {
        "Month": pd.to_datetime(["2025-10-01", "2025-11-01"]),
        "Index (1/97 = 100)": [202.875, 205.433],
        "Index % YoY": [0.00027, 0.000213],
    }
    base.update(overrides)
    return pd.DataFrame(base)


class TestParse:
    def test_parses_dates_index_and_yoy_pct(self):
        rows = manheim.parse_manheim_xlsx(_xlsx(_frame()))
        assert rows[0][0] == "2025-10-01"
        assert rows[0][1] == pytest.approx(202.875)
        # YoY stored as a fraction in the file; parsed to percent.
        assert rows[0][2] == pytest.approx(0.027)

    def test_missing_yoy_column_tolerated(self):
        df = _frame()
        df = df.drop(columns=["Index % YoY"])
        rows = manheim.parse_manheim_xlsx(_xlsx(df))
        assert rows[1] == ("2025-11-01", pytest.approx(205.433), None)

    def test_missing_index_column_raises(self):
        df = pd.DataFrame({"Month": pd.to_datetime(["2025-10-01"]), "Other": [1.0]})
        with pytest.raises(ValueError):
            manheim.parse_manheim_xlsx(_xlsx(df))

    def test_nan_index_rows_skipped(self):
        df = _frame()
        df.loc[0, "Index (1/97 = 100)"] = float("nan")
        rows = manheim.parse_manheim_xlsx(_xlsx(df))
        assert len(rows) == 1
        assert rows[0][0] == "2025-11-01"


class TestFetch:
    def test_fetch_scrapes_link_and_stores_metrics(self, fresh_db, mocked_responses):
        url = "https://site.manheim.com/wp-content/uploads/sites/2/2025/12/Nov-2025-Manheim-Used-Vehicle-Value-Index.xlsx"
        mocked_responses.get(
            manheim.PAGE_URL, body=f'<a href="{url}">Download</a>'
        )
        mocked_responses.get(url, body=_xlsx(_frame()))

        n = manheim.fetch_manheim_index()
        assert n == 4  # 2 dates × (index + yoy)

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT series_id, date, value FROM metrics "
                "WHERE series_id LIKE 'MANHEIM%' ORDER BY series_id, date"
            ).fetchall()
        assert [(r[0], r[1]) for r in rows] == [
            ("MANHEIM_UVVI", "2025-10-01"),
            ("MANHEIM_UVVI", "2025-11-01"),
            ("MANHEIM_UVVI_YOY", "2025-10-01"),
            ("MANHEIM_UVVI_YOY", "2025-11-01"),
        ]

    def test_no_link_on_page_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(manheim.PAGE_URL, body="<html>nothing here</html>")
        assert manheim.fetch_manheim_index() == 0
