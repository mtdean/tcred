"""
Cover data/bdc.py — SEC BDC bulk-dataset ingest.

Strategy:
  * Pure helpers (`_sort_key`, `_safe_float`, `_norm`, `_edgar_filing_url`,
    `_compute_bdc_summary`) get direct-value assertions.
  * `_pick_primary_breakdown` exercised via a small DataFrame.
  * `_get_latest_bdc_zip_url` and `_list_all_bdc_zip_urls` with stubbed HTTP.
  * `_ingest_dataframe` (and the higher-level `fetch_bdc_data`) end-to-end:
    build a small in-memory SOI-shaped TSV ZIP, feed it through, assert rows
    landed in bdc_holdings + bdc_summary.
  * Query helpers (`get_bdc_summary`, `get_bdc_summary_latest_per_bdc`,
    `get_bdc_nonaccrual_trend`, `get_bdc_aggregate_trend`, `get_bdc_nonaccruals`)
    seeded directly via SQL.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from cache import db
from data import bdc


NOW = "2026-05-29T12:00:00+00:00"


# ─── _sort_key ────────────────────────────────────────────────────────────────
class TestSortKey:
    @pytest.mark.parametrize("path, expected", [
        ("/x/2026_04_bdc.zip", (2026, 4, 1)),
        ("/x/2024q3_bdc.zip", (2024, 9, 0)),
        ("/x/2024q1_bdc.zip", (2024, 3, 0)),
        ("/x/random.zip",     (0, 0, 0)),
    ])
    def test_value_table(self, path, expected):
        assert bdc._sort_key(path) == expected

    def test_monthly_outranks_quarterly_same_period(self):
        # Both released for "March 2026". Monthly (2026_03) gets month=3 and
        # variant flag 1; quarterly (2026q1) maps to month=3 variant flag 0.
        monthly = bdc._sort_key("/x/2026_03_bdc.zip")
        quarterly = bdc._sort_key("/x/2026q1_bdc.zip")
        assert monthly > quarterly

    def test_newer_year_wins(self):
        assert bdc._sort_key("/x/2026_01_bdc.zip") > bdc._sort_key("/x/2025_12_bdc.zip")


# ─── _safe_float ──────────────────────────────────────────────────────────────
class TestSafeFloat:
    @pytest.mark.parametrize("val, expected", [
        (None, None),
        ("", None),
        ("nan", None),
        ("None", None),
        ("NaN", None),
        ("  3.5 ", 3.5),
        ("0", 0.0),
        (3, 3.0),
        (3.14, 3.14),
        ("not a number", None),
    ])
    def test_value_table(self, val, expected):
        assert bdc._safe_float(val) == expected


# ─── _norm ────────────────────────────────────────────────────────────────────
class TestNorm:
    @pytest.mark.parametrize("val, expected", [
        (None, ""),
        ("  hello  ", "hello"),
        ("First Lien[Member]", "First Lien"),
        ("[Member]", ""),
        (float("nan"), ""),
    ])
    def test_value_table(self, val, expected):
        assert bdc._norm(val) == expected


# ─── _edgar_filing_url ────────────────────────────────────────────────────────
class TestEdgarFilingUrl:
    def test_canonical_url(self):
        url = bdc._edgar_filing_url("1287750", "0001287750-25-000026")
        assert url == (
            "https://www.sec.gov/Archives/edgar/data/1287750/"
            "000128775025000026/0001287750-25-000026-index.htm"
        )

    def test_strips_leading_zeros_from_cik(self):
        url = bdc._edgar_filing_url("0001287750", "0001287750-25-000026")
        assert "/data/1287750/" in url

    @pytest.mark.parametrize("cik, adsh", [
        (None, "0001287750-25-000026"),
        ("1287750", None),
        ("", "0001287750-25-000026"),
        ("not-an-int", "0001287750-25-000026"),
        ("1287750", "too-short"),
        ("0", "0001287750-25-000026"),
    ])
    def test_invalid_inputs_return_none(self, cik, adsh):
        assert bdc._edgar_filing_url(cik, adsh) is None


# ─── _compute_bdc_summary ─────────────────────────────────────────────────────
def _holding(**overrides):
    base = {
        "fair_value": 1000.0,
        "cost_basis": 1000.0,
        "is_nonaccrual": 0,
        "investment_type": "",
        "company_name": "",
        "interest_rate": None,
    }
    base.update(overrides)
    return base


class TestComputeBdcSummary:
    def test_empty_holdings_returns_empty_dict(self):
        assert bdc._compute_bdc_summary([], "ARCC", "1", "2026-03-31") == {}

    def test_aggregates_fair_value_and_cost(self):
        out = bdc._compute_bdc_summary(
            [_holding(fair_value=200, cost_basis=210),
             _holding(fair_value=300, cost_basis=290)],
            "ARCC", "1", "2026-03-31",
        )
        assert out["total_fair_value"] == 500
        assert out["total_cost_basis"] == 500
        assert out["mark_to_cost"] == 1.0
        assert out["n_holdings"] == 2

    def test_nonaccrual_rate_split(self):
        out = bdc._compute_bdc_summary(
            [_holding(fair_value=100, cost_basis=100, is_nonaccrual=1),
             _holding(fair_value=900, cost_basis=900, is_nonaccrual=0)],
            "ARCC", "1", "2026-03-31",
        )
        assert out["nonaccrual_fv"] == 100
        assert out["nonaccrual_rate_fv"] == pytest.approx(0.10)
        assert out["nonaccrual_rate_cost"] == pytest.approx(0.10)

    def test_lien_classifier_first_second_equity(self):
        out = bdc._compute_bdc_summary(
            [_holding(fair_value=500, investment_type="First Lien Term Loan"),
             _holding(fair_value=300, investment_type="Second Lien Notes"),
             _holding(fair_value=200, company_name="ACME Common Stock")],
            "ARCC", "1", "2026-03-31",
        )
        assert out["pct_first_lien"] == pytest.approx(0.5)
        assert out["pct_second_lien"] == pytest.approx(0.3)
        assert out["pct_equity"] == pytest.approx(0.2)

    def test_lien_returns_null_when_nothing_classifiable(self):
        # No keyword in either column → tagged_fv stays 0 → lien % is NULL.
        out = bdc._compute_bdc_summary(
            [_holding(fair_value=500, investment_type="Loan",
                      company_name="Acme Corp")],
            "ARCC", "1", "2026-03-31",
        )
        assert out["pct_first_lien"] is None
        assert out["pct_second_lien"] is None
        assert out["pct_equity"] is None

    def test_weighted_average_yield_excludes_rate_null_holdings(self):
        # Only the rate-bearing holding (worth 100, rate 8.0) contributes.
        out = bdc._compute_bdc_summary(
            [_holding(fair_value=100, interest_rate=8.0),
             _holding(fair_value=900, interest_rate=None)],
            "ARCC", "1", "2026-03-31",
        )
        assert out["wa_interest_rate"] == pytest.approx(8.0)

    def test_weighted_average_yield_blends_multiple_rates(self):
        # 100 @ 4% + 100 @ 8% → 6%.
        out = bdc._compute_bdc_summary(
            [_holding(fair_value=100, interest_rate=4.0),
             _holding(fair_value=100, interest_rate=8.0)],
            "ARCC", "1", "2026-03-31",
        )
        assert out["wa_interest_rate"] == pytest.approx(6.0)

    def test_id_is_stable_hash_of_cik_period(self):
        out1 = bdc._compute_bdc_summary([_holding()], "X", "42", "2026-03-31")
        out2 = bdc._compute_bdc_summary([_holding()], "Y", "42", "2026-03-31")
        assert out1["id"] == out2["id"]  # same cik+period → same id
        out3 = bdc._compute_bdc_summary([_holding()], "X", "42", "2026-06-30")
        assert out1["id"] != out3["id"]


# ─── _pick_primary_breakdown ──────────────────────────────────────────────────
class TestPickPrimaryBreakdown:
    def test_returns_axis_with_smallest_positive_total_cost(self):
        # Identifier axis: 2 rows summing to 1000.
        # Issuer axis:     2 rows summing to 800 (smaller → win).
        df = pd.DataFrame([
            {"Investment, Identifier Axis": "A",
             "Investment, Issuer Name Axis": "",
             "fv": "100", "cost": "500"},
            {"Investment, Identifier Axis": "B",
             "Investment, Issuer Name Axis": "",
             "fv": "200", "cost": "500"},
            {"Investment, Identifier Axis": "",
             "Investment, Issuer Name Axis": "X",
             "fv": "100", "cost": "400"},
            {"Investment, Identifier Axis": "",
             "Investment, Issuer Name Axis": "Y",
             "fv": "200", "cost": "400"},
        ])
        axis = bdc._pick_primary_breakdown(df, "fv", "cost")
        assert axis == "Investment, Issuer Name Axis"

    def test_returns_none_when_no_axis_has_data(self):
        df = pd.DataFrame([
            {"Investment, Identifier Axis": "", "fv": "0", "cost": "0"},
        ])
        assert bdc._pick_primary_breakdown(df, "fv", "cost") is None

    def test_skips_zero_total_cost_axes(self):
        df = pd.DataFrame([
            {"Investment, Identifier Axis": "X",
             "Investment, Issuer Name Axis": "",
             "fv": "10", "cost": "0"},
            {"Investment, Identifier Axis": "",
             "Investment, Issuer Name Axis": "A",
             "fv": "10", "cost": "5"},
        ])
        # Identifier axis sums to 0 cost → skipped; Issuer Name wins.
        axis = bdc._pick_primary_breakdown(df, "fv", "cost")
        assert axis == "Investment, Issuer Name Axis"


# ─── _get_latest_bdc_zip_url / _list_all_bdc_zip_urls ────────────────────────
def _index_html(filenames: list[str]) -> str:
    links = "".join(
        f'<a href="{bdc.BDC_BASE_PATH}/{f}">{f}</a>' for f in filenames
    )
    return f"<html><body>{links}</body></html>"


class TestZipUrlDiscovery:
    def test_returns_most_recent_zip(self, mocked_responses):
        body = _index_html([
            "2024q3_bdc.zip", "2024q4_bdc.zip", "2025_04_bdc.zip",
            "2026_04_bdc.zip", "2026_02_bdc.zip",
        ])
        mocked_responses.get(bdc.BDC_DATA_INDEX, body=body, status=200)
        url = bdc._get_latest_bdc_zip_url()
        assert url == f"https://www.sec.gov{bdc.BDC_BASE_PATH}/2026_04_bdc.zip"

    def test_returns_none_on_http_error(self, mocked_responses):
        mocked_responses.get(bdc.BDC_DATA_INDEX, status=500)
        assert bdc._get_latest_bdc_zip_url() is None

    def test_returns_none_when_no_links(self, mocked_responses):
        mocked_responses.get(
            bdc.BDC_DATA_INDEX, body="<html>no links</html>", status=200,
        )
        assert bdc._get_latest_bdc_zip_url() is None

    def test_list_all_sorts_oldest_first(self, mocked_responses):
        body = _index_html([
            "2026_04_bdc.zip", "2024q3_bdc.zip", "2025_06_bdc.zip",
        ])
        mocked_responses.get(bdc.BDC_DATA_INDEX, body=body, status=200)
        urls = bdc._list_all_bdc_zip_urls()
        names = [u.rsplit("/", 1)[-1] for u in urls]
        assert names == ["2024q3_bdc.zip", "2025_06_bdc.zip", "2026_04_bdc.zip"]

    def test_list_all_returns_empty_on_error(self, mocked_responses):
        mocked_responses.get(bdc.BDC_DATA_INDEX, status=502)
        assert bdc._list_all_bdc_zip_urls() == []


# ─── _ingest_dataframe end-to-end ────────────────────────────────────────────
def _make_soi_df(rows: list[dict]) -> pd.DataFrame:
    """Build a SOI-shaped DataFrame with the columns the parser reads."""
    # The set of columns _ingest_dataframe touches.
    cols = [
        "adsh", "cik", "name", "ddate", "period", "filed",
        "Industry Sector Axis", "Investment Type Axis",
        "Investment Interest Rate",
        "Investment, Interest Rate, Paid in Kind",
        "Investment Owned, Net Assets, Percentage",
        "Investment Maturity Date",
        "Investment Owned, Cost", "Investment Owned, Fair Value",
        "Investment, Identifier Axis",
        "Investment, Issuer Name Axis",
        "Investment, Issuer Affiliation Axis",
        "Industry Sector Axis",  # duplicate keys are fine, set later
        "Financial Instrument Axis",
        "Fair Value Hierarchy and NAV Axis",
        "Valuation Approach and Technique Axis",
        "Consolidation Items Axis",
        "Investment Company, Nonconsolidated Subsidiary Axis",
        "Segments Axis",
    ]
    # de-dup preserving order
    seen = set()
    uniq_cols = []
    for c in cols:
        if c not in seen:
            uniq_cols.append(c)
            seen.add(c)
    # Build with defaults of "" so blank axis fields are explicit.
    out_rows = []
    for r in rows:
        row = {c: "" for c in uniq_cols}
        row.update(r)
        out_rows.append(row)
    return pd.DataFrame(out_rows, columns=uniq_cols).astype(object)


class TestIngestDataframe:
    def test_empty_df_returns_zero(self, fresh_db):
        assert bdc._ingest_dataframe(pd.DataFrame()) == 0

    def test_missing_required_columns_returns_zero(self, fresh_db):
        # No cik / ddate / period → parser bails before touching the DB.
        df = pd.DataFrame([{"adsh": "x", "name": "Y"}])
        assert bdc._ingest_dataframe(df) == 0

    def test_persists_holdings_and_summary(self, fresh_db):
        # Two holdings for ARCC at 2026-03-31, both on the Identifier axis.
        # Note: only ONE breakdown axis may be populated per row, so we leave
        # "Industry Sector Axis" blank. "Investment Type Axis" is a rollup-side
        # column (used for non-accrual text scanning) but listed under
        # _BREAKDOWN_AXES too — keep it blank as well.
        df = _make_soi_df([
            {
                "adsh": "0001287750-26-000001", "cik": "0001287750",
                "name": "Ares Capital Corp", "ddate": "2026-03-31",
                "period": "2026-03-31", "filed": "2026-05-01",
                "Investment, Identifier Axis": "Acme Corp First Lien Loan",
                "Investment Interest Rate": "8.5",
                "Investment Owned, Cost": "1000",
                "Investment Owned, Fair Value": "950",
            },
            {
                "adsh": "0001287750-26-000001", "cik": "0001287750",
                "name": "Ares Capital Corp", "ddate": "2026-03-31",
                "period": "2026-03-31", "filed": "2026-05-01",
                "Investment, Identifier Axis": "BetaCo Second Lien",
                "Investment Interest Rate": "10.5",
                "Investment Owned, Cost": "500",
                "Investment Owned, Fair Value": "480",
            },
        ])
        n = bdc._ingest_dataframe(df, source="test")
        assert n == 2

        with db.get_conn() as conn:
            holdings = conn.execute(
                "SELECT * FROM bdc_holdings ORDER BY company_name"
            ).fetchall()
        assert len(holdings) == 2
        assert holdings[0]["bdc_name"] == "Ares Capital Corp"
        # Lien classification picks up "First Lien" from identifier text.
        assert holdings[0]["interest_rate"] == pytest.approx(8.5)
        assert holdings[1]["interest_rate"] == pytest.approx(10.5)

        # Summary row was rolled up.
        with db.get_conn() as conn:
            s = conn.execute("SELECT * FROM bdc_summary").fetchone()
        assert s is not None
        assert s["bdc_name"] == "Ares Capital Corp"
        assert s["total_fair_value"] == pytest.approx(1430)  # 950 + 480
        assert s["total_cost_basis"] == pytest.approx(1500)
        assert s["pct_first_lien"] == pytest.approx(950 / 1430)
        assert s["pct_second_lien"] == pytest.approx(480 / 1430)
        assert s["mark_to_cost"] == pytest.approx(1430 / 1500)
        # Source filing recorded.
        assert s["adsh"] == "0001287750-26-000001"
        assert s["filed"] == "2026-05-01"

    def test_drops_rollup_axes(self, fresh_db):
        # One real breakdown row + one rollup-axis row.
        df = _make_soi_df([
            {
                "adsh": "x", "cik": "1", "name": "ARCC",
                "ddate": "2026-03-31", "period": "2026-03-31",
                "Investment, Identifier Axis": "Real Holding",
                "Investment Owned, Cost": "100",
                "Investment Owned, Fair Value": "100",
            },
            {
                "adsh": "x", "cik": "1", "name": "ARCC",
                "ddate": "2026-03-31", "period": "2026-03-31",
                "Investment, Identifier Axis": "Should Not Appear",
                "Fair Value Hierarchy and NAV Axis": "Level 1",
                "Investment Owned, Cost": "999",
                "Investment Owned, Fair Value": "999",
            },
        ])
        n = bdc._ingest_dataframe(df)
        assert n == 1
        with db.get_conn() as conn:
            names = [r[0] for r in conn.execute(
                "SELECT company_name FROM bdc_holdings"
            )]
        assert "Should Not Appear" not in names

    def test_drops_rows_far_outside_period_window(self, fresh_db):
        # ddate 2 years prior to period → outside 380-day window.
        df = _make_soi_df([
            {
                "adsh": "x", "cik": "1", "name": "ARCC",
                "ddate": "2024-03-31", "period": "2026-03-31",
                "Investment, Identifier Axis": "Old Holding",
                "Investment Owned, Cost": "100",
                "Investment Owned, Fair Value": "100",
            },
        ])
        assert bdc._ingest_dataframe(df) == 0

    def test_drops_rows_with_no_cost_or_fv(self, fresh_db):
        df = _make_soi_df([
            {
                "adsh": "x", "cik": "1", "name": "ARCC",
                "ddate": "2026-03-31", "period": "2026-03-31",
                "Investment, Identifier Axis": "No Numbers",
                "Investment Owned, Cost": "",
                "Investment Owned, Fair Value": "",
            },
        ])
        assert bdc._ingest_dataframe(df) == 0

    def test_drops_rows_with_multiple_breakdown_axes(self, fresh_db):
        # Two breakdown axes populated → row dropped as sub-partition.
        df = _make_soi_df([
            {
                "adsh": "x", "cik": "1", "name": "ARCC",
                "ddate": "2026-03-31", "period": "2026-03-31",
                "Investment, Identifier Axis": "A",
                "Investment, Issuer Name Axis": "B",
                "Investment Owned, Cost": "100",
                "Investment Owned, Fair Value": "100",
            },
        ])
        assert bdc._ingest_dataframe(df) == 0

    def test_nonaccrual_detected_in_identifier_text(self, fresh_db):
        df = _make_soi_df([
            {
                "adsh": "x", "cik": "1", "name": "ARCC",
                "ddate": "2026-03-31", "period": "2026-03-31",
                "Investment, Identifier Axis": "Acme Corp (non-accrual)",
                "Investment Owned, Cost": "100",
                "Investment Owned, Fair Value": "50",
            },
        ])
        assert bdc._ingest_dataframe(df) == 1
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT is_nonaccrual FROM bdc_holdings"
            ).fetchone()
        assert row["is_nonaccrual"] == 1


# ─── fetch_bdc_data end-to-end ────────────────────────────────────────────────
def _make_soi_tsv_bytes(rows: list[dict]) -> bytes:
    df = _make_soi_df(rows)
    buf = io.StringIO()
    df.to_csv(buf, sep="\t", index=False)
    return buf.getvalue().encode("utf-8")


def _make_zip_bytes(tsv_bytes: bytes, name: str = "SOI.tsv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, tsv_bytes)
    return buf.getvalue()


class TestFetchBdcData:
    def test_returns_zero_when_no_zip_discovered(
        self, fresh_db, mocked_responses
    ):
        mocked_responses.get(
            bdc.BDC_DATA_INDEX, body="<html>no zips</html>", status=200,
        )
        assert bdc.fetch_bdc_data() == 0

    def test_end_to_end_index_to_persistence(
        self, fresh_db, mocked_responses
    ):
        zip_name = "2026_04_bdc.zip"
        zip_url = f"https://www.sec.gov{bdc.BDC_BASE_PATH}/{zip_name}"
        index_html = _index_html([zip_name])
        mocked_responses.get(bdc.BDC_DATA_INDEX, body=index_html, status=200)

        tsv = _make_soi_tsv_bytes([
            {
                "adsh": "0001-26-000001", "cik": "1287750",
                "name": "ARCC", "ddate": "2026-03-31",
                "period": "2026-03-31", "filed": "2026-05-01",
                "Investment, Identifier Axis": "Acme First Lien",
                "Investment Owned, Cost": "100",
                "Investment Owned, Fair Value": "98",
            },
        ])
        mocked_responses.get(zip_url, body=_make_zip_bytes(tsv), status=200)

        n = bdc.fetch_bdc_data()
        assert n == 1
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT bdc_name, total_fair_value FROM bdc_summary"
            ).fetchone()
        assert row["bdc_name"] == "ARCC"
        assert row["total_fair_value"] == pytest.approx(98)

    def test_zip_without_soi_returns_zero(
        self, fresh_db, mocked_responses
    ):
        zip_name = "2026_04_bdc.zip"
        zip_url = f"https://www.sec.gov{bdc.BDC_BASE_PATH}/{zip_name}"
        mocked_responses.get(
            bdc.BDC_DATA_INDEX, body=_index_html([zip_name]), status=200,
        )
        # Build a ZIP whose only file is NOT a SOI.tsv.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "not the soi file")
        mocked_responses.get(zip_url, body=buf.getvalue(), status=200)
        assert bdc.fetch_bdc_data() == 0


# ─── Query helpers ────────────────────────────────────────────────────────────
def _seed_summary(cik: str, period: str, **fields) -> None:
    """Insert a bdc_summary row with sensible defaults."""
    base = {
        "id": f"{cik}_{period}",
        "cik": cik, "bdc_name": f"BDC-{cik}", "period": period,
        "adsh": None, "filed": None,
        "total_fair_value": 1000.0, "total_cost_basis": 1000.0,
        "nonaccrual_fv": 0.0, "nonaccrual_cost": 0.0,
        "nonaccrual_rate_fv": 0.0, "nonaccrual_rate_cost": 0.0,
        "pct_first_lien": None, "pct_second_lien": None, "pct_equity": None,
        "wa_interest_rate": None, "mark_to_cost": 1.0,
        "n_holdings": 5, "fetched_at": NOW,
    }
    base.update(fields)
    with db.get_conn() as conn:
        cols = ", ".join(base.keys())
        placeholders = ", ".join(f":{k}" for k in base.keys())
        conn.execute(
            f"INSERT OR REPLACE INTO bdc_summary ({cols}) VALUES ({placeholders})",
            base,
        )


class TestGetBdcSummary:
    def test_returns_latest_period_when_unspecified(self, fresh_db):
        _seed_summary("1", "2026-03-31", total_fair_value=100)
        _seed_summary("1", "2025-12-31", total_fair_value=200)
        _seed_summary("2", "2026-03-31", total_fair_value=300)
        rows = bdc.get_bdc_summary()
        assert {r["cik"] for r in rows} == {"1", "2"}
        assert all(r["period"] == "2026-03-31" for r in rows)
        # Sorted by NAV desc.
        assert rows[0]["cik"] == "2"

    def test_filters_to_specified_period(self, fresh_db):
        _seed_summary("1", "2026-03-31", total_fair_value=100)
        _seed_summary("1", "2025-12-31", total_fair_value=200)
        rows = bdc.get_bdc_summary(period="2025-12-31")
        assert [r["cik"] for r in rows] == ["1"]

    def test_attaches_filing_url_when_adsh_present(self, fresh_db):
        _seed_summary("1287750", "2026-03-31",
                      adsh="0001287750-25-000026")
        rows = bdc.get_bdc_summary()
        assert rows[0]["filing_url"] is not None
        assert "/Archives/edgar/data/1287750/" in rows[0]["filing_url"]


class TestGetBdcSummaryLatestPerBdc:
    def test_per_bdc_max_period(self, fresh_db):
        _seed_summary("1", "2026-03-31", total_fair_value=100)
        _seed_summary("1", "2025-12-31", total_fair_value=200)
        # BDC 2 has only the older quarter — should still appear.
        _seed_summary("2", "2025-12-31", total_fair_value=300)
        rows = bdc.get_bdc_summary_latest_per_bdc()
        by_cik = {r["cik"]: r["period"] for r in rows}
        assert by_cik == {"1": "2026-03-31", "2": "2025-12-31"}

    def test_excludes_zero_or_null_fv(self, fresh_db):
        _seed_summary("1", "2026-03-31", total_fair_value=0)
        rows = bdc.get_bdc_summary_latest_per_bdc()
        assert rows == []


class TestGetBdcNonaccrualTrend:
    def test_requires_at_least_5_bdcs_per_period(self, fresh_db):
        # 4 BDCs → period filtered out by HAVING.
        for i in range(4):
            _seed_summary(str(i), "2026-03-31", total_fair_value=1000,
                          nonaccrual_fv=10)
        assert bdc.get_bdc_nonaccrual_trend() == []

        # Add a 5th — now period qualifies.
        _seed_summary("4", "2026-03-31", total_fair_value=1000, nonaccrual_fv=10)
        rows = bdc.get_bdc_nonaccrual_trend()
        assert len(rows) == 1
        r = rows[0]
        assert r["period"] == "2026-03-31"
        assert r["n_bdcs"] == 5
        assert r["avg_nonaccrual_rate"] == pytest.approx(50 / 5000)


class TestGetBdcAggregateTrend:
    def test_aggregates_per_period(self, fresh_db):
        for i in range(5):
            _seed_summary(str(i), "2026-03-31", total_fair_value=1000,
                          total_cost_basis=1000,
                          wa_interest_rate=8.0,
                          pct_first_lien=0.6, pct_second_lien=0.2,
                          pct_equity=0.2)
        rows = bdc.get_bdc_aggregate_trend()
        assert len(rows) == 1
        r = rows[0]
        assert r["n_bdcs"] == 5
        assert r["mark_to_cost"] == pytest.approx(1.0)
        assert r["wa_interest_rate"] == pytest.approx(8.0)
        assert r["pct_first_lien"] == pytest.approx(0.6)
        assert r["pct_second_lien"] == pytest.approx(0.2)
        assert r["pct_equity"] == pytest.approx(0.2)

    def test_skips_periods_below_5_bdcs(self, fresh_db):
        for i in range(3):
            _seed_summary(str(i), "2026-03-31", total_fair_value=1000)
        assert bdc.get_bdc_aggregate_trend() == []


class TestGetBdcNonaccruals:
    def test_returns_latest_period_nonaccruals(self, fresh_db):
        with db.get_conn() as conn:
            for i, period in enumerate(["2025-12-31", "2026-03-31"]):
                conn.execute(
                    "INSERT INTO bdc_holdings(id, adsh, cik, bdc_name, "
                    "period, company_name, cost_basis, fair_value, "
                    "is_nonaccrual, fetched_at) "
                    "VALUES(?, 'x', '1', 'ARCC', ?, ?, 100, 80, 1, ?)",
                    (f"h{i}", period, f"NA-{period}", NOW),
                )
            # A non-NA row that should be excluded.
            conn.execute(
                "INSERT INTO bdc_holdings(id, adsh, cik, bdc_name, period, "
                "company_name, cost_basis, fair_value, is_nonaccrual, "
                "fetched_at) VALUES('h2', 'x', '1', 'ARCC', '2026-03-31', "
                "'Healthy', 200, 200, 0, ?)",
                (NOW,),
            )
        out = bdc.get_bdc_nonaccruals()
        # Only the latest period (2026-03-31) and only is_nonaccrual=1.
        assert {r["company_name"] for r in out} == {"NA-2026-03-31"}

    def test_limit_caps_results(self, fresh_db):
        with db.get_conn() as conn:
            for i in range(5):
                conn.execute(
                    "INSERT INTO bdc_holdings(id, adsh, cik, bdc_name, "
                    "period, company_name, cost_basis, fair_value, "
                    "is_nonaccrual, fetched_at) "
                    "VALUES(?, 'x', '1', 'ARCC', '2026-03-31', ?, ?, 50, 1, ?)",
                    (f"h{i}", f"NA-{i}", 100.0 + i, NOW),
                )
        out = bdc.get_bdc_nonaccruals(limit=2)
        assert len(out) == 2


# ─── get_watch_list ──────────────────────────────────────────────────────────
class TestGetWatchList:
    def test_returns_yaml_watch_list(self):
        # Reads config/data_sources.yaml — verify the returned list is non-empty
        # and each entry has the documented shape.
        items = bdc.get_watch_list()
        assert isinstance(items, list)
        assert items, "expected at least one BDC in the watch list"
        for entry in items:
            assert "cik" in entry
            assert "name" in entry


# ─── _attach_filing_url ───────────────────────────────────────────────────────
class TestAttachFilingUrl:
    def test_no_adsh_yields_null_url(self):
        out = bdc._attach_filing_url({"cik": "1287750", "adsh": None})
        assert out["filing_url"] is None

    def test_present_adsh_yields_canonical_url(self):
        out = bdc._attach_filing_url({
            "cik": "1287750", "adsh": "0001287750-25-000026",
        })
        assert out["filing_url"] is not None
        assert "0001287750-25-000026-index.htm" in out["filing_url"]
