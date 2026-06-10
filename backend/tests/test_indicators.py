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
import statistics
import sys
import types
import zipfile

import pandas as pd
import pytest

from cache import db
from config import settings
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


# ═══════════════════════════════════════════════════════════════════════════════
# Characterization tests appended to lock in CURRENT numeric behavior of the
# previously uncovered paths (CFSI, NTFS probit fit, ensemble stack, ingest
# edge cases). Synthetic inputs are designed so fitted logits are *saturated*
# (two-valued features), making the fitted probabilities exact group means —
# closed-form value locks that don't reimplement the production math.
#
# Intentionally NOT covered (noted, not fixed — source must stay untouched):
#   • The CFSI PCA sign-flip executes only when LAPACK returns a
#     DSR-anti-aligned eigenvector (sign is arbitrary per platform). The
#     orientation *invariant* is locked by
#     TestComputeCfsi.test_pca_is_oriented_to_the_dsr_input, which is
#     deterministic regardless of which branch runs. A PC1 orthogonal to the
#     DSR z-score (cov ~ 0) is treated as degenerate → equal-weight fallback
#     (test_pc_orthogonal_to_dsr_falls_back_to_equal_weight).
#   • indicators.py:789-790, 896 — defensive numerics guards
#     (np.linalg.LinAlgError from solve; a None NTFS-probit fit) that ridge
#     regularization makes practically unreachable.
#   • indicators.py:1003-1004 — dead code: `months` is the union of the
#     component dicts' keys, so _equal_weight() can never return None there.
# ═══════════════════════════════════════════════════════════════════════════════


def _seed_metric(series_id: str, date: str, value: float) -> None:
    db.upsert_metric({
        "series_id": series_id, "label": "x", "category": "test",
        "date": date, "value": value, "fetched_at": NOW,
    })


def _ym(i: int, start_year: int = 2000) -> str:
    """Month index i (0 = Jan of start_year) -> 'YYYY-MM'."""
    return f"{start_year + i // 12:04d}-{i % 12 + 1:02d}"


def _install_fake_fredapi(monkeypatch, series_by_id: dict) -> None:
    """Inject a fake `fredapi` module (same convention as test_fred.py).

    `series_by_id` maps series_id -> pandas Series, or -> an Exception
    instance to be raised by get_series.
    """
    class StubFred:
        def __init__(self, *args, **kwargs):
            pass

        def get_series(self, series_id, observation_start=None):
            out = series_by_id[series_id]
            if isinstance(out, Exception):
                raise out
            return out

    fake_mod = types.ModuleType("fredapi")
    fake_mod.Fred = StubFred
    monkeypatch.setitem(sys.modules, "fredapi", fake_mod)


# ─── Shared synthetic recession geometry ─────────────────────────────────────
#
# 132 monthly USREC observations 2000-01..2010-12 (indices 0..131) with two
# recessions: 2003-01..2003-12 (36..47) and 2008-01..2008-12 (96..107).
# "Recession within the next 12 months" is then true exactly for indices
# 24..46 and 84..106 (46 positive months out of the 120 scored 2000-01..2009-12).
_REC_MONTHS = set(range(36, 48)) | set(range(96, 108))
_POS_MONTHS = set(range(24, 47)) | set(range(84, 107))
# The binary "stress signal": positives minus 4 misses, plus 6 false alarms.
# Signal group: 48 months, 42 positive  → saturated P = 42/48 = 87.5%.
# Calm group:   72 months,  4 positive  → saturated P = 4/72 ≈ 5.5556%.
_SIGNAL_MONTHS = (_POS_MONTHS - {24, 25, 26, 27}) | set(range(0, 6))
_P_SIGNAL = 100.0 * 42 / 48   # 87.5
_P_CALM = 100.0 * 4 / 72      # 5.5555…


def _seed_usrec(months: int = 132, all_zero: bool = False) -> None:
    for i in range(months):
        flag = 0.0 if all_zero else (1.0 if i in _REC_MONTHS else 0.0)
        _seed_metric("USREC", f"{_ym(i)}-01", flag)


# ─── fetch_excess_bond_premium: parser edge cases ────────────────────────────
class TestExcessBondPremiumEdgeCases:
    def test_empty_body_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(ind._EBP_CSV_URL, body="", status=200)
        assert ind.fetch_excess_bond_premium() == 0

    def test_missing_date_column_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(
            ind._EBP_CSV_URL, body="foo,bar\n1,2\n", status=200,
        )
        assert ind.fetch_excess_bond_premium() == 0

    def test_blank_bad_dates_and_unparsable_floats_are_skipped(
        self, fresh_db, mocked_responses
    ):
        csv_body = (
            "date,gz_spread,ebp,est_prob\n"
            ",1,1,1\n"                      # blank date → skipped
            "not-a-date,1,1,1\n"            # unparseable date → skipped
            "2026-01-01,abc,0.5,xx\n"       # only ebp parses
        )
        mocked_responses.get(ind._EBP_CSV_URL, body=csv_body, status=200)
        assert ind.fetch_excess_bond_premium() == 1
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT series_id, value FROM metrics"
            ).fetchall()
        assert [(r["series_id"], r["value"]) for r in rows] == [("EBP", 0.5)]

    def test_columns_absent_from_header_are_ignored(
        self, fresh_db, mocked_responses
    ):
        # est_prob column missing entirely → only 2 of 3 series written.
        csv_body = "date,gz_spread,ebp\n2026-01-01,1.0,0.5\n"
        mocked_responses.get(ind._EBP_CSV_URL, body=csv_body, status=200)
        assert ind.fetch_excess_bond_premium() == 2


# ─── compute_near_term_forward_spread: edge cases ────────────────────────────
class TestNearTermForwardSpreadEdgeCases:
    def test_network_error_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(ind._GSW_URL, status=500)
        assert ind.compute_near_term_forward_spread() == 0

    def test_missing_header_row_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(
            ind._GSW_URL, body="junk line\nanother junk line\n", status=200,
        )
        assert ind.compute_near_term_forward_spread() == 0

    def test_na_parameter_rows_are_skipped(self, fresh_db, mocked_responses):
        body = (
            "Date,BETA0,BETA1,BETA2,BETA3,TAU1,TAU2\n"
            "2024-05-01,NA,NA,NA,NA,NA,NA\n"
        )
        mocked_responses.get(ind._GSW_URL, body=body, status=200)
        assert ind.compute_near_term_forward_spread() == 0


# ─── compute_credit_impulse: GDP gaps + exact value lock ─────────────────────
class TestCreditImpulseValues:
    @staticmethod
    def _q(i: int) -> str:
        return f"{2024 + i // 4:04d}-{(i % 4) * 3 + 1:02d}-01"

    def test_quarter_missing_gdp_is_skipped(self, fresh_db):
        # 9 quarters of TCMDO but no GDP at the only emittable quarter (i=8).
        for i in range(9):
            _seed_metric("TCMDO", self._q(i), 1_000_000 + i * 10_000)
            if i != 8:
                _seed_metric("GDP", self._q(i), 20_000.0)
        assert ind.compute_credit_impulse() == 0

    def test_exact_impulse_for_accelerating_credit(self, fresh_db):
        # flow_now = (C8-C4)/1000 = 300; flow_prior = (C4-C0)/1000 = 200;
        # impulse = (300-200)/20000*100 = 0.5 exactly.
        tcmdo = [
            1_000_000, 1_050_000, 1_100_000, 1_150_000,
            1_200_000, 1_275_000, 1_350_000, 1_425_000,
            1_500_000,
        ]
        for i, c in enumerate(tcmdo):
            _seed_metric("TCMDO", self._q(i), c)
            _seed_metric("GDP", self._q(i), 20_000.0)
        assert ind.compute_credit_impulse() == 1
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT date, value FROM metrics WHERE series_id='CREDIT_IMPULSE'"
            ).fetchone()
        assert row["date"] == self._q(8)
        assert row["value"] == pytest.approx(0.5, abs=1e-9)


# ─── fetch_ofr_fsi: edge cases ───────────────────────────────────────────────
class TestOfrFsiEdgeCases:
    def test_network_error_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(ind._OFR_FSI_URL, status=500)
        assert ind.fetch_ofr_fsi() == 0

    def test_header_without_date_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(
            ind._OFR_FSI_URL, body="NotDate,OFR FSI\nx,1\n", status=200,
        )
        assert ind.fetch_ofr_fsi() == 0

    def test_blank_dates_and_bad_floats_are_skipped(
        self, fresh_db, mocked_responses
    ):
        body = (
            "Date,OFR FSI,Credit,Equity valuation,Safe assets,Funding,Volatility\n"
            ",1,1,1,1,1,1\n"               # blank date → whole row skipped
            "2026-01-02,abc,,,,,\n"        # bad float + empty cells → no writes
        )
        mocked_responses.get(ind._OFR_FSI_URL, body=body, status=200)
        assert ind.fetch_ofr_fsi() == 0


# ─── fetch_bis_credit_gap: edge cases ────────────────────────────────────────
class TestBisCreditGapEdgeCases:
    @staticmethod
    def _zip_of(csv_text: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("WS_CREDIT_GAP.csv", csv_text)
        return buf.getvalue()

    def test_network_error_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(ind._BIS_CGAP_URL, status=500)
        assert ind.fetch_bis_credit_gap() == 0

    def test_non_zip_body_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(ind._BIS_CGAP_URL, body=b"not a zip", status=200)
        assert ind.fetch_bis_credit_gap() == 0

    def test_empty_csv_returns_zero(self, fresh_db, mocked_responses):
        mocked_responses.get(
            ind._BIS_CGAP_URL, body=self._zip_of(""), status=200,
        )
        assert ind.fetch_bis_credit_gap() == 0

    def test_missing_dimension_columns_returns_zero(
        self, fresh_db, mocked_responses
    ):
        csv_text = "BORROWERS_CTY,TC_BORROWERS,CG_DTYPE,1990-Q1\nUS,P,C,5.0\n"
        mocked_responses.get(
            ind._BIS_CGAP_URL, body=self._zip_of(csv_text), status=200,
        )
        assert ind.fetch_bis_credit_gap() == 0

    def test_short_empty_and_invalid_value_cells_are_skipped(
        self, fresh_db, mocked_responses
    ):
        # q1 unparsable, q2 empty, q3 beyond the row's length → 0 rows stored.
        csv_text = (
            "BORROWERS_CTY,TC_BORROWERS,TC_LENDERS,CG_DTYPE,1990-Q1,1990-Q2,1990-Q3\n"
            "US,P,A,C,abc,\n"
        )
        mocked_responses.get(
            ind._BIS_CGAP_URL, body=self._zip_of(csv_text), status=200,
        )
        assert ind.fetch_bis_credit_gap() == 0

    def test_non_matching_rows_before_the_us_series_are_skipped(
        self, fresh_db, mocked_responses
    ):
        # The wrong-country row precedes the match (the loop breaks after the
        # first match, so only a *preceding* row exercises the dimension filter).
        csv_text = (
            "BORROWERS_CTY,TC_BORROWERS,TC_LENDERS,CG_DTYPE,1990-Q1\n"
            "DE,P,A,C,2.0\n"
            "US,P,A,C,5.0\n"
        )
        mocked_responses.get(
            ind._BIS_CGAP_URL, body=self._zip_of(csv_text), status=200,
        )
        assert ind.fetch_bis_credit_gap() == 1
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT date, value FROM metrics WHERE series_id='BIS_CREDIT_GAP_US'"
            ).fetchone()
        assert (row["date"], row["value"]) == ("1990-01-01", 5.0)


# ─── _pca_first_component: degenerate inputs ─────────────────────────────────
class TestPcaDegenerateInputs:
    def test_nan_input_returns_none(self):
        rows = [[float("nan"), 1.0]] + [[float(i), float(i)] for i in range(6)]
        assert ind._pca_first_component(rows) is None

    def test_ragged_input_hits_exception_path(self):
        # np.asarray on a ragged list raises → caught → None.
        rows = [[1.0, 2.0], [1.0], [3.0, 4.0], [5.0, 6.0]]
        assert ind._pca_first_component(rows) is None


# ─── _fit_logit: exact saturated-fit value lock ──────────────────────────────
class TestFitLogitSaturatedValues:
    def test_two_group_design_recovers_group_log_odds_exactly(self):
        # x ∈ {-1, +1}; mean(y | x=-1) = 0.75, mean(y | x=+1) = 0.25.
        # The saturated logit MLE satisfies p̂(x) = group mean, so
        # b0 = 0 and b1 = -ln(3) in closed form. Also exercises the
        # Newton convergence break (step < 1e-9).
        x = [-1.0] * 4 + [1.0] * 4
        y = [1, 1, 1, 0] + [1, 0, 0, 0]
        coefs = ind._fit_logit(x, y)
        assert coefs is not None
        b0, b1 = coefs
        assert b0 == pytest.approx(0.0, abs=1e-3)
        assert b1 == pytest.approx(-math.log(3.0), abs=1e-3)


# ─── _cfsi_fred_components ───────────────────────────────────────────────────
class TestCfsiFredComponents:
    def test_returns_none_without_fred_key(self, monkeypatch):
        monkeypatch.setattr(settings, "FRED_API_KEY", "")
        assert ind._cfsi_fred_components() is None

    def test_normalizes_to_quarter_start_and_latest_obs_wins(self, monkeypatch):
        # Monthly obs within 2020Q1 collapse to '2020-01-01' (Mar wins);
        # the lone 2020Q2 obs maps to '2020-04-01'.
        s = pd.Series(
            [10.0, 11.0, 12.0, 20.0],
            index=pd.to_datetime(
                ["2020-01-15", "2020-02-15", "2020-03-15", "2020-05-01"]
            ),
        )
        _install_fake_fredapi(
            monkeypatch, {sid: s for sid in ind._CFSI_FRED_SERIES}
        )
        out = ind._cfsi_fred_components()
        assert out is not None
        assert set(out) == set(ind._CFSI_FRED_SERIES)
        assert out["CDSP"] == {"2020-01-01": 12.0, "2020-04-01": 20.0}

    def test_fred_exception_returns_none(self, monkeypatch):
        _install_fake_fredapi(
            monkeypatch,
            {sid: RuntimeError("boom") for sid in ind._CFSI_FRED_SERIES},
        )
        assert ind._cfsi_fred_components() is None


# ─── compute_cfsi ────────────────────────────────────────────────────────────
class TestComputeCfsi:
    # Underlying synthetic "stress" series, one value per quarter 2020Q1..2023Q4.
    V = [1.0, 3.0, 2.0, 5.0, 4.0, 7.0, 6.0, 9.0, 8.0,
         11.0, 10.0, 13.0, 12.0, 15.0, 14.0, 16.0]

    @staticmethod
    def _qdate(j: int, base_year: int = 2020) -> str:
        """Quarter index j (0 = base_year Q1, may be negative) -> 'YYYY-MM-01'."""
        total = base_year * 4 + j
        y, q = divmod(total, 4)
        return f"{y:04d}-{q * 3 + 1:02d}-01"

    def _fred_stub_series(self) -> dict:
        """Build the four FRED components so every CFSI input equals V.

        - CDSP[q_j] = V[j]
        - DRTSCLCC[q_{j-4}] = V[j]  (compute_cfsi reads SLOOS lagged 4Q)
        - CPIAUCSL = 100 flat → real revolving credit = REVOLSL / 100
        - REVOLSL grows so YoY real growth at q_j is exactly V[j] percent.
        """
        qd = self._qdate
        cdsp = pd.Series(
            self.V, index=pd.to_datetime([qd(j) for j in range(16)])
        )
        sloos = pd.Series(
            self.V, index=pd.to_datetime([qd(j - 4) for j in range(16)])
        )
        all_dates = pd.to_datetime([qd(j) for j in range(-4, 16)])
        cpi = pd.Series([100.0] * 20, index=all_dates)
        revol_vals: dict[int, float] = {j: 100.0 for j in range(-4, 0)}
        for j in range(16):
            revol_vals[j] = revol_vals[j - 4] * (1.0 + self.V[j] / 100.0)
        revol = pd.Series(
            [revol_vals[j] for j in range(-4, 16)], index=all_dates
        )
        return {
            "CDSP": cdsp, "DRTSCLCC": sloos,
            "REVOLSL": revol, "CPIAUCSL": cpi,
        }

    def test_missing_component_returns_zero(self, fresh_db, monkeypatch):
        monkeypatch.setattr(settings, "FRED_API_KEY", "")
        for j in range(16):
            _seed_metric("CDSP", self._qdate(j), 5.0)
        assert ind.compute_cfsi() == 0

    def test_fewer_than_eight_aligned_quarters_returns_zero(
        self, fresh_db, monkeypatch
    ):
        monkeypatch.setattr(settings, "FRED_API_KEY", "")
        qd = self._qdate
        for j in range(-4, 5):
            _seed_metric("REVOLSL", qd(j), 100.0)
            _seed_metric("CPIAUCSL", qd(j), 100.0)
            _seed_metric("DRTSCLCC", qd(j), 10.0)
        for j in range(5):  # only 5 aligned quarters < 8
            _seed_metric("CDSP", qd(j), 5.0)
            _seed_metric("HHDC_FLOW30_ALL", qd(j), 2.0)
        assert ind.compute_cfsi() == 0

    def test_constant_inputs_use_equal_weight_and_store_zeros(
        self, fresh_db, monkeypatch
    ):
        # Metrics-table fallback (no FRED key). All inputs constant → every
        # z-score is 0 (pstdev guard substitutes sd=1), PCA is degenerate →
        # equal-weight path → CFSI is exactly 0.0 for all 16 quarters.
        monkeypatch.setattr(settings, "FRED_API_KEY", "")
        qd = self._qdate
        for j in range(-4, 16):
            _seed_metric("REVOLSL", qd(j), 100.0)
            _seed_metric("CPIAUCSL", qd(j), 100.0)
            _seed_metric("DRTSCLCC", qd(j), 10.0)
        for j in range(16):
            _seed_metric("CDSP", qd(j), 5.0)
            _seed_metric("HHDC_FLOW30_ALL", qd(j), 2.0)
        assert ind.compute_cfsi() == 16
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT value FROM metrics WHERE series_id='CFSI'"
            ).fetchall()
        assert len(rows) == 16
        for r in rows:
            assert r["value"] == pytest.approx(0.0, abs=1e-9)

    def test_pca_path_equals_zscore_when_all_components_co_move(
        self, fresh_db, monkeypatch
    ):
        # FRED-direct path (key present from conftest). All four standardized
        # inputs are identical, so PC1 loads them equally and the unit-σ,
        # stress-oriented CFSI must equal the population z-score of V exactly.
        _install_fake_fredapi(monkeypatch, self._fred_stub_series())
        for j in range(16):
            _seed_metric("HHDC_FLOW30_ALL", self._qdate(j), self.V[j])

        assert ind.compute_cfsi() == 16

        mu = statistics.fmean(self.V)
        sd = statistics.pstdev(self.V)
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id='CFSI' ORDER BY date"
            ).fetchall()
        assert [r["date"] for r in rows] == [self._qdate(j) for j in range(16)]
        for j, r in enumerate(rows):
            expected = (self.V[j] - mu) / sd
            assert r["value"] == pytest.approx(expected, abs=1e-3)

    def test_pca_is_oriented_to_the_dsr_input(self, fresh_db, monkeypatch):
        # Negate the DSR component only: the other three still co-move with V,
        # but the orientation rule aligns the PC with the *DSR* z-score, so the
        # stored CFSI must equal MINUS the z-score of V — deterministic
        # regardless of LAPACK's arbitrary eigenvector sign.
        stubs = self._fred_stub_series()
        stubs["CDSP"] = -stubs["CDSP"]
        _install_fake_fredapi(monkeypatch, stubs)
        for j in range(16):
            _seed_metric("HHDC_FLOW30_ALL", self._qdate(j), self.V[j])

        assert ind.compute_cfsi() == 16

        mu = statistics.fmean(self.V)
        sd = statistics.pstdev(self.V)
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id='CFSI' ORDER BY date"
            ).fetchall()
        for j, r in enumerate(rows):
            expected = -(self.V[j] - mu) / sd
            assert r["value"] == pytest.approx(expected, abs=1e-3)

    def test_pc_orthogonal_to_dsr_falls_back_to_equal_weight(
        self, fresh_db, monkeypatch
    ):
        # If PC1 carries no DSR signal (cov == 0) its sign can't be oriented,
        # so compute_cfsi must treat the PCA as degenerate and use the
        # equal-weight path. Force the case by stubbing the PCA to return
        # all-zero scores (trivially orthogonal to the DSR z-series); with all
        # four inputs co-moving with V, equal weight = the z-score of V.
        _install_fake_fredapi(monkeypatch, self._fred_stub_series())
        monkeypatch.setattr(
            ind, "_pca_first_component", lambda zmatrix: [0.0] * len(zmatrix)
        )
        for j in range(16):
            _seed_metric("HHDC_FLOW30_ALL", self._qdate(j), self.V[j])

        assert ind.compute_cfsi() == 16

        mu = statistics.fmean(self.V)
        sd = statistics.pstdev(self.V)
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id='CFSI' ORDER BY date"
            ).fetchall()
        for j, r in enumerate(rows):
            expected = (self.V[j] - mu) / sd
            assert r["value"] == pytest.approx(expected, abs=1e-3)


# ─── _fit_ntfs_recession_prob ────────────────────────────────────────────────
class TestFitNtfsRecessionProb:
    # 121 NTFS months: the last one (2010-01) has no fully observed forward
    # window (USREC ends 2010-12), so it is excluded from the fit but still
    # receives a fitted probability.
    def _seed_ntfs(self, spread_for=lambda i: -1.0 if i in _SIGNAL_MONTHS else 1.0):
        for i in range(121):
            _seed_metric(ind._NTFS_ID, f"{_ym(i)}-01", spread_for(i))

    def _usrec_series(self, all_zero: bool = False) -> pd.Series:
        idx = pd.to_datetime([f"{_ym(i)}-01" for i in range(132)])
        vals = [
            0.0 if all_zero else (1.0 if i in _REC_MONTHS else 0.0)
            for i in range(132)
        ]
        return pd.Series(vals, index=idx)

    def test_returns_empty_without_fred_key(self, fresh_db, monkeypatch):
        self._seed_ntfs()
        monkeypatch.setattr(settings, "FRED_API_KEY", "")
        assert ind._fit_ntfs_recession_prob() == {}

    def test_returns_empty_when_usrec_fetch_fails(self, fresh_db, monkeypatch):
        self._seed_ntfs()
        _install_fake_fredapi(monkeypatch, {"USREC": RuntimeError("boom")})
        assert ind._fit_ntfs_recession_prob() == {}

    def test_returns_empty_when_one_target_class(self, fresh_db, monkeypatch):
        # USREC all zeros → no positive targets → probit not fit.
        self._seed_ntfs()
        _install_fake_fredapi(monkeypatch, {"USREC": self._usrec_series(all_zero=True)})
        assert ind._fit_ntfs_recession_prob() == {}

    def test_saturated_two_value_spread_yields_exact_group_probabilities(
        self, fresh_db, monkeypatch
    ):
        # spread = -1 for the 48 "signal" months (42 of which precede a
        # recession), +1 for the 72 calm months (4 of which do). A logit on a
        # two-valued feature is saturated: fitted P must equal the group means
        # 87.5% and 4/72 ≈ 5.5556% exactly (up to the 1e-6 ridge).
        self._seed_ntfs()
        _install_fake_fredapi(monkeypatch, {"USREC": self._usrec_series()})

        probs = ind._fit_ntfs_recession_prob()
        assert set(probs) == {_ym(i) for i in range(121)}

        assert probs["2000-01"] == pytest.approx(_P_SIGNAL, abs=0.05)  # signal (false alarm)
        assert probs["2002-05"] == pytest.approx(_P_SIGNAL, abs=0.05)  # signal (true)
        assert probs["2002-01"] == pytest.approx(_P_CALM, abs=0.05)    # miss → calm group
        assert probs["2004-04"] == pytest.approx(_P_CALM, abs=0.05)    # genuinely calm
        # The out-of-fit-window month is still scored from its spread (+1).
        assert probs["2010-01"] == pytest.approx(_P_CALM, abs=0.05)

        # USREC is cached back into metrics for the ensemble stack.
        with db.get_conn() as conn:
            n_usrec = conn.execute(
                "SELECT COUNT(*) AS n FROM metrics WHERE series_id='USREC'"
            ).fetchone()["n"]
        assert n_usrec == 132


# ─── _fit_ensemble_stack ─────────────────────────────────────────────────────
class TestFitEnsembleStack:
    @staticmethod
    def _component(hot_months=_SIGNAL_MONTHS, hi=80.0, lo=20.0) -> dict[str, float]:
        return {_ym(i): (hi if i in hot_months else lo) for i in range(120)}

    def test_insufficient_aligned_months_returns_none(self, fresh_db):
        _seed_usrec(36, all_zero=True)
        comp = {_ym(i): 50.0 for i in range(12)}  # 12 aligned months < 60
        assert ind._fit_ensemble_stack(comp, dict(comp), dict(comp)) is None

    def test_single_target_class_returns_none(self, fresh_db):
        _seed_usrec(all_zero=True)  # 132 months, never a recession
        comp = self._component()
        assert ind._fit_ensemble_stack(comp, dict(comp), dict(comp)) is None

    def test_anti_correlated_components_clamp_to_zero_and_fall_back(
        self, fresh_db
    ):
        # Components are HIGH exactly when no recession follows: unconstrained
        # slopes would be negative, the non-negativity projection clamps them
        # to 0, and the all-slopes-~0 guard rejects the stack (returns None).
        _seed_usrec()
        anti = self._component(hot_months=set(range(120)) - _POS_MONTHS)
        assert ind._fit_ensemble_stack(anti, dict(anti), dict(anti)) is None

    def test_non_finite_component_value_falls_back_to_none(self, fresh_db):
        # A NaN sneaking into a component probability poisons the Newton
        # iterations; _fit_logit reports a failed fit and the stack must
        # return None (→ equal-weight fallback) rather than NaN coefficients.
        _seed_usrec()
        comp = self._component()
        bad = dict(comp)
        bad["2000-01"] = float("nan")
        assert ind._fit_ensemble_stack(bad, dict(comp), dict(comp)) is None

    def test_saturated_fit_recovers_closed_form_coefficients(self, fresh_db):
        # All three features identical and two-valued (0.8 / 0.2 as fractions):
        # the stack is saturated, so on the logit scale
        #   z_signal = ln(42/6) = ln 7,  z_calm = ln(4/68)
        # total slope = (z_signal - z_calm) / 0.6 ≈ 7.9652, split equally by
        # symmetry → each ≈ 2.6551; b0 = z_calm - 0.2 · total ≈ -4.4263.
        _seed_usrec()
        comp = self._component()
        coefs = ind._fit_ensemble_stack(comp, dict(comp), dict(comp))
        assert coefs is not None
        b0, bN, bE, bT = coefs
        z_signal = math.log(42 / 6)
        z_calm = math.log(4 / 68)
        slope_total = (z_signal - z_calm) / 0.6
        for b in (bN, bE, bT):
            assert b == pytest.approx(slope_total / 3.0, abs=0.05)
        assert b0 == pytest.approx(z_calm - 0.2 * slope_total, abs=0.05)


# ─── compute_recession_ensemble: stacked path ────────────────────────────────
class TestRecessionEnsembleStacked:
    def test_stacked_headline_equals_saturated_group_means(
        self, fresh_db, monkeypatch
    ):
        # Seed USREC + NYFED + EBP probabilities into metrics and inject the
        # NTFS probability dict directly (bypasses the fredapi-backed fit).
        # All three components share the 80/20 signal pattern, so:
        #   • RECESSION_RISK_ENSEMBLE_EW is exactly 80.0 / 20.0, and
        #   • the stacked headline is the saturated 87.5% / 5.5556%.
        _seed_usrec()
        for i in range(120):
            hot = i in _SIGNAL_MONTHS
            _seed_metric(ind._RECESSION_PROBIT_ID, f"{_ym(i)}-15",
                         80.0 if hot else 20.0)
            _seed_metric("EBP_REC_PROB", f"{_ym(i)}-15", 80.0 if hot else 20.0)
        ntfs_prob = {
            _ym(i): (80.0 if i in _SIGNAL_MONTHS else 20.0) for i in range(120)
        }
        monkeypatch.setattr(ind, "_fit_ntfs_recession_prob", lambda: ntfs_prob)

        assert ind.compute_recession_ensemble() == 120

        def _series(sid: str) -> dict[str, float]:
            with db.get_conn() as conn:
                return {
                    r["date"]: r["value"]
                    for r in conn.execute(
                        "SELECT date, value FROM metrics WHERE series_id=?",
                        (sid,),
                    )
                }

        headline = _series(ind._ENSEMBLE_ID)
        ew = _series(ind._ENSEMBLE_EW_ID)
        ntfs_stored = _series(ind._NTFS_PROB_ID)
        assert len(headline) == len(ew) == len(ntfs_stored) == 120

        # The injected NTFS probabilities are persisted verbatim.
        assert ntfs_stored["2000-01-01"] == pytest.approx(80.0)
        assert ntfs_stored["2004-04-01"] == pytest.approx(20.0)

        # Equal-weight mean of three identical components is the component.
        assert ew["2000-01-01"] == pytest.approx(80.0)
        assert ew["2004-04-01"] == pytest.approx(20.0)

        # Stacked headline: saturated group probabilities.
        assert headline["2000-01-01"] == pytest.approx(_P_SIGNAL, abs=0.05)
        assert headline["2002-05-01"] == pytest.approx(_P_SIGNAL, abs=0.05)
        assert headline["2002-01-01"] == pytest.approx(_P_CALM, abs=0.05)
        assert headline["2004-04-01"] == pytest.approx(_P_CALM, abs=0.05)


# ─── compute_all_indicators ──────────────────────────────────────────────────
class TestComputeAllIndicators:
    def test_runs_every_stage_and_sums_to_zero_when_sources_unavailable(
        self, fresh_db, mocked_responses, monkeypatch
    ):
        # Every network source 500s and the DB is empty, so each stage returns
        # 0 — the orchestrator must still call all eight and sum to 0.
        # (FRED key blanked so CFSI/NTFS-probit never import fredapi.)
        monkeypatch.setattr(settings, "FRED_API_KEY", "")
        for url in (
            ind._EBP_CSV_URL, ind._GSW_URL, ind._OFR_FSI_URL, ind._BIS_CGAP_URL,
        ):
            mocked_responses.get(url, status=500)
        assert ind.compute_all_indicators() == 0
