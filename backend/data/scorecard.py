"""
backend/data/scorecard.py — narrative scorecards (consumer / SMB / leveraged).

One engine, three instances. Each scorecard is a list of indicator specs;
every indicator reports its latest value, a short-term trend, and a STRESS
PERCENTILE — where today sits inside the last 5 years of that series,
oriented so 100 = maximum stress. The composite is the mean of available
stress percentiles.

Spec fields:
  id           — metrics series_id (or synthetic id for derived series)
  label, unit
  orient       — +1: higher = more stress; -1: lower = more stress
  trend_obs    — observations back for the trend arrow (~3 months native freq)
  yoy_obs      — optional: transform to % change over N observations first
  scale        — optional multiplier (applied before yoy)
  spread_of    — optional [a, b]: series = a − b (e.g. CCC−B OAS, in bps via scale)
  ratio_of     — optional [a, b]: series = a / b (e.g. JBBB/JAAA price ratio)

Sources: `metrics` (FRED / SCE / SBA / NFIB / market mirrors / computed),
`trust_performance` (10-D actuals), `bdc_industry` (private credit marks).
Pure reads — cheap enough to compute per request.
"""

from __future__ import annotations

import re
from typing import Optional

from cache.db import get_conn

_SUBPRIME_AUTO = re.compile(
    r"santander|drive auto|exeter|westlake|americredit|dt auto|flagship|prestige",
    re.I,
)

# 16 keeps quarterly series (20 obs / 5y) eligible while still suppressing
# the few-month 10-D aggregates until they build real history.
_MIN_OBS_FOR_PCTL = 16


# ── Series plumbing ──────────────────────────────────────────────────────────

def _series(conn, series_id: str, limit: int = 1400) -> list[tuple[str, float]]:
    rows = conn.execute(
        "SELECT date, value FROM metrics WHERE series_id = ? AND value IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        (series_id, limit),
    ).fetchall()
    return [(r["date"], float(r["value"])) for r in reversed(rows)]


def _combine(a: list[tuple[str, float]], b: list[tuple[str, float]], op) -> list[tuple[str, float]]:
    """Date-aligned elementwise combination of two series."""
    bmap = dict(b)
    return [(d, op(v, bmap[d])) for d, v in a if d in bmap]


def _yoy(vals: list[tuple[str, float]], periods: int) -> list[tuple[str, float]]:
    out = []
    for i in range(periods, len(vals)):
        prior = vals[i - periods][1]
        if prior:
            out.append((vals[i][0], (vals[i][1] / prior - 1) * 100))
    return out


def _five_years(vals: list[tuple[str, float]]) -> list[float]:
    if not vals:
        return []
    cutoff = f"{int(vals[-1][0][:4]) - 5}{vals[-1][0][4:]}"
    return [v for d, v in vals if d >= cutoff]


def _stress_percentile(history: list[float], latest: float, orient: int) -> Optional[float]:
    if len(history) < _MIN_OBS_FOR_PCTL:
        return None
    below = sum(1 for v in history if v < latest)
    equal = sum(1 for v in history if v == latest)
    pctl = (below + equal / 2) / len(history) * 100
    return pctl if orient > 0 else 100 - pctl


def _trend(vals: list[tuple[str, float]], obs: int, orient: int) -> Optional[str]:
    """'worsening' | 'improving' | 'flat' vs `obs` observations ago."""
    if len(vals) <= obs:
        return None
    latest, prior = vals[-1][1], vals[-1 - obs][1]
    span = max(abs(prior), 1e-9)
    change = (latest - prior) / span
    if abs(change) < 0.01:
        return "flat"
    worse = (change > 0) == (orient > 0)
    return "worsening" if worse else "improving"


def _spec_values(conn, spec: dict) -> list[tuple[str, float]]:
    if spec.get("spread_of"):
        a, b = spec["spread_of"]
        vals = _combine(_series(conn, a), _series(conn, b), lambda x, y: x - y)
    elif spec.get("ratio_of"):
        a, b = spec["ratio_of"]
        vals = [
            (d, v) for d, v in _combine(
                _series(conn, a), _series(conn, b),
                lambda x, y: x / y if y else None,
            ) if v is not None
        ]
    else:
        vals = _series(conn, spec["id"])
    if spec.get("scale"):
        vals = [(d, v * spec["scale"]) for d, v in vals]
    if spec.get("yoy_obs"):
        vals = _yoy(vals, spec["yoy_obs"])
    return vals


def _build_indicators(conn, specs: list[dict], source: str = "fred") -> list[dict]:
    out = []
    for spec in specs:
        vals = _spec_values(conn, spec)
        if not vals:
            continue
        date, latest = vals[-1]
        out.append({
            "id": spec["id"],
            "label": spec["label"],
            "unit": spec["unit"],
            "value": latest,
            "date": date,
            "trend": _trend(vals, spec["trend_obs"], spec["orient"]),
            "stress_percentile": _stress_percentile(
                _five_years(vals), latest, spec["orient"]
            ),
            "source": spec.get("source", source),
        })
    return out


def _package(indicators: list[dict]) -> dict:
    scored = [i["stress_percentile"] for i in indicators if i["stress_percentile"] is not None]
    return {
        "composite_stress": sum(scored) / len(scored) if scored else None,
        "n_indicators": len(indicators),
        "n_scored": len(scored),
        "n_worsening": sum(1 for i in indicators if i["trend"] == "worsening"),
        "indicators": indicators,
    }


def _trust_aggregate(conn, segment: str, metric: str,
                     name_filter: Optional[re.Pattern] = None) -> list[tuple[str, float]]:
    """Cross-trust simple average of `metric` per period for one segment."""
    rows = conn.execute(
        "SELECT period_end, trust_name, value FROM trust_performance "
        "WHERE segment = ? AND metric = ? AND period_end != '' "
        "ORDER BY period_end",
        (segment, metric),
    ).fetchall()
    by_period: dict[str, list[float]] = {}
    for r in rows:
        if name_filter and not name_filter.search(r["trust_name"]):
            continue
        by_period.setdefault(r["period_end"], []).append(float(r["value"]))
    return [(p, sum(vs) / len(vs)) for p, vs in sorted(by_period.items()) if vs]


def _custom_indicator(indicator_id: str, label: str, unit: str,
                      vals: list[tuple[str, float]], orient: int,
                      trend_obs: int, source: str) -> Optional[dict]:
    if not vals:
        return None
    date, latest = vals[-1]
    return {
        "id": indicator_id,
        "label": label,
        "unit": unit,
        "value": latest,
        "date": date,
        "trend": _trend(vals, min(trend_obs, len(vals) - 1), orient)
                 if len(vals) > 1 else None,
        "stress_percentile": _stress_percentile(_five_years(vals), latest, orient),
        "source": source,
    }


# ── Consumer ─────────────────────────────────────────────────────────────────

_CONSUMER_SPECS: list[dict] = [
    {"id": "ICSA", "label": "Initial Jobless Claims", "unit": "k",
     "orient": +1, "trend_obs": 13, "scale": 0.001},
    {"id": "JTSQUR", "label": "Quits Rate (JOLTS)", "unit": "%",
     "orient": -1, "trend_obs": 3},
    {"id": "PSAVERT", "label": "Personal Saving Rate", "unit": "%",
     "orient": -1, "trend_obs": 3},
    {"id": "DSPIC96", "label": "Real Disp. Income YoY", "unit": "%",
     "orient": -1, "trend_obs": 3, "yoy_obs": 12},
    {"id": "UMCSENT", "label": "Consumer Sentiment", "unit": "",
     "orient": -1, "trend_obs": 3},
    {"id": "U6RATE", "label": "U-6 Underemployment", "unit": "%",
     "orient": +1, "trend_obs": 3},
    {"id": "DRCCLACBS", "label": "Card Delinquency (banks)", "unit": "%",
     "orient": +1, "trend_obs": 2},
    {"id": "CORCCACBS", "label": "Card Charge-offs (banks)", "unit": "%",
     "orient": +1, "trend_obs": 2},
    # Forward-looking: what households themselves expect (NY Fed SCE).
    {"id": "SCE_MISS_PAYMENT_PROB", "label": "Expect to Miss Payment (SCE)",
     "unit": "%", "orient": +1, "trend_obs": 3},
    {"id": "SCE_JOB_LOSS_PROB", "label": "Expect to Lose Job (SCE)",
     "unit": "%", "orient": +1, "trend_obs": 3},
    {"id": "SCE_REJECTION_RATE", "label": "Credit Rejection Rate (SCE)",
     "unit": "%", "orient": +1, "trend_obs": 1},
]


def compute_consumer_scorecard() -> dict:
    with get_conn() as conn:
        indicators = _build_indicators(conn, _CONSUMER_SPECS)
        # 10-D actuals: short history (percentile suppressed until it builds),
        # but the freshest consumer credit reads on the board.
        for label, segment, metric, name_filter in (
            ("Card Trust 30+ DLQ (10-D avg)", "credit_card", "delinq_30plus_rate", None),
            ("Card Trust Net Charge-off (10-D avg)", "credit_card", "net_charge_off_rate", None),
            ("Subprime Auto 30+ DLQ (10-D avg)", "auto", "delinq_30plus_rate", _SUBPRIME_AUTO),
        ):
            ind = _custom_indicator(
                f"trust_{segment}_{metric}", label, "%",
                _trust_aggregate(conn, segment, metric, name_filter),
                +1, 2, "10-D",
            )
            if ind:
                indicators.append(ind)
    return _package(indicators)


# ── Small business ───────────────────────────────────────────────────────────

_SMB_SPECS: list[dict] = [
    {"id": "DRTSCIS", "label": "SLOOS Tightening, Small Firms", "unit": "%",
     "orient": +1, "trend_obs": 1},
    {"id": "DRBLACBS", "label": "Business Loan Delinquency", "unit": "%",
     "orient": +1, "trend_obs": 2},
    {"id": "SBA_7A_DOLLARS", "label": "SBA 7(a) Approvals YoY", "unit": "%",
     "orient": -1, "trend_obs": 3, "yoy_obs": 12, "source": "sba"},
    {"id": "TOTCI", "label": "Bank C&I Loans YoY (H.8)", "unit": "%",
     "orient": -1, "trend_obs": 13, "yoy_obs": 52},
    {"id": "TEMPHELPS", "label": "Temp-Help Employment YoY", "unit": "%",
     "orient": -1, "trend_obs": 3, "yoy_obs": 12},
    {"id": "NFIB_OPTIMISM", "label": "NFIB Small Business Optimism", "unit": "",
     "orient": -1, "trend_obs": 3, "source": "nfib"},
    {"id": "NFIB_LOAN_AVAILABILITY", "label": "NFIB Loan Availability (net)", "unit": "%",
     "orient": -1, "trend_obs": 3, "source": "nfib"},
]


def compute_smb_scorecard() -> dict:
    with get_conn() as conn:
        return _package(_build_indicators(conn, _SMB_SPECS))


# ── Leveraged credit / CLO ───────────────────────────────────────────────────

_LEVERAGED_SPECS: list[dict] = [
    {"id": "BAMLH0A0HYM2", "label": "HY OAS", "unit": "bp",
     "orient": +1, "trend_obs": 63, "scale": 100},
    {"id": "HY_CCC_B_BASIS", "label": "CCC − B Basis", "unit": "bp",
     "orient": +1, "trend_obs": 63, "scale": 100,
     "spread_of": ["BAMLH0A3HYC", "BAMLH0A2HYB"]},
    {"id": "HY_B_BB_BASIS", "label": "B − BB Basis", "unit": "bp",
     "orient": +1, "trend_obs": 63, "scale": 100,
     "spread_of": ["BAMLH0A2HYB", "BAMLH0A1HYBB"]},
    {"id": "EBP", "label": "Excess Bond Premium", "unit": "",
     "orient": +1, "trend_obs": 3},
    {"id": "CLO_MEZZ_RATIO", "label": "CLO Mezz Proxy (JBBB/JAAA)", "unit": "",
     "orient": -1, "trend_obs": 63, "ratio_of": ["mkt_JBBB", "mkt_JAAA"],
     "source": "market"},
    {"id": "mkt_BKLN", "label": "Lev-Loan ETF 3M Return (BKLN)", "unit": "%",
     "orient": -1, "trend_obs": 21, "yoy_obs": 63, "source": "market"},
]


def compute_leveraged_scorecard() -> dict:
    with get_conn() as conn:
        indicators = _build_indicators(conn, _LEVERAGED_SPECS)
        # Private credit marks from the BDC sector layer — quarterly
        # NAV-weighted portfolio mark across all reporting BDCs.
        rows = conn.execute(
            """
            SELECT period, SUM(fair_value) AS fv, SUM(cost_basis) AS cost
            FROM bdc_industry
            WHERE cost_basis > 0 AND fair_value > 0
            GROUP BY period HAVING COUNT(DISTINCT cik) >= 5
            ORDER BY period
            """
        ).fetchall()
        marks = [(r["period"], r["fv"] / r["cost"]) for r in rows if r["cost"]]
        ind = _custom_indicator(
            "bdc_portfolio_mark", "Private Credit Marks (BDC agg)", "",
            marks, -1, 1, "bdc",
        )
        if ind:
            indicators.append(ind)
    return _package(indicators)
