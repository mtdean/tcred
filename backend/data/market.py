"""
backend/data/market.py — market data via yfinance.

Fetches ETF prices + basic stats for the configured tickers.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from config import load_data_sources
from cache.db import upsert_metric, get_conn

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_market_data() -> int:
    """Fetch all configured tickers. Returns count of rows stored."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — run: pip install yfinance")
        return 0

    cfg = load_data_sources()
    tickers_cfg = cfg.get("market_tickers", [])
    now = _now()
    count = 0

    tickers = [t["ticker"] for t in tickers_cfg]
    ticker_meta = {t["ticker"]: t for t in tickers_cfg}

    try:
        data = yf.download(
            tickers,
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        logger.error(f"yfinance batch download error: {e}")
        return 0

    for ticker in tickers:
        meta = ticker_meta[ticker]
        label = meta["label"]
        category = meta["category"]

        try:
            if len(tickers) == 1:
                series = data["Close"]
            else:
                series = data[ticker]["Close"]

            series = series.dropna()

            for date, price in series.items():
                upsert_metric(
                    {
                        "series_id": f"mkt_{ticker}",
                        "label": label,
                        "category": category,
                        "date": str(date.date()),
                        "value": float(price),
                        "fetched_at": now,
                    }
                )
                count += 1

        except Exception as e:
            logger.warning(f"[{ticker}] processing error: {e}")

    logger.info(f"Market data fetch complete — {count} rows stored")
    return count


def get_market_snapshot() -> list[dict]:
    """
    Returns latest price + 1d/5d/30d change for each ticker.
    """
    cfg = load_data_sources()
    tickers_cfg = cfg.get("market_tickers", [])
    results = []

    with get_conn() as conn:
        for t in tickers_cfg:
            series_id = f"mkt_{t['ticker']}"
            rows = conn.execute(
                """SELECT date, value FROM metrics
                   WHERE series_id = ?
                   ORDER BY date DESC LIMIT 35""",
                (series_id,),
            ).fetchall()

            if not rows:
                continue

            prices = [dict(r) for r in rows]
            latest = prices[0]
            price_1d = prices[1]["value"] if len(prices) > 1 else None
            price_5d = prices[5]["value"] if len(prices) > 5 else None
            price_30d = prices[30]["value"] if len(prices) > 30 else None

            def pct_chg(current, prior) -> Optional[float]:
                if prior and prior != 0:
                    return round((current - prior) / prior * 100, 2)
                return None

            results.append(
                {
                    "ticker": t["ticker"],
                    "label": t["label"],
                    "category": t["category"],
                    "price": round(latest["value"], 2),
                    "date": latest["date"],
                    "chg_1d": pct_chg(latest["value"], price_1d),
                    "chg_5d": pct_chg(latest["value"], price_5d),
                    "chg_30d": pct_chg(latest["value"], price_30d),
                }
            )

    return results
