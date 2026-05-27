"""
backend/data/indicators.py — computed / derived leading indicators.

These are *not* raw FRED pulls. They are constructed series (recession probits,
the Gilchrist-Zakrajšek Excess Bond Premium ingest) that we store in the same
`metrics` table as everything else, under synthetic series_ids and the
`recession_risk` / `credit` categories. Because they live in `metrics`, they
flow through the existing /fred/latest and /fred/history/{series_id} endpoints
for free.

Run order matters: compute_nyfed_recession_probit() reads T10Y3M out of the DB,
so fetch_fred_series() must have run first. The scheduler enforces this by
calling compute_all_indicators() at the tail of the FRED job.

See docs/INDICATORS.md for the research, formulas, sources, and caveats behind
each series.
"""

import csv
import io
import logging
import math
import re
import statistics
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

import requests

from config import settings
from cache.db import get_conn, upsert_metric

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_cdf(x: float) -> float:
    """Standard normal CDF Φ(x) via the stdlib error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ── 1. NY Fed yield-curve recession probit (Estrella-Mishkin) ────────────────
#
# P(recession within 12 months) = Φ(α + β · spread), where spread is the
# 10y-3m Treasury term spread in percentage points. These are the canonical
# Estrella & Mishkin (1996) coefficients estimated on ~1959-1995 data. The NY
# Fed's *currently published* series is re-estimated over 1959-2009 and
# distributed as allmonth.xls; the classic coefficients reproduce that model
# closely and need no .xls parsing. The 10y-3m spread (not 2s10s) is the input
# the NY Fed uses.
_EM_ALPHA = -0.5333
_EM_BETA = -0.6629
_TERM_SPREAD_SERIES = "T10Y3M"
_RECESSION_PROBIT_ID = "NYFED_RECESSION_PROB"


def compute_nyfed_recession_probit() -> int:
    """
    Build the NY Fed yield-curve recession probability from T10Y3M.

    The published model is monthly, so we average the daily term spread within
    each calendar month before applying the probit, and date the result to the
    last observed day of that month. The stored value is a probability in
    PERCENT (0-100), matching FRED's RECPROUSM156N so the two share an axis.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics "
            "WHERE series_id = ? AND value IS NOT NULL ORDER BY date",
            (_TERM_SPREAD_SERIES,),
        ).fetchall()

    if not rows:
        logger.warning(
            "recession probit: no %s in metrics yet — run FRED fetch first",
            _TERM_SPREAD_SERIES,
        )
        return 0

    # Monthly average of the daily spread; remember the last day seen per month.
    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    last_day: dict[str, str] = {}
    for r in rows:
        ym = r["date"][:7]  # YYYY-MM
        sums[ym] += float(r["value"])
        counts[ym] += 1
        last_day[ym] = r["date"]

    now = _now()
    count = 0
    for ym in sorted(sums):
        spread = sums[ym] / counts[ym]
        prob = _norm_cdf(_EM_ALPHA + _EM_BETA * spread) * 100.0
        upsert_metric(
            {
                "series_id": _RECESSION_PROBIT_ID,
                "label": "Recession Probability (NY Fed yield-curve probit)",
                "category": "recession_risk",
                "date": last_day[ym],
                "value": round(prob, 3),
                "fetched_at": now,
            }
        )
        count += 1

    logger.info("recession probit: stored %d monthly points", count)
    return count


# ── 2. Excess Bond Premium (Gilchrist-Zakrajšek) ─────────────────────────────
#
# The Fed staff publish gz_spread, ebp, and est_prob (the EBP+term-spread probit
# recession probability) in one CSV, updated monthly after 10:00 a.m. on the 4th
# business day. The predictive power of credit spreads for downturns is due
# entirely to the EBP component, not the raw spread level.
_EBP_CSV_URL = "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv"

# CSV column -> (series_id, label, category, scale). Probability columns are
# stored in PERCENT to match the recession-probit series; spreads as-is.
_EBP_COLUMNS = {
    "gz_spread": ("GZ_SPREAD", "GZ Credit Spread (Gilchrist-Zakrajšek)", "credit", 1.0),
    "ebp": ("EBP", "Excess Bond Premium (Gilchrist-Zakrajšek)", "credit", 1.0),
    "est_prob": (
        "EBP_REC_PROB",
        "Recession Probability (EBP + term-spread probit)",
        "recession_risk",
        100.0,
    ),
}


def fetch_excess_bond_premium() -> int:
    """Download the Fed EBP CSV and store gz_spread, ebp, and est_prob."""
    try:
        resp = requests.get(
            _EBP_CSV_URL,
            timeout=30,
            headers={"User-Agent": "SituationMonitor/0.1 (+macro dashboard)"},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("EBP fetch error: %s", e)
        return 0

    reader = csv.DictReader(io.StringIO(resp.text))
    if not reader.fieldnames:
        logger.error("EBP CSV: empty or unparseable response")
        return 0

    # Be tolerant of header whitespace/case drift.
    field_map = {f.strip().lower(): f for f in reader.fieldnames}
    date_field = field_map.get("date")
    if not date_field:
        logger.error("EBP CSV: no 'date' column in %s", reader.fieldnames)
        return 0

    now = _now()
    count = 0
    for raw in reader:
        date_raw = (raw.get(date_field) or "").strip()
        if not date_raw:
            continue
        # Dates arrive as YYYY-MM-DD (month-start); normalize defensively.
        try:
            date = datetime.fromisoformat(date_raw).date().isoformat()
        except ValueError:
            continue

        for col, (series_id, label, category, scale) in _EBP_COLUMNS.items():
            src = field_map.get(col)
            if not src:
                continue
            val = (raw.get(src) or "").strip()
            if val in ("", "NA", "NaN", "."):
                continue
            try:
                value = float(val) * scale
            except ValueError:
                continue
            upsert_metric(
                {
                    "series_id": series_id,
                    "label": label,
                    "category": category,
                    "date": date,
                    "value": round(value, 4),
                    "fetched_at": now,
                }
            )
            count += 1

    logger.info("EBP: stored %d points across %d series", count, len(_EBP_COLUMNS))
    return count


# ── 3. Near-Term Forward Spread (Engstrom-Sharpe) ────────────────────────────
#
# The Fed staff's *preferred* recession-curve measure: the market-implied 3-month
# Treasury yield six quarters (18 months) out, minus the current 3-month yield.
# When negative it signals the market expects near-term rate cuts — i.e. a
# downturn. Engstrom & Sharpe (2018) show it dominates 2s10s out-of-sample.
#
# We reconstruct it from the Fed Board's Gürkaynak-Sack-Wright (GSW) Svensson
# yield-curve parameters, which span sub-year maturities the published yield
# columns don't. Fully public, no key, no fragile sheet-scraping.
_GSW_URL = "https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv"
# GSW's 4-factor (Svensson) params start 1980; earlier rows lack TAU2/BETA3 and
# are skipped. The long history lets the recession-ensemble probit see real
# cycles (1981, 1990, 2001, 2008, 2020).
_GSW_START = "1980-01-01"
_NTFS_ID = "NEAR_TERM_FWD_SPREAD"


def _svensson_yield(m: float, b0, b1, b2, b3, t1, t2) -> float:
    """Svensson zero-coupon yield (percent, cont. comp.) at maturity m years."""
    def g(tau: float) -> float:
        x = m / tau
        return (1.0 - math.exp(-x)) / x

    return (
        b0
        + b1 * g(t1)
        + b2 * (g(t1) - math.exp(-m / t1))
        + b3 * (g(t2) - math.exp(-m / t2))
    )


def compute_near_term_forward_spread() -> int:
    """Fetch the GSW curve params and store the near-term forward spread."""
    try:
        resp = requests.get(
            _GSW_URL,
            timeout=90,
            headers={"User-Agent": "SituationMonitor/0.1 (+macro dashboard)"},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("NTFS/GSW fetch error: %s", e)
        return 0

    lines = resp.text.splitlines()
    try:
        hdr = next(i for i, ln in enumerate(lines) if ln.startswith("Date,"))
    except StopIteration:
        logger.error("NTFS: GSW header row not found")
        return 0

    reader = csv.DictReader(lines[hdr:])
    now = _now()
    count = 0
    for raw in reader:
        date = (raw.get("Date") or "").strip()
        if not date or date < _GSW_START:
            continue
        try:
            b0, b1, b2, b3 = (float(raw[k]) for k in ("BETA0", "BETA1", "BETA2", "BETA3"))
            t1, t2 = float(raw["TAU1"]), float(raw["TAU2"])
        except (KeyError, ValueError):
            continue  # early dates / NA rows lack a full 6-factor fit

        y = lambda m: _svensson_yield(m, b0, b1, b2, b3, t1, t2)
        # 3-month forward starting 1.5y out: (y₁.₇₅·1.75 − y₁.₅·1.5) / 0.25
        fwd_3m_18mo = (y(1.75) * 1.75 - y(1.50) * 1.50) / 0.25
        ntfs = fwd_3m_18mo - y(0.25)

        upsert_metric(
            {
                "series_id": _NTFS_ID,
                "label": "Near-Term Forward Spread (Engstrom-Sharpe)",
                "category": "rates",
                "date": date,
                "value": round(ntfs, 4),
                "fetched_at": now,
            }
        )
        count += 1

    logger.info("NTFS: stored %d daily points", count)
    return count


# ── 4. Credit Impulse ────────────────────────────────────────────────────────
#
# The change in the *flow* of new credit as a share of GDP — the second
# derivative of the credit stock. It leads real-economy momentum (GDP) by
# ~9-12 months. We use the year-over-year change in the annual credit flow,
# scaled by nominal GDP, to damp the quarterly noise a second difference adds:
#   flow_t   = TCMDO_t − TCMDO_{t-4}            (annual new credit)
#   impulse  = (flow_t − flow_{t-4}) / GDP_t × 100   (% of nominal SAAR GDP)
# TCMDO is in $millions, GDP in $billions, so the credit terms are /1000.
_CREDIT_IMPULSE_ID = "CREDIT_IMPULSE"
_TCMDO_TO_GDP_UNITS = 1000.0  # $millions → $billions


def compute_credit_impulse() -> int:
    """Build the credit impulse from TCMDO (credit stock) and nominal GDP."""
    with get_conn() as conn:
        def _series(sid: str) -> dict[str, float]:
            rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id = ? AND value IS NOT NULL ORDER BY date",
                (sid,),
            ).fetchall()
            return {r["date"]: float(r["value"]) for r in rows}

        credit = _series("TCMDO")
        gdp = _series("GDP")

    if not credit or not gdp:
        logger.warning("credit impulse: need TCMDO and GDP in metrics first")
        return 0

    dates = sorted(credit)  # quarter-start dates, ascending
    now = _now()
    count = 0
    # Need t, t-4, t-8 for the credit terms (and GDP at t).
    for i in range(8, len(dates)):
        d, d4, d8 = dates[i], dates[i - 4], dates[i - 8]
        if d not in gdp:
            continue
        flow_now = (credit[d] - credit[d4]) / _TCMDO_TO_GDP_UNITS
        flow_prior = (credit[d4] - credit[d8]) / _TCMDO_TO_GDP_UNITS
        impulse = (flow_now - flow_prior) / gdp[d] * 100.0
        upsert_metric(
            {
                "series_id": _CREDIT_IMPULSE_ID,
                "label": "Credit Impulse (% of GDP)",
                "category": "credit",
                "date": d,
                "value": round(impulse, 4),
                "fetched_at": now,
            }
        )
        count += 1

    logger.info("credit impulse: stored %d quarterly points", count)
    return count


# ── 5. Consumer Financial Stress Index (CFSI) ────────────────────────────────
#
# A single-needle composite of consumer credit stress built from four quarterly
# inputs, each oriented so higher = more stress:
#   • Consumer DSR (CDSP) — debt service as a share of income
#   • SLOOS credit-card tightening (DRTSCLCC), lagged 4Q — leads delinquency
#   • Flow into 30+ delinquency, all loans (HHDC_FLOW30_ALL) — the leading flow
#   • Real revolving-credit growth (REVOLSL/CPIAUCSL, YoY) — consumer leverage
#
# v2 changes vs. the original equal-weight / post-2015 build:
#   1. LONGER HISTORY — three of the four components (CDSP, DRTSCLCC, REVOLSL,
#      CPIAUCSL) are fetched DIRECTLY from FRED with observation_start=1999-01-01,
#      rather than read out of the `metrics` table (which is truncated to the
#      2015 generic-fetch start). HHDC_FLOW30_ALL stays from `metrics` (already
#      back to 2003Q1). The aligned common window then spans ~2004Q1+ and
#      *includes the GFC*, so the z-score baseline reflects a full stress cycle.
#   2. PCA WEIGHTING (default) — instead of an equal-weight z-score average, the
#      index is the first principal component of the four standardized inputs.
#      Each input is standardized (mean 0, sd 1); we eigendecompose the 4×4
#      correlation matrix (np.linalg.eigh), take the top eigenvector, and project
#      the standardized data onto it. The PC is sign-oriented so that higher =
#      more stress (we flip it if it correlates negatively with the DSR input)
#      and rescaled to unit standard deviation so the output stays in σ units.
#      PCA lets the data choose the weights — co-moving stress signals get more
#      weight than idiosyncratic noise. Falls back to equal-weight if PCA is
#      degenerate (e.g. a zero/near-singular eigenvector).
#
# If FRED_API_KEY is missing we fall back to the v1 behavior (read all four
# components from the 2015-truncated `metrics` table).
#
# Output is in standard deviations: 0 = full-sample (~2004+) average, +1 = 1σ
# more stress.
_CFSI_ID = "CFSI"
_CFSI_KEYS = ("dsr", "sloos", "flow", "revol")
# Direct-from-FRED components and how far back to fetch (HHDC stays from metrics).
_CFSI_FRED_START = "1999-01-01"
_CFSI_FRED_SERIES = ("CDSP", "DRTSCLCC", "REVOLSL", "CPIAUCSL")


def _quarter_shift(d: str, back: int) -> str:
    """Shift a 'YYYY-MM-01' quarter date back `back` quarters."""
    y, m, _ = d.split("-")
    total = int(y) * 12 + (int(m) - 1) - back * 3
    return f"{total // 12:04d}-{total % 12 + 1:02d}-01"


def _cfsi_fred_components() -> dict[str, dict[str, float]] | None:
    """Fetch CDSP/DRTSCLCC/REVOLSL/CPIAUCSL direct from FRED back to 1999.

    Returns {series_id: {YYYY-MM-01: value}} keyed to quarter-start dates, or
    None if FRED is unavailable (caller then falls back to the metrics table).
    """
    if not settings.FRED_API_KEY:
        return None
    try:
        from fredapi import Fred

        fred = Fred(api_key=settings.FRED_API_KEY)
        out: dict[str, dict[str, float]] = {}
        for sid in _CFSI_FRED_SERIES:
            s = fred.get_series(sid, observation_start=_CFSI_FRED_START).dropna()
            # Normalize each observation date to its quarter-start month so the
            # quarterly series (CDSP, DRTSCLCC) and monthly ones (REVOLSL,
            # CPIAUCSL) share a 'YYYY-MM-01' grid. Later obs win within a quarter.
            sm: dict[str, float] = {}
            for d, v in s.items():
                month = d.month
                qmonth = ((month - 1) // 3) * 3 + 1
                key = f"{d.year:04d}-{qmonth:02d}-01"
                sm[key] = float(v)
            out[sid] = sm
        return out
    except Exception as e:  # noqa: BLE001 — any FRED failure → fall back
        logger.warning("CFSI: direct FRED fetch failed (%s) — using metrics table", e)
        return None


def _pca_first_component(rows: list[list[float]]) -> list[float] | None:
    """First principal component score for each row of standardized inputs.

    `rows` is N observations × K standardized features (mean 0, sd 1 per column).
    Builds the K×K correlation matrix, eigendecomposes it (np.linalg.eigh, which
    returns ascending eigenvalues for a symmetric matrix), takes the top
    eigenvector, and projects each row onto it. Returns the projected scores
    (rescaled to unit sd), or None if the result is degenerate.
    """
    try:
        import numpy as np

        X = np.asarray(rows, float)
        if X.shape[0] < 4 or X.shape[1] < 2:
            return None
        # Columns are already standardized → covariance == correlation matrix.
        corr = np.cov(X, rowvar=False)
        if not np.all(np.isfinite(corr)):
            return None
        eigvals, eigvecs = np.linalg.eigh(corr)  # ascending eigenvalues
        top = eigvecs[:, -1]  # eigenvector of the largest eigenvalue
        scores = X @ top
        sd = float(scores.std())
        if not np.isfinite(sd) or sd < 1e-9:
            return None
        return list(scores / sd)
    except Exception as e:  # noqa: BLE001
        logger.warning("CFSI: PCA failed (%s) — using equal-weight", e)
        return None


def compute_cfsi() -> int:
    """Build the Consumer Financial Stress Index (v2). Returns rows written."""
    # HHDC flow always comes from metrics (already back to 2003Q1).
    with get_conn() as conn:
        def _series(sid: str) -> dict[str, float]:
            return {
                r["date"]: float(r["value"])
                for r in conn.execute(
                    "SELECT date, value FROM metrics "
                    "WHERE series_id = ? AND value IS NOT NULL",
                    (sid,),
                )
            }

        flow = _series("HHDC_FLOW30_ALL")

        # Prefer long-history FRED pulls; fall back to the 2015-truncated metrics.
        fred = _cfsi_fred_components()
        if fred is not None:
            cdsp = fred["CDSP"]
            sloos = fred["DRTSCLCC"]
            revol = fred["REVOLSL"]
            cpi = fred["CPIAUCSL"]
        else:
            cdsp = _series("CDSP")
            sloos = _series("DRTSCLCC")
            revol = _series("REVOLSL")
            cpi = _series("CPIAUCSL")

    if not (cdsp and sloos and flow and revol and cpi):
        logger.warning("CFSI: missing one or more component series — skipping")
        return 0

    # Real revolving credit, then YoY growth on the quarterly grid.
    real = {d: revol[d] / cpi[d] for d in revol if d in cpi and cpi[d]}
    revol_growth: dict[str, float] = {}
    for d in real:
        d4 = _quarter_shift(d, 4)
        if d4 in real and real[d4]:
            revol_growth[d] = (real[d] / real[d4] - 1.0) * 100.0

    # Assemble component values per quarter (SLOOS lagged 4 quarters).
    comps: dict[str, dict[str, float]] = {}
    for d in cdsp:
        d_lag = _quarter_shift(d, 4)
        if d in flow and d_lag in sloos and d in revol_growth:
            comps[d] = {
                "dsr": cdsp[d],
                "sloos": sloos[d_lag],
                "flow": flow[d],
                "revol": revol_growth[d],
            }

    if len(comps) < 8:
        logger.warning("CFSI: only %d aligned quarters — skipping", len(comps))
        return 0

    # Standardize each component over the common sample (all already higher = stress).
    moments: dict[str, tuple[float, float]] = {}
    for k in _CFSI_KEYS:
        vals = [comps[d][k] for d in comps]
        mu = statistics.fmean(vals)
        sd = statistics.pstdev(vals) or 1.0
        moments[k] = (mu, sd)

    ordered = sorted(comps)
    zmatrix = [
        [(comps[d][k] - moments[k][0]) / moments[k][1] for k in _CFSI_KEYS]
        for d in ordered
    ]

    # PCA-weighted index (default); fall back to equal-weight if degenerate.
    pca_scores = _pca_first_component(zmatrix)
    if pca_scores is not None:
        # Orient so higher = more stress: align the PC's sign with the consumer
        # DSR z-score (a monotone stress input). If the PC anti-correlates with
        # DSR, flip it.
        dsr_idx = _CFSI_KEYS.index("dsr")
        dsr_z = [row[dsr_idx] for row in zmatrix]
        cov = sum(p * z for p, z in zip(pca_scores, dsr_z))
        if cov < 0:
            pca_scores = [-p for p in pca_scores]
        values = pca_scores
        method = "PCA"
    else:
        values = [sum(row) / len(row) for row in zmatrix]
        method = "equal-weight"

    now = _now()
    count = 0
    for d, val in zip(ordered, values):
        upsert_metric(
            {
                "series_id": _CFSI_ID,
                "label": "Consumer Financial Stress Index (z-score)",
                "category": "recession_risk",
                "date": d,
                "value": round(val, 4),
                "fetched_at": now,
            }
        )
        count += 1

    logger.info("CFSI: stored %d quarterly points (%s, span %s..%s)",
                count, method, ordered[0], ordered[-1])
    return count


# ── 6. OFR Financial Stress Index ────────────────────────────────────────────
#
# A daily, global systemic-stress gauge from 33 market variables across five
# categories. Zero = average stress; positive = stressed. Documented to
# Granger-lead the CFNAI. Free CSV from the Office of Financial Research; not on
# FRED. We store the headline plus the five additive category contributions so
# the dashboard can show what's driving stress.
_OFR_FSI_URL = "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv"
_OFR_COLUMNS = {
    "OFR FSI": ("OFR_FSI", "OFR Financial Stress Index"),
    "Credit": ("OFR_FSI_CREDIT", "OFR FSI — Credit"),
    "Equity valuation": ("OFR_FSI_EQUITY", "OFR FSI — Equity Valuation"),
    "Safe assets": ("OFR_FSI_SAFE", "OFR FSI — Safe Assets"),
    "Funding": ("OFR_FSI_FUNDING", "OFR FSI — Funding"),
    "Volatility": ("OFR_FSI_VOL", "OFR FSI — Volatility"),
}


def fetch_ofr_fsi() -> int:
    """Download the OFR Financial Stress Index CSV and store its components."""
    try:
        resp = requests.get(
            _OFR_FSI_URL,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 SituationMonitor/0.1 (+macro dashboard)"},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("OFR FSI fetch error: %s", e)
        return 0

    reader = csv.DictReader(io.StringIO(resp.text))
    if not reader.fieldnames or "Date" not in reader.fieldnames:
        logger.error("OFR FSI: unexpected CSV header %s", reader.fieldnames)
        return 0

    now = _now()
    count = 0
    for row in reader:
        date = (row.get("Date") or "").strip()
        if not date:
            continue
        for col, (series_id, label) in _OFR_COLUMNS.items():
            val = (row.get(col) or "").strip()
            if val in ("", "NA", "."):
                continue
            try:
                value = float(val)
            except ValueError:
                continue
            upsert_metric(
                {
                    "series_id": series_id,
                    "label": label,
                    "category": "financial_conditions",
                    "date": date,
                    "value": round(value, 4),
                    "fetched_at": now,
                }
            )
            count += 1

    logger.info("OFR FSI: stored %d points across %d series", count, len(_OFR_COLUMNS))
    return count


# ── 7. BIS Credit-to-GDP Gap (Borio-Drehmann) ────────────────────────────────
#
# The best early-warning indicator of systemic banking crises at 2-5yr horizons
# (basis for the Basel III countercyclical capital buffer): the private-credit-
# to-GDP ratio minus its one-sided HP trend, in percentage points. >~10pp is the
# elevated-risk zone. We ingest BIS's *pre-computed* gap (it bakes in the HP
# filter) for the US private non-financial sector. Signals "too early" and has
# HP end-point instability — read it as a slow vulnerability gauge, not a timer.
_BIS_CGAP_URL = "https://data.bis.org/static/bulk/WS_CREDIT_GAP_csv_col.zip"
_BIS_CGAP_ID = "BIS_CREDIT_GAP_US"


def _bis_quarter_to_date(label: str) -> str | None:
    """'1990-Q1' -> '1990-01-01' (quarter start)."""
    m = re.match(r"(\d{4})-Q([1-4])", label.strip())
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    return f"{year:04d}-{(q - 1) * 3 + 1:02d}-01"


def fetch_bis_credit_gap() -> int:
    """Ingest the BIS US private credit-to-GDP gap (series Q:US:P:A:C)."""
    try:
        resp = requests.get(
            _BIS_CGAP_URL,
            timeout=60,
            headers={"User-Agent": "Mozilla/5.0 SituationMonitor/0.1 (+macro dashboard)"},
        )
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            text = zf.read(name).decode("utf-8", errors="ignore")
    except Exception as e:
        logger.error("BIS credit-gap fetch error: %s", e)
        return 0

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return 0
    header = rows[0]
    # Quarter columns are those matching YYYY-Qn; the rest are dimensions.
    qcols = {i: _bis_quarter_to_date(h) for i, h in enumerate(header) if re.match(r"\d{4}-Q[1-4]", h)}

    def _col(name: str) -> int | None:
        return header.index(name) if name in header else None

    ci_cty, ci_borr, ci_lend, ci_dtype = (
        _col("BORROWERS_CTY"), _col("TC_BORROWERS"), _col("TC_LENDERS"), _col("CG_DTYPE"),
    )
    if None in (ci_cty, ci_borr, ci_lend, ci_dtype):
        logger.error("BIS credit-gap: missing dimension columns")
        return 0

    now = _now()
    count = 0
    for row in rows[1:]:
        # US, private non-financial, all lenders, gap (actual-trend).
        if not (
            row[ci_cty] == "US" and row[ci_borr] == "P"
            and row[ci_lend] == "A" and row[ci_dtype] == "C"
        ):
            continue
        for i, date in qcols.items():
            if not date or i >= len(row):
                continue
            val = row[i].strip()
            if not val:
                continue
            try:
                value = float(val)
            except ValueError:
                continue
            upsert_metric(
                {
                    "series_id": _BIS_CGAP_ID,
                    "label": "BIS Credit-to-GDP Gap — US (pp)",
                    "category": "financial_conditions",
                    "date": date,
                    "value": round(value, 4),
                    "fetched_at": now,
                }
            )
            count += 1
        break  # only the one matching series

    logger.info("BIS credit-gap: stored %d quarterly points", count)
    return count


# ── 8. Recession-risk ensemble ───────────────────────────────────────────────
#
# A single headline 12-month recession probability blending three models:
#   • the NY Fed yield-curve probit (NYFED_RECESSION_PROB),
#   • the Excess Bond Premium probit (EBP_REC_PROB), and
#   • a Near-Term Forward Spread model fit here.
# NTFS is a spread, not a probability, so we fit a logit of "NBER recession
# within the next 12 months" on NTFS over 1980-present (≈5 cycles) — the
# Engstrom-Sharpe construction — and store the fitted probability (NTFS_REC_PROB).
# The ensemble is the simple mean of whichever components exist each month;
# if NTFS can't be fit (no key / too little history) it falls back to the two
# published probits. Equal-weight is deliberate — it's robust and transparent.
_ENSEMBLE_ID = "RECESSION_RISK_ENSEMBLE"
_NTFS_PROB_ID = "NTFS_REC_PROB"


def _ym_index(ym: str) -> int:
    """'YYYY-MM' -> month ordinal, for forward-window arithmetic."""
    y, m = ym.split("-")
    return int(y) * 12 + (int(m) - 1)


def _fit_logit(x, y):
    """Newton-Raphson logit of y on [1, x]. Returns (b0, b1) or None."""
    import numpy as np

    X = np.column_stack([np.ones(len(x)), np.asarray(x, float)])
    y = np.asarray(y, float)
    beta = np.zeros(2)
    for _ in range(50):
        p = 1.0 / (1.0 + np.exp(-(X @ beta)))
        w = np.clip(p * (1.0 - p), 1e-9, None)
        grad = X.T @ (y - p)
        hess = (X * w[:, None]).T @ X + 1e-6 * np.eye(2)
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            return None
        beta += step
        if np.max(np.abs(step)) < 1e-9:
            break
    return float(beta[0]), float(beta[1])


def _monthly_latest(series_id: str) -> dict[str, float]:
    """Read a metrics series and collapse to the latest value per calendar month."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics WHERE series_id = ? AND value IS NOT NULL "
            "ORDER BY date",
            (series_id,),
        ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        out[r["date"][:7]] = float(r["value"])  # later rows overwrite → latest wins
    return out


def _fit_ntfs_recession_prob() -> dict[str, float]:
    """Fit the NTFS recession logit against NBER USREC; return {ym: prob%}.

    Fetches USREC (NBER recession, monthly) with long history via FRED and caches
    it to metrics. Returns {} if NTFS/USREC history is insufficient to fit.
    """
    ntfs = _monthly_latest(_NTFS_ID)
    if len(ntfs) < 120:
        return {}

    # Pull NBER recession indicator with long history and cache it.
    if not settings.FRED_API_KEY:
        return {}
    try:
        from fredapi import Fred

        usrec_series = Fred(api_key=settings.FRED_API_KEY).get_series(
            "USREC", observation_start="1979-01-01"
        ).dropna()
    except Exception as e:
        logger.warning("ensemble: USREC fetch failed (%s) — NTFS probit skipped", e)
        return {}

    now = _now()
    usrec: dict[str, int] = {}
    for d, v in usrec_series.items():
        ym = str(d.date())[:7]
        usrec[ym] = int(v)
        upsert_metric(
            {
                "series_id": "USREC",
                "label": "NBER Recession Indicator",
                "category": "recession_risk",
                "date": str(d.date()),
                "value": float(v),
                "fetched_at": now,
            }
        )

    last_rec_idx = max(_ym_index(m) for m in usrec)
    # Target: recession in any of the next 12 months.
    fit_x, fit_y = [], []
    for ym, spread in ntfs.items():
        base = _ym_index(ym)
        if base + 12 > last_rec_idx:
            continue  # forward window not yet observed
        future = [usrec.get(f"{(base + k) // 12:04d}-{(base + k) % 12 + 1:02d}", 0) for k in range(1, 13)]
        fit_x.append(spread)
        fit_y.append(1.0 if any(future) else 0.0)

    if sum(fit_y) < 12 or (len(fit_y) - sum(fit_y)) < 12:
        return {}  # need both classes well represented
    coefs = _fit_logit(fit_x, fit_y)
    if coefs is None:
        return {}
    b0, b1 = coefs

    import math as _m

    return {ym: 100.0 / (1.0 + _m.exp(-(b0 + b1 * s))) for ym, s in ntfs.items()}


def compute_recession_ensemble() -> int:
    """Blend the yield-curve probit, EBP probit, and NTFS probit into one gauge."""
    nyfed = _monthly_latest(_RECESSION_PROBIT_ID)
    ebp = _monthly_latest("EBP_REC_PROB")
    ntfs_prob = _fit_ntfs_recession_prob()

    if not (nyfed or ebp or ntfs_prob):
        logger.warning("recession ensemble: no component probabilities available")
        return 0

    now = _now()
    count = 0

    # Persist the fitted NTFS probability as its own series for the panel.
    for ym, p in ntfs_prob.items():
        upsert_metric(
            {
                "series_id": _NTFS_PROB_ID,
                "label": "Recession Probability (NTFS logit)",
                "category": "recession_risk",
                "date": f"{ym}-01",
                "value": round(p, 3),
                "fetched_at": now,
            }
        )

    months = set(nyfed) | set(ebp) | set(ntfs_prob)
    for ym in sorted(months):
        comps = [d[ym] for d in (nyfed, ebp, ntfs_prob) if ym in d]
        if not comps:
            continue
        upsert_metric(
            {
                "series_id": _ENSEMBLE_ID,
                "label": "Recession Risk — Ensemble (12-mo)",
                "category": "recession_risk",
                "date": f"{ym}-01",
                "value": round(sum(comps) / len(comps), 3),
                "fetched_at": now,
            }
        )
        count += 1

    logger.info("recession ensemble: stored %d monthly points (NTFS probit: %s)",
                count, "yes" if ntfs_prob else "no")
    return count


def compute_all_indicators() -> int:
    """Run every derived indicator. Returns total rows written."""
    total = 0
    total += fetch_excess_bond_premium()
    total += compute_nyfed_recession_probit()
    total += compute_near_term_forward_spread()
    total += compute_credit_impulse()
    total += fetch_ofr_fsi()
    total += fetch_bis_credit_gap()
    total += compute_recession_ensemble()  # reads NTFS + both probits (above)
    total += compute_cfsi()  # depends on HHDC flows; recomputed in the HHDC job too
    return total
