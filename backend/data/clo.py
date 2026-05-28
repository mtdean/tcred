"""
backend/data/clo.py — CLO Stress Monitor (Phase 7).

Read-only computed analytics layered on existing ingest paths:
  - compute_clo_spread_proxy() builds a daily mezz-vs-AAA price ratio from CLO
    ETF prices already stored in `metrics` by the yfinance fetcher. Falling
    ratio = mezz selling off relative to AAA = CLO stress widening. JBBB/JAAA
    is the most liquid pair; CLOA/JBBB is included as a corroborating series.
  - get_clo_edgar_filings(limit) pulls CLO-tagged rows from `edgar_filings`,
    which the existing EDGAR fetcher tags via the `asset_class_keywords` list
    in data_sources.yaml (entries like "CLO", "collateralized loan obligation",
    "broadly syndicated", "middle market CLO").

No new ingest path — CLO ETF tickers and CLO keywords were added to
data_sources.yaml in the foundation commit so the existing market and EDGAR
fetchers pick them up automatically.
"""

import logging

from cache.db import get_conn

logger = logging.getLogger(__name__)

# Mezz / AAA pairs to compute. The first leg is AAA, the second is the mezz
# series whose price we divide by AAA. The most liquid AAA CLO ETF is JAAA;
# JBBB is the canonical BBB CLO ETF and the cleanest mezz read. CLOA is the
# BlackRock AAA ETF and provides a corroborating AAA leg.
_PAIRS: list[tuple[str, str]] = [
    ("mkt_JAAA", "mkt_JBBB"),
    ("mkt_CLOA", "mkt_JBBB"),
]

# Trailing window (trading days) — ~1 year.
_LOOKBACK_DAYS = 252


def compute_clo_spread_proxy() -> list[dict]:
    """Daily price-ratio time series for each (AAA, mezz) CLO ETF pair.

    For each pair, pull the last ~252 trading days of both series from
    `metrics`, join on date, and emit one row per common date with the mezz/AAA
    price ratio. Skips pairs where either leg has no data.
    """
    out: list[dict] = []
    with get_conn() as conn:
        for aaa_series, mezz_series in _PAIRS:
            aaa_rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id = ? AND value IS NOT NULL "
                "ORDER BY date DESC LIMIT ?",
                (aaa_series, _LOOKBACK_DAYS),
            ).fetchall()
            mezz_rows = conn.execute(
                "SELECT date, value FROM metrics "
                "WHERE series_id = ? AND value IS NOT NULL "
                "ORDER BY date DESC LIMIT ?",
                (mezz_series, _LOOKBACK_DAYS),
            ).fetchall()

            if not aaa_rows or not mezz_rows:
                logger.info(
                    "CLO spread proxy: skipping pair %s/%s (missing data)",
                    aaa_series, mezz_series,
                )
                continue

            aaa = {r["date"]: float(r["value"]) for r in aaa_rows}
            mezz = {r["date"]: float(r["value"]) for r in mezz_rows}
            common = sorted(set(aaa) & set(mezz))

            for d in common:
                a = aaa[d]
                m = mezz[d]
                ratio = m / a if a else None
                out.append(
                    {
                        "date": d,
                        "aaa_series": aaa_series,
                        "mezz_series": mezz_series,
                        "aaa_price": round(a, 4),
                        "mezz_price": round(m, 4),
                        "price_ratio": round(ratio, 6) if ratio is not None else None,
                    }
                )

    return out


def get_clo_edgar_filings(limit: int = 50) -> list[dict]:
    """Recent CLO-tagged EDGAR filings, ordered newest first.

    Matches the asset_class column (set by the EDGAR fetcher to the keyword
    that triggered the hit) against lowercase CLO substrings, and as a
    backstop searches the description for the same substrings. SQLite's LIKE
    is case-insensitive for ASCII, so lowercase patterns suffice.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT accession_no, company_name, form_type, filed_at,
                   description, url, asset_class
            FROM edgar_filings
            WHERE asset_class LIKE '%clo%'
               OR asset_class LIKE '%collateralized%'
               OR asset_class LIKE '%broadly syndicated%'
               OR description  LIKE '%clo%'
               OR description  LIKE '%collateralized loan%'
            ORDER BY filed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(r) for r in rows]
