"""
Cover data/indicators.py — the numerical core of the recession-risk layer.

Test strategy:
  * Pure helpers (CDF, Svensson yield, quarter shifters, ym index) get
    direct-value assertions against hand-computed answers.
  * CSV/ZIP loaders are exercised against `responses`-mocked HTTP bodies that
    embed only the fields the parser actually reads.
  * DB-coupled computations (probit, credit impulse, ensemble) seed the metrics
    table, run the function, and verify the rows it wrote.
"""

from __future__ import annotations

import io
import math
import zipfile

import pytest

from cache import db
from data import indicators as ind


NOW = "2026-05-29T12:00:00+00:00"


# ─── Pure-math helpers ────────────────────────────────────────────────────────
class TestNormCdf:
    @pytest.mark.parametrize("x, expected", [
        (0.0, 0.5),
        (1.0, 0.8413447460685),
        (-1.0, 0.1586552539314),
        (2.0, 0.9772498680518),
    ])
    def test_known_values(self, x, expected):
        assert ind._norm_cdf(x) == pytest.approx(expected, abs=1e-9)


class TestSvenssonYield:
    def test_long_maturity_approaches_beta0(self):
        # As m → ∞, all decay terms vanish; yield → β0.
        y = ind._svensson_yield(500.0, 3.5, -1.0, 0.5, 0.2, 1.5, 4.0)
        assert y == pytest.approx(3.5, abs=1e-3)

    def test_returns_finite_for_typical_inputs(self):
        # A 2-year point with normal-looking coefficients should be finite,
        # bounded, and order-of-magnitude correct (~ a few %).
        y = ind._svensson_yield(2.0, 4.0, -2.0, 1.0, -0.5, 1.5, 6.0)
        assert math.isfinite(y)
        assert -5.0 < y < 15.0


class TestQuarterShift:
    @pytest.mark.parametrize("d, back, expected", [
        ("2026-04-01", 1, "2026-01-01"),
        ("2026-04-01", 4, "2025-04-01"),
        ("2026-01-01", 1, "2025-10-01"),
        ("2026-01-01", 8, "2024-01-01"),
    ])
    def test_quarter_arithmetic(self, d, back, expected):
        assert ind._quarter_shift(d, back) == expected


class TestBisQuarterToDate:
    @pytest.mark.parametrize("label, expected", [
        ("1990-Q1", "1990-01-01"),
        ("2020-Q2", "2020-04-01"),
        ("2024-Q3", "2024-07-01"),
        ("2024-Q4", "2024-10-01"),
        ("bad",      None),
        ("2024-Q5",  None),
    ])
    def test_parsing(self, label, expected):
        assert ind._bis_quarter_to_date(label) == expected


class TestYmIndex:
    def test_index_is_total_months_since_year_zero(self):
        assert ind._ym_index("2026-05") - ind._ym_index("2026-01") == 4
        assert ind._ym_index("2026-01") - ind._ym_index("2025-01") == 12


# ─── _forward_recession_targets ───────────────────────────────────────────────
class TestForwardRecessionTargets:
    def test_drops_months_beyond_observable_window(self):
        # 14 months of zeros — the last 12 months have no observed forward
        # window, so only months 1-2 should produce a target.
        usrec = {f"2025-{m:02d}": 0 for m in range(1, 13)}
        usrec.update({"2026-01": 0, "2026-02": 0})
        targets = ind._forward_recession_targets(usrec)
        assert set(targets) == {"2025-01", "2025-02"}
        assert all(v == 0.0 for v in targets.values())

    def test_marks_recession_when_any_future_month_is_a_recession(self):
        usrec = {f"2025-{m:02d}": 0 for m in range(1, 13)}
        usrec.update({"2026-01": 1, "2026-02": 0})
        targets = ind._forward_recession_targets(usrec)
        assert targets["2025-01"] == 1.0  # 2026-01 falls inside next 12mo
        assert targets["2025-02"] == 1.0  # ditto


# ─── _fit_logit ──────────────────────────────────────────────────────────────
class TestFitLogit:
    def test_recovers_positive_slope_on_clear_signal(self):
        # y = 1 when x large, 0 when small → slope must come out positive.
        x = list(range(-10, 11))
        y = [1.0 if xi > 0 else 0.0 for xi in x]
        coefs = ind._fit_logit(x, y)
        assert coefs is not None
        b0, b1 = coefs
        assert b1 > 0
        assert math.isfinite(b0)

    def test_nonneg_slopes_clamps_negative_to_zero(self):
        # y = 1 when x small → unrestricted slope would be negative.
        # nonneg_slopes=True must clamp it to ≥ 0.
        x = list(range(-10, 11))
        y = [1.0 if xi < 0 else 0.0 for xi in x]
        coefs = ind._fit_logit(x, y, nonneg_slopes=True)
        assert coefs is not None
        assert coefs[1] >= 0.0


# ─── _pca_first_component ────────────────────────────────────────────────────
class TestPcaFirstComponent:
    def test_two_perfectly_correlated_columns(self):
        # Two columns of the same standardized series → PC1 loads equally.
        col = [-2, -1, 0, 1, 2, -1, 0, 1, 2, -2]
        rows = [[c, c] for c in col]
        scores = ind._pca_first_component(rows)
        assert scores is not None
        # Output rescaled to unit sd.
        sd = (sum(s * s for s in scores) / len(scores)) ** 0.5
        assert sd == pytest.approx(1.0, abs=1e-6)

    def test_too_few_rows_returns_none(self):
        assert ind._pca_first_component([[1.0, 2.0], [2.0, 3.0]]) is None

    def test_constant_input_returns_none(self):
        # All zeros → degenerate covariance → None.
        rows = [[0.0, 0.0] for _ in range(20)]
        assert ind._pca_first_component(rows) is None


# ─── compute_nyfed_recession_probit ──────────────────────────────────────────
class TestNyfedRecessionProbit:
    def test_writes_zero_rows_when_no_t10y3m(self, fresh_db):
        assert ind.compute_nyfed_recession_probit() == 0

    def test_averages_within_month_and_applies_probit(self, fresh_db):
        # Seed two daily spread values in the same month; the function should
        # average them and apply Φ(α + β · spread).
        for date, value in [("2026-05-15", 0.50), ("2026-05-20", 0.30)]:
            db.upsert_metric({
                "series_id": "T10Y3M", "label": "10y-3m spread",
                "category": "rates", "date": date, "value": value,
                "fetched_at": NOW,
            })
        n = ind.compute_nyfed_recession_probit()
        assert n == 1

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id='NYFED_RECESSION_PROB'"
            ).fetchone()
        # Date should be the last day in the month seen (2026-05-20).
        assert row["date"] == "2026-05-20"
        # Hand check: avg spread = 0.40 → Φ(-0.5333 - 0.6629*0.40) * 100.
        expected_pct = ind._norm_cdf(-0.5333 - 0.6629 * 0.40) * 100.0
        assert row["value"] == pytest.approx(round(expected_pct, 3), abs=1e-3)

    def test_processes_multiple_months_separately(self, fresh_db):
        for date, value in [
            ("2026-04-15", 0.10),
            ("2026-05-15", 0.50),
            ("2026-05-20", 0.30),
        ]:
            db.upsert_metric({
                "series_id": "T10Y3M", "label": "x", "category": "rates",
                "date": date, "value": value, "fetched_at": NOW,
            })
        assert ind.compute_nyfed_recession_probit() == 2


# ─── fetch_excess_bond_premium ───────────────────────────────────────────────
class TestExcessBondPremium:
    CSV = (
        "date,gz_spread,ebp,est_prob\n"
        "2026-03-01,2.10,0.55,0.12\n"
        "2026-04-01,2.30,0.70,0.18\n"
        "2026-05-01,NA,NA,NA\n"  # missing row should be skipped
    )

    def test_parses_csv_and_scales_probability_column(
        self, fresh_db, mocked_responses
    ):
        mocked_responses.get(
            ind._EBP_CSV_URL, body=self.CSV, status=200,
            content_type="text/csv",
        )
        n = ind.fetch_excess_bond_premium()
        # 2 valid dates × 3 columns each = 6 row writes
        assert n == 6

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT series_id, date, value FROM metrics ORDER BY series_id, date"
            ).fetchall()

        by_series = {}
        for r in rows:
            by_series.setdefault(r["series_id"], {})[r["date"]] = r["value"]

        # est_prob is scaled ×100 (probit → percent), others stay 1:1.
        assert by_series["EBP_REC_PROB"]["2026-03-01"] == pytest.approx(12.0)
        assert by_series["EBP_REC_PROB"]["2026-04-01"] == pytest.approx(18.0)
        assert by_series["EBP"]["2026-03-01"] == pytest.approx(0.55)
        assert by_series["GZ_SPREAD"]["2026-04-01"] == pytest.approx(2.30)

    def test_network_error_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(ind._EBP_CSV_URL, status=500)
        assert ind.fetch_excess_bond_premium() == 0


# ─── compute_near_term_forward_spread ────────────────────────────────────────
class TestNearTermForwardSpread:
    HEADER_AND_ROW = (
        "preamble line that should be skipped\n"
        "Date,BETA0,BETA1,BETA2,BETA3,TAU1,TAU2\n"
        "2026-05-01,3.5,-1.0,0.5,0.2,1.5,4.0\n"
    )

    def test_seeds_one_row_per_csv_obs(self, fresh_db, mocked_responses):
        mocked_responses.get(
            ind._GSW_URL, body=self.HEADER_AND_ROW, status=200,
            content_type="text/csv",
        )
        n = ind.compute_near_term_forward_spread()
        assert n == 1

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id='NEAR_TERM_FWD_SPREAD'"
            ).fetchone()
        assert row["date"] == "2026-05-01"

        # Recompute the same formula by hand to confirm the pipeline is intact.
        y = lambda m: ind._svensson_yield(m, 3.5, -1.0, 0.5, 0.2, 1.5, 4.0)
        expected = (y(1.75) * 1.75 - y(1.50) * 1.50) / 0.25 - y(0.25)
        assert row["value"] == pytest.approx(round(expected, 4), abs=1e-4)

    def test_dates_before_1980_are_dropped(self, fresh_db, mocked_responses):
        old = (
            "Date,BETA0,BETA1,BETA2,BETA3,TAU1,TAU2\n"
            "1975-01-01,3,-1,0.5,0.2,1.5,4.0\n"
            "2024-01-01,3,-1,0.5,0.2,1.5,4.0\n"
        )
        mocked_responses.get(ind._GSW_URL, body=old, status=200)
        n = ind.compute_near_term_forward_spread()
        assert n == 1  # only the 2024 row


# ─── compute_credit_impulse ──────────────────────────────────────────────────
class TestCreditImpulse:
    def test_returns_zero_when_components_missing(self, fresh_db):
        assert ind.compute_credit_impulse() == 0

    def test_writes_one_row_per_valid_quarter(self, fresh_db):
        # Build 10 quarters of TCMDO (growing $millions) and GDP ($billions).
        def _q(year, q):
            return f"{year:04d}-{(q - 1) * 3 + 1:02d}-01"

        # 2024Q1..2026Q2 = 10 quarters. We need 8 lags before the first emit.
        quarters = []
        for y in (2024, 2025, 2026):
            for q in (1, 2, 3, 4):
                if y == 2026 and q > 2:
                    break
                quarters.append(_q(y, q))

        # TCMDO grows by 100,000 ($M) per quarter; GDP at 25,000 ($B).
        for i, d in enumerate(quarters):
            db.upsert_metric({
                "series_id": "TCMDO", "label": "credit stock",
                "category": "credit", "date": d,
                "value": 50_000_000 + i * 100_000, "fetched_at": NOW,
            })
            db.upsert_metric({
                "series_id": "GDP", "label": "GDP", "category": "macro",
                "date": d, "value": 25_000.0, "fetched_at": NOW,
            })
        n = ind.compute_credit_impulse()
        # 10 dates, only i >= 8 produce an output → 2 rows.
        assert n == 2

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id='CREDIT_IMPULSE' ORDER BY date"
            ).fetchall()
        # Linear credit growth ⇒ flow_now == flow_prior ⇒ impulse = 0.
        for r in rows:
            assert r["value"] == pytest.approx(0.0, abs=1e-9)


# ─── fetch_ofr_fsi ───────────────────────────────────────────────────────────
class TestOfrFsi:
    CSV = (
        "Date,OFR FSI,Credit,Equity valuation,Safe assets,Funding,Volatility\n"
        "2026-05-27,1.20,0.40,0.30,-0.10,0.30,0.30\n"
        "2026-05-28,NA,NA,NA,NA,NA,NA\n"
    )

    def test_writes_one_row_per_column_per_valid_date(
        self, fresh_db, mocked_responses
    ):
        mocked_responses.get(
            ind._OFR_FSI_URL, body=self.CSV, status=200,
            content_type="text/csv",
        )
        n = ind.fetch_ofr_fsi()
        assert n == 6  # 1 valid row × 6 columns

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM metrics WHERE series_id='OFR_FSI'"
            ).fetchone()
        assert row["value"] == pytest.approx(1.20)


# ─── fetch_bis_credit_gap ────────────────────────────────────────────────────
class TestBisCreditGap:
    def _build_zip(self) -> bytes:
        csv_text = (
            "BORROWERS_CTY,TC_BORROWERS,TC_LENDERS,CG_DTYPE,1990-Q1,1990-Q2\n"
            "US,P,A,C,5.0,7.5\n"
            "US,P,A,B,99.0,99.0\n"   # wrong dtype, must be skipped
            "DE,P,A,C,2.0,3.0\n"     # wrong country, must be skipped
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("WS_CREDIT_GAP.csv", csv_text)
        return buf.getvalue()

    def test_pulls_only_us_private_all_lenders_gap(
        self, fresh_db, mocked_responses
    ):
        mocked_responses.get(
            ind._BIS_CGAP_URL, body=self._build_zip(), status=200,
            content_type="application/zip",
        )
        n = ind.fetch_bis_credit_gap()
        assert n == 2

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id='BIS_CREDIT_GAP_US' ORDER BY date"
            ).fetchall()
        assert [(r["date"], r["value"]) for r in rows] == [
            ("1990-01-01", 5.0),
            ("1990-04-01", 7.5),
        ]


# ─── compute_recession_ensemble ──────────────────────────────────────────────
class TestRecessionEnsemble:
    def test_writes_zero_when_no_components(self, fresh_db):
        assert ind.compute_recession_ensemble() == 0

    def test_equal_weight_fallback_when_stack_cannot_fit(self, fresh_db):
        # Seed only NYFED and EBP probabilities — no NTFS, no USREC.
        # The stack needs all three features + 60+ aligned months, so this
        # path must hit the equal-weight fallback for available components.
        for ym in ("2025-01", "2025-02", "2025-03"):
            db.upsert_metric({
                "series_id": ind._RECESSION_PROBIT_ID, "label": "ny",
                "category": "recession_risk", "date": f"{ym}-15",
                "value": 30.0, "fetched_at": NOW,
            })
            db.upsert_metric({
                "series_id": "EBP_REC_PROB", "label": "ebp",
                "category": "recession_risk", "date": f"{ym}-15",
                "value": 50.0, "fetched_at": NOW,
            })

        # No FRED key → NTFS probit fit short-circuits to {}.
        import os as _os
        _os.environ["FRED_API_KEY"] = ""  # force the fit to return empty
        # Re-init settings.FRED_API_KEY by patching directly (it cached at import).
        from config import settings
        original = settings.FRED_API_KEY
        settings.FRED_API_KEY = ""
        try:
            n = ind.compute_recession_ensemble()
        finally:
            settings.FRED_API_KEY = original

        assert n == 3

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id=? ORDER BY date",
                (ind._ENSEMBLE_ID,),
            ).fetchall()
        # Equal-weight of 30 and 50 is 40.
        for r in rows:
            assert r["value"] == pytest.approx(40.0)
