"""
backend/data/h8.py — Fed H.8 Bank Credit Monitor (Phase 7).

Foundation stub. The h8-credit-monitor worktree implements:
  - compute_h8_metrics():     for each H.8 series in `metrics`, compute WoW
                               change, WoW %, 4-week MA, YoY %, and emit a
                               103-week history slice
  - compute_credit_impulse(): quarterly TOTLL changes / GDP, annualized %

Raw H.8 series are ingested by the existing FRED fetcher (see data_sources.yaml,
`h8_bank_credit` category). This module is read-only computed analytics — no
new ingest path required.
"""


def compute_h8_metrics() -> list[dict]:
    return []


def compute_credit_impulse() -> list[dict]:
    return []
