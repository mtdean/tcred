"""
backend/data/bdc.py — SEC BDC Portfolio Monitor (Phase 7).

Foundation stub. The bdc-portfolio-monitor worktree implements:
  - fetch_bdc_data():            download bulk dataset ZIP, parse SOI.tsv,
                                  compute per-BDC summaries, persist
  - get_watch_list():            curated CIK/ticker/name list from yaml
  - get_bdc_nonaccrual_trend():  aggregate non-accrual rate by period
  - get_bdc_summary(period):     per-BDC roll-up
  - get_bdc_nonaccruals(limit):  individual non-accrual holdings

Until implemented, endpoints return empty data so the rest of the app boots cleanly.
"""

from typing import Optional


def fetch_bdc_data() -> int:
    return 0


def get_watch_list() -> list[dict]:
    from config import load_data_sources
    cfg = load_data_sources()
    return cfg.get("bdc", {}).get("watch_list", [])


def get_bdc_nonaccrual_trend() -> list[dict]:
    return []


def get_bdc_summary(period: Optional[str] = None) -> list[dict]:
    return []


def get_bdc_nonaccruals(limit: int = 100) -> list[dict]:
    return []
