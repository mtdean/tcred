"""
backend/data/scorecard.py — Consumer Health Scorecard.

One narrative-ready table: ~10 consumer indicators, each with its latest
value, short-term trend, and a STRESS PERCENTILE — where today sits inside
the last 5 years of that series, oriented so 100 = maximum stress. The
composite is the mean of available stress percentiles (indicators with too
little history contribute trend only).

Sources: `metrics` (FRED + computed) and `trust_performance` (10-D card /
auto actuals). Pure reads — no network, cheap enough to compute per request.
"""

from __future__ import annotations

import re
from typing import Optional

from cache.db import get_conn

# orientation: +1 → higher value = more stress; -1 → lower value = more stress.
# trend_obs: how many observations back the trend arrow compares against
# (≈3 months for the series' native frequency).
_METRIC_INDICATORS: list[dict] = [
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
]

_SUBPRIME_AUTO = re.compile(
    r"santander|drive auto|exeter|westlake|americredit|dt auto|flagship|prestige",
    re.I,
)

# 16 keeps quarterly series (20 obs / 5y) eligible while still suppressing
# the 5-month 10-D aggregates until they build real history.
_MIN_OBS_FOR_PCTL = 16


def _series(conn, series_id: str, limit: int = 400) -> list[tuple[str, float]]:
    rows = conn.execute(
        "SELECT date, value FROM metrics WHERE series_id = ? AND value IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        (series_id, limit),
    ).fetchall()
    return [(r["date"], float(r["value"])) for r in reversed(rows)]


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


def compute_consumer_scorecard() -> dict:
    indicators: list[dict] = []
    with get_conn() as conn:
        for spec in _METRIC_INDICATORS:
            vals = _series(conn, spec["id"])
            if spec.get("yoy_obs"):
                vals = _yoy(vals, spec["yoy_obs"])
            if spec.get("scale"):
                vals = [(d, v * spec["scale"]) for d, v in vals]
            if not vals:
                continue
            date, latest = vals[-1]
            hist5 = _five_years(vals)
            indicators.append({
                "id": spec["id"],
                "label": spec["label"],
                "unit": spec["unit"],
                "value": latest,
                "date": date,
                "trend": _trend(vals, spec["trend_obs"], spec["orient"]),
                "stress_percentile": _stress_percentile(hist5, latest, spec["orient"]),
                "source": "fred",
            })

        # 10-D actuals: short history (percentile suppressed until ≥24 obs),
        # but the freshest consumer credit reads on the board.
        for label, segment, metric, name_filter in (
            ("Card Trust 30+ DLQ (10-D avg)", "credit_card", "delinq_30plus_rate", None),
            ("Card Trust Net Charge-off (10-D avg)", "credit_card", "net_charge_off_rate", None),
            ("Subprime Auto 30+ DLQ (10-D avg)", "auto", "delinq_30plus_rate", _SUBPRIME_AUTO),
        ):
            vals = _trust_aggregate(conn, segment, metric, name_filter)
            if not vals:
                continue
            date, latest = vals[-1]
            indicators.append({
                "id": f"trust_{segment}_{metric}",
                "label": label,
                "unit": "%",
                "value": latest,
                "date": date,
                "trend": _trend(vals, min(2, len(vals) - 1), +1) if len(vals) > 1 else None,
                "stress_percentile": _stress_percentile(
                    [v for _, v in vals], latest, +1
                ),
                "source": "10-D",
            })

    scored = [i["stress_percentile"] for i in indicators if i["stress_percentile"] is not None]
    composite = sum(scored) / len(scored) if scored else None
    worsening = sum(1 for i in indicators if i["trend"] == "worsening")
    return {
        "composite_stress": composite,
        "n_indicators": len(indicators),
        "n_scored": len(scored),
        "n_worsening": worsening,
        "indicators": indicators,
    }
