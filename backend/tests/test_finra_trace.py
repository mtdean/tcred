"""Tests for data/finra_trace.py — STAR daily volume parse + ingest."""

from __future__ import annotations

import io

import pandas as pd
import pytest

from cache import db
from data import finra_trace as ft


def _star_xlsx(rows: list[list]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        pd.DataFrame(rows).to_excel(
            w, sheet_name="TradingActivity", header=False, index=False
        )
    return buf.getvalue()


def _standard_rows() -> list[list]:
    pad = [None] * 8  # col0..col7 then padding is unnecessary; keep 8 cols
    return [
        [None] * 8,
        [None, "DATA AS OF: ", pd.Timestamp("2026-06-09"), None, None, None, None, None],
        [None, "ASSET CLASS", "INVESTMENT GRADE", None, None, "NON-INVESTMENT GRADE", None, None],
        [None, None, "TRADE", "UNIQUE", "$ TRADES", "TRADE", "UNIQUE", "$ TRADES"],
        # CMBS: section header row, values on the P&I row below
        [None, "NON-AGENCY CMBS", None, None, None, None, None, None],
        [None, "P&I", 293, 159, 680847, 37, 20, 50000],
        [None, "ABS", 529, 298, 1145136.9, 112, 61, 127689.2],
        [None, "CBO/CDO/CLO", 225, 154, 1453007, 54, 35, 187428.2],
        # Suppressed cells: '*' counts as zero
        [None, "NON-AGENCY CMO", None, None, None, None, None, None],
        [None, "P&I", 183, 103, 507690.6, "*", "*", "*"],
    ]


class TestParse:
    def test_parses_date_and_classes(self):
        as_of, classes = ft.parse_star_xlsx(_star_xlsx(_standard_rows()))
        assert as_of == "2026-06-09"
        assert set(classes) == {"TRACE_ABS", "TRACE_CLO", "TRACE_CMBS", "TRACE_NA_CMO"}
        # IG + non-IG summed; volume converted $000 -> $mm
        assert classes["TRACE_ABS"]["trades"] == 641
        assert classes["TRACE_ABS"]["volume_mm"] == pytest.approx(1272.8261)

    def test_section_header_reads_following_pi_row(self):
        _, classes = ft.parse_star_xlsx(_star_xlsx(_standard_rows()))
        assert classes["TRACE_CMBS"]["trades"] == 330
        assert classes["TRACE_CMBS"]["volume_mm"] == pytest.approx(730.847)

    def test_suppressed_cells_count_as_zero(self):
        _, classes = ft.parse_star_xlsx(_star_xlsx(_standard_rows()))
        assert classes["TRACE_NA_CMO"]["trades"] == 183
        assert classes["TRACE_NA_CMO"]["volume_mm"] == pytest.approx(507.6906)

    def test_missing_date_raises(self):
        rows = _standard_rows()[2:]  # drop the DATA AS OF row
        with pytest.raises(ValueError):
            ft.parse_star_xlsx(_star_xlsx(rows))


class TestFetch:
    def test_fetch_stores_eight_series(self, fresh_db, mocked_responses):
        mocked_responses.get(ft.STAR_URL, body=_star_xlsx(_standard_rows()))
        assert ft.fetch_trace_volumes() == 8

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT series_id, date, value FROM metrics "
                "WHERE series_id LIKE 'TRACE%' ORDER BY series_id"
            ).fetchall()
        assert len(rows) == 8
        assert all(r[1] == "2026-06-09" for r in rows)
        by_id = {r[0]: r[2] for r in rows}
        assert by_id["TRACE_CLO_VOLUME"] == pytest.approx(1640.4352)
        assert by_id["TRACE_CLO_TRADES"] == 279

    def test_same_day_refetch_upserts_in_place(self, fresh_db, mocked_responses):
        mocked_responses.get(ft.STAR_URL, body=_star_xlsx(_standard_rows()))
        mocked_responses.get(ft.STAR_URL, body=_star_xlsx(_standard_rows()))
        ft.fetch_trace_volumes()
        ft.fetch_trace_volumes()
        with db.get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM metrics WHERE series_id LIKE 'TRACE%'"
            ).fetchone()[0]
        assert n == 8
