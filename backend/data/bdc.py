"""
backend/data/bdc.py — SEC BDC Bulk Dataset ingestion.

Downloads the SEC's monthly BDC XBRL bulk dataset ZIP, parses SOI.tsv
(Schedule of Investments), computes per-BDC summary metrics, and persists
both per-holding rows and roll-ups.

Source: https://www.sec.gov/data-research/sec-markets-data/bdc-data-sets
No API key required. Token-free. SEC requires User-Agent identification.
"""

import hashlib
import io
import logging
import re
import zipfile
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests

from cache.db import get_conn
from config import load_data_sources, settings

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": settings.EDGAR_USER_AGENT}

# SEC publishes ZIPs at /files/bdc/*.zip; the index page links to them.
BDC_DATA_INDEX = "https://www.sec.gov/data-research/sec-markets-data/bdc-data-sets"
BDC_ZIP_URL_PATTERN = "https://www.sec.gov/files/bdc/bdc_{year}q{quarter}.zip"


def _get_latest_bdc_zip_url() -> Optional[str]:
    """Scrape the index page for the most recent ZIP link, falling back to the
    current calendar quarter if scraping fails."""
    try:
        resp = requests.get(BDC_DATA_INDEX, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        matches = re.findall(r'href="(/files/bdc/[^"]+\.zip)"', resp.text)
        if matches:
            # The index lists newest-last; take the trailing entry.
            return f"https://www.sec.gov{matches[-1]}"
    except Exception as e:
        logger.error(f"BDC ZIP URL discovery error: {e}")
    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    return BDC_ZIP_URL_PATTERN.format(year=now.year, quarter=q)


def _download_and_parse_soi(zip_url: str) -> Optional[pd.DataFrame]:
    """Download ZIP, locate SOI.tsv inside, return as a string-typed DataFrame."""
    try:
        logger.info(f"Downloading BDC dataset from {zip_url}")
        resp = requests.get(zip_url, headers=HEADERS, timeout=120)
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            soi_files = [
                name for name in z.namelist()
                if "soi" in name.lower() and name.endswith(".tsv")
            ]
            if not soi_files:
                logger.error("SOI.tsv not found in BDC ZIP")
                return None

            with z.open(soi_files[0]) as f:
                # dtype=str avoids pandas inferring numerics on dirty XBRL values;
                # we cast explicitly per column below.
                df = pd.read_csv(f, sep="\t", low_memory=False, dtype=str)

        logger.info(f"BDC SOI loaded: {len(df)} rows, {len(df.columns)} columns")
        return df

    except Exception as e:
        logger.error(f"BDC download/parse error: {e}")
        return None


def _safe_float(val) -> Optional[float]:
    try:
        if val is None:
            return None
        s = str(val).strip()
        if s in ("", "nan", "None", "NaN"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _compute_bdc_summary(
    holdings: list[dict],
    bdc_name: str,
    cik: str,
    period: str,
) -> dict:
    """Roll holdings up to BDC-level summary. Returns {} for empty input."""
    if not holdings:
        return {}

    total_fv = sum(h["fair_value"] or 0 for h in holdings)
    total_cost = sum(h["cost_basis"] or 0 for h in holdings)
    nonaccrual = [h for h in holdings if h["is_nonaccrual"]]
    nonaccrual_fv = sum(h["fair_value"] or 0 for h in nonaccrual)
    nonaccrual_cost = sum(h["cost_basis"] or 0 for h in nonaccrual)

    by_type: dict[str, float] = {}
    for h in holdings:
        t = (h.get("investment_type") or "Other").lower()
        by_type[t] = by_type.get(t, 0) + (h["fair_value"] or 0)

    # Weighted average coupon, weighted by fair value.
    wa_rate_num = sum(
        (h["interest_rate"] or 0) * (h["fair_value"] or 0)
        for h in holdings if h["interest_rate"]
    )
    wa_rate = wa_rate_num / total_fv if total_fv else None

    row_id = hashlib.sha256(f"{cik}_{period}".encode()).hexdigest()[:16]

    return {
        "id": row_id,
        "cik": cik,
        "bdc_name": bdc_name,
        "period": period,
        "total_fair_value": total_fv,
        "total_cost_basis": total_cost,
        "nonaccrual_fv": nonaccrual_fv,
        "nonaccrual_cost": nonaccrual_cost,
        "nonaccrual_rate_fv": nonaccrual_fv / total_fv if total_fv else None,
        "nonaccrual_rate_cost": nonaccrual_cost / total_cost if total_cost else None,
        "pct_first_lien": by_type.get("first lien", 0) / total_fv if total_fv else None,
        "pct_second_lien": by_type.get("second lien", 0) / total_fv if total_fv else None,
        "pct_equity": by_type.get("equity", 0) / total_fv if total_fv else None,
        "wa_interest_rate": wa_rate,
        "mark_to_cost": total_fv / total_cost if total_cost else None,
        "n_holdings": len(holdings),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_bdc_data() -> int:
    """Pipeline: discover URL → download → parse → persist holdings + summaries.
    Returns total holdings inserted."""
    zip_url = _get_latest_bdc_zip_url()
    if not zip_url:
        logger.error("Could not determine BDC ZIP URL")
        return 0

    df = _download_and_parse_soi(zip_url)
    if df is None or df.empty:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    stored = 0

    df.columns = [c.strip() for c in df.columns]

    # SEC dataset has varied between camelCase and lowercase across vintages.
    col_map = {
        "adsh":       ["adsh", "Adsh"],
        "cik":        ["cik", "CIK"],
        "name":       ["name", "Name"],
        "period":     ["period", "Period"],
        "company":    ["InvestmentIdentifier", "investmentidentifier", "company"],
        "industry":   ["InvestmentIndustry", "investmentindustry"],
        "inv_type":   ["InvestmentTypeAxis", "investmenttypeaxis"],
        "rate":       ["InvestmentInterestRate", "investmentinterestrate"],
        "pik":        ["InvestmentPIKRate", "investmentpikrate"],
        "cost":       ["InvestmentCostBasis", "investmentcostbasis"],
        "fv":         ["InvestmentFairValue", "investmentfairvalue"],
        "pct_nav":    ["InvestmentPercentOfNetAssets", "investmentpercentofnetassets"],
        "maturity":   ["InvestmentMaturityDate", "investmentmaturitydate"],
        "nonaccrual": ["InvestmentIsOnNonaccrualStatus", "investmentisonnonaccrualstatus"],
    }

    def get_col(key: str) -> Optional[str]:
        for candidate in col_map.get(key, []):
            if candidate in df.columns:
                return candidate
        return None

    cik_col = get_col("cik")
    name_col = get_col("name")
    period_col = get_col("period")

    if not all([cik_col, period_col]):
        logger.error(
            f"Required BDC columns not found. Available: {list(df.columns)[:20]}"
        )
        return 0

    # Log a sample of BDCs we're processing so the watch list stays maintainable.
    if cik_col and name_col:
        unique_bdcs = df[[cik_col, name_col]].drop_duplicates()
        logger.info(f"BDCs in dataset: {len(unique_bdcs)}")
        for _, row in unique_bdcs.head(20).iterrows():
            logger.info(f"  CIK={row[cik_col]} NAME={row[name_col]}")

    group_cols = [c for c in [cik_col, name_col, period_col] if c]
    bdc_groups = df.groupby(group_cols) if group_cols else []

    with get_conn() as conn:
        for group_key, group_df in bdc_groups:
            if isinstance(group_key, str):
                group_key = (group_key,)

            cik = group_key[0] if len(group_key) > 0 else ""
            bdc_name = group_key[1] if len(group_key) > 1 else ""
            period = group_key[2] if len(group_key) > 2 else ""

            holdings: list[dict] = []
            for _, row in group_df.iterrows():
                inv_id = str(row.get(get_col("company"), "") or "")
                row_id = hashlib.sha256(
                    f"{row.get(get_col('adsh'), '')}_{inv_id}".encode()
                ).hexdigest()[:16]

                is_nonaccrual_raw = str(
                    row.get(get_col("nonaccrual"), "") or ""
                ).lower()
                is_nonaccrual = is_nonaccrual_raw in ("true", "1", "yes")

                h = {
                    "id": row_id,
                    "adsh": str(row.get(get_col("adsh"), "") or ""),
                    "cik": str(cik),
                    "bdc_name": str(bdc_name),
                    "period": str(period),
                    "company_name": inv_id[:200],
                    "industry": str(row.get(get_col("industry"), "") or "")[:100],
                    "investment_type": str(row.get(get_col("inv_type"), "") or "")[:100],
                    "interest_rate": _safe_float(row.get(get_col("rate"))),
                    "pik_rate": _safe_float(row.get(get_col("pik"))),
                    "cost_basis": _safe_float(row.get(get_col("cost"))),
                    "fair_value": _safe_float(row.get(get_col("fv"))),
                    "fair_value_pct_nav": _safe_float(row.get(get_col("pct_nav"))),
                    "maturity_date": str(row.get(get_col("maturity"), "") or "")[:20],
                    "is_nonaccrual": 1 if is_nonaccrual else 0,
                    "fetched_at": now,
                }
                holdings.append(h)

                try:
                    cols = ", ".join(h.keys())
                    placeholders = ", ".join(f":{k}" for k in h.keys())
                    conn.execute(
                        f"INSERT OR IGNORE INTO bdc_holdings ({cols}) VALUES ({placeholders})",
                        h,
                    )
                    stored += 1
                except Exception as e:
                    logger.debug(f"BDC holding insert skip: {e}")

            summary = _compute_bdc_summary(holdings, str(bdc_name), str(cik), str(period))
            if summary:
                try:
                    cols = ", ".join(summary.keys())
                    placeholders = ", ".join(f":{k}" for k in summary.keys())
                    conn.execute(
                        f"INSERT OR REPLACE INTO bdc_summary ({cols}) VALUES ({placeholders})",
                        summary,
                    )
                except Exception as e:
                    logger.debug(f"BDC summary insert skip: {e}")

    logger.info(f"BDC data stored: {stored} holdings")
    return stored


def get_watch_list() -> list[dict]:
    """Curated watch list from config/data_sources.yaml. Foundation-provided."""
    cfg = load_data_sources()
    return cfg.get("bdc", {}).get("watch_list", [])


def get_bdc_nonaccrual_trend() -> list[dict]:
    """Aggregate non-accrual rate across all BDCs by reporting period.
    Core private-credit stress indicator."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                period,
                COUNT(DISTINCT cik)         AS n_bdcs,
                SUM(nonaccrual_fv)          AS total_nonaccrual_fv,
                SUM(total_fair_value)       AS total_fv,
                AVG(nonaccrual_rate_fv)     AS avg_nonaccrual_rate,
                AVG(mark_to_cost)           AS avg_mark_to_cost,
                AVG(wa_interest_rate)       AS avg_wa_rate
            FROM bdc_summary
            GROUP BY period
            ORDER BY period ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_bdc_summary(period: Optional[str] = None) -> list[dict]:
    """Per-BDC roll-up — given period or latest if omitted, sorted by NAV desc."""
    with get_conn() as conn:
        if period:
            rows = conn.execute(
                "SELECT * FROM bdc_summary WHERE period = ? ORDER BY total_fair_value DESC",
                (period,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM bdc_summary
                WHERE period = (SELECT MAX(period) FROM bdc_summary)
                ORDER BY total_fair_value DESC
                """
            ).fetchall()
    return [dict(r) for r in rows]


def get_bdc_nonaccruals(limit: int = 100) -> list[dict]:
    """Individual non-accrual holdings across all BDCs for the latest period."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT bdc_name, company_name, industry, investment_type,
                   cost_basis, fair_value, period
            FROM bdc_holdings
            WHERE is_nonaccrual = 1
              AND period = (SELECT MAX(period) FROM bdc_holdings)
            ORDER BY cost_basis DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
