"""
backend/data/clo.py — CLO Stress Monitor (Phase 7).

Foundation stub. The clo-stress-monitor worktree implements:
  - compute_clo_spread_proxy(): daily JBBB/JAAA price-ratio time series
                                 (stress when ratio falls). ETF prices come
                                 from the existing yfinance fetcher.
  - get_clo_edgar_filings(limit): CLO-tagged filings from edgar_filings table
                                    (matched via asset_class_keywords).

Read-only computed analytics — no new ingest path. CLO ETF tickers are added to
data_sources.yaml so the existing market fetcher picks them up. CLO asset_class
keywords likewise added so existing EDGAR fetcher tags relevant filings.
"""


def compute_clo_spread_proxy() -> list[dict]:
    return []


def get_clo_edgar_filings(limit: int = 50) -> list[dict]:
    return []
