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

# SEC publishes BDC dataset ZIPs under
# /files/structureddata/data/business-development-company-bdc-data-sets/.
# Historical files are quarterly (e.g. 2024q3_bdc.zip); from 2025_04 onward the
# release cadence switched to monthly (e.g. 2026_04_bdc.zip).
BDC_DATA_INDEX = "https://www.sec.gov/data-research/sec-markets-data/bdc-data-sets"
BDC_BASE_PATH = (
    "/files/structureddata/data/business-development-company-bdc-data-sets"
)


def _sort_key(path: str) -> tuple:
    """Sort BDC zip filenames so monthly releases (YYYY_MM_bdc.zip) outrank
    quarterly ones for the same year, with most-recent winning overall."""
    name = path.rsplit("/", 1)[-1]
    m = re.match(r"^(\d{4})_(\d{2})_bdc\.zip$", name)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return (year, month, 1)  # monthly wins ties
    q = re.match(r"^(\d{4})q([1-4])_bdc\.zip$", name)
    if q:
        year, qtr = int(q.group(1)), int(q.group(2))
        return (year, qtr * 3, 0)  # map quarter to its last month
    return (0, 0, 0)


def _get_latest_bdc_zip_url() -> Optional[str]:
    """Scrape the index page for the most recent ZIP link.

    SEC publishes both quarterly archives and (since 2025-04) monthly releases.
    We accept either filename pattern and return the most-recent file.
    """
    try:
        resp = requests.get(BDC_DATA_INDEX, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        matches = re.findall(
            rf'href="({re.escape(BDC_BASE_PATH)}/[^"]+\.zip)"', resp.text
        )
        if matches:
            latest = max(matches, key=_sort_key)
            return f"https://www.sec.gov{latest}"
    except Exception as e:
        logger.error(f"BDC ZIP URL discovery error: {e}")
    return None


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


# SOI.tsv is reported as XBRL facts — the SAME total NAV gets repeated under
# each independent breakdown (by industry, by issuer, by fair-value hierarchy,
# by valuation technique, etc.). Naïvely summing every row over-counts 5-10x.
#
# Rollup axes are pure totals (e.g. "Level 1/2/3", "Income Approach", "Operating
# Segments") — we exclude rows where any of them is set. Breakdown axes are
# disjoint partitions of the portfolio — we keep rows that have exactly one
# breakdown axis populated and then pick, per filer, the breakdown axis whose
# rows sum to the SMALLEST positive total cost (the least double-counted
# representation of the BDC's portfolio).
_ROLLUP_AXES = [
    "Fair Value Hierarchy and NAV Axis",
    "Valuation Approach and Technique Axis",
    "Consolidation Items Axis",
    "Investment Company, Nonconsolidated Subsidiary Axis",
    "Segments Axis",
]
_BREAKDOWN_AXES = [
    "Investment, Identifier Axis",
    "Investment, Issuer Name Axis",
    "Investment, Issuer Affiliation Axis",
    "Industry Sector Axis",
    "Investment Type Axis",
    "Financial Instrument Axis",
]


def _norm(s) -> str:
    """NaN-safe string strip + [Member] suffix removal (XBRL Member taxonomy)."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return str(s).replace("[Member]", "").strip()


def _pick_primary_breakdown(group_df: pd.DataFrame, fv_col: str, cost_col: str) -> str | None:
    """For one BDC's rows, return the breakdown axis whose rows sum to the
    smallest positive total cost — the cleanest non-double-counted view of the
    portfolio. Returns None if no axis yields any usable rows."""
    best_axis: str | None = None
    best_total: float | None = None
    for axis in _BREAKDOWN_AXES:
        if axis not in group_df.columns:
            continue
        rows = group_df[group_df[axis].map(_norm) != ""]
        if rows.empty:
            continue
        cost_sum = pd.to_numeric(rows[cost_col], errors="coerce").fillna(0).sum()
        if cost_sum <= 0:
            continue  # affiliate-axis edge case where cost is zeroed out
        if best_total is None or cost_sum < best_total:
            best_axis, best_total = axis, cost_sum
    return best_axis


def fetch_bdc_data() -> int:
    """Pipeline: discover URL → download → parse → persist holdings + summaries.
    Returns total holdings inserted.

    The SOI.tsv schema does NOT match PHASE7.md's spec. It's a sparse fact table
    in XBRL long-form, with each total repeated under multiple breakdown axes.
    See _pick_primary_breakdown for the de-duplication strategy.
    """
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

    COL_ADSH     = "adsh"
    COL_CIK      = "cik"
    COL_NAME     = "name"
    COL_DDATE    = "ddate"
    COL_PERIOD   = "period"
    COL_INDUSTRY = "Industry Sector Axis"
    COL_INV_TYPE = "Investment Type Axis"
    COL_RATE     = "Investment Interest Rate"
    COL_PIK      = "Investment, Interest Rate, Paid in Kind"
    COL_COST     = "Adjusted cost basis"
    COL_FV       = "Initial fair value of Investment"
    COL_PCT_NAV  = "Investment Owned, Net Assets, Percentage"
    COL_MATURITY = "Investment Maturity Date"

    required = [COL_ADSH, COL_CIK, COL_NAME, COL_DDATE, COL_PERIOD, COL_COST, COL_FV]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.error(
            f"BDC SOI missing expected columns: {missing}. "
            f"Available: {list(df.columns)[:20]}"
        )
        return 0

    # Current-period observations only. A 10-Q includes prior comparative
    # balances under the same adsh but with ddate set to the prior quarter.
    df = df[df[COL_DDATE] == df[COL_PERIOD]].copy()

    # Need cost OR fair value to be useful.
    df["_cost_num"] = pd.to_numeric(df[COL_COST], errors="coerce")
    df["_fv_num"] = pd.to_numeric(df[COL_FV], errors="coerce")
    df = df[df[["_cost_num", "_fv_num"]].notna().any(axis=1)]

    # Drop rows that are rollup totals (any rollup-axis member set).
    for axis in _ROLLUP_AXES:
        if axis in df.columns:
            df = df[df[axis].map(_norm) == ""]

    if df.empty:
        logger.warning("BDC SOI produced 0 usable holdings after rollup filter")
        return 0

    # Each row should have exactly one breakdown axis populated; multi-axis
    # rows are sub-partitions and re-introduce double counting.
    present_axes = [a for a in _BREAKDOWN_AXES if a in df.columns]
    df["_n_breakdowns"] = (
        df[present_axes].map(_norm).ne("").sum(axis=1)
    )
    df = df[df["_n_breakdowns"] == 1]

    unique_bdcs = df[[COL_CIK, COL_NAME]].drop_duplicates()
    logger.info(f"BDC dataset: {len(df)} candidate rows across {len(unique_bdcs)} BDCs")

    def _get(row, col: str) -> str:
        return _norm(row.get(col) if col in df.columns else "")

    with get_conn() as conn:
        for (cik, bdc_name, period), group_df in df.groupby(
            [COL_CIK, COL_NAME, COL_PERIOD]
        ):
            primary = _pick_primary_breakdown(group_df, COL_FV, COL_COST)
            if primary is None:
                logger.debug(f"BDC {bdc_name}: no usable breakdown axis")
                continue

            sub = group_df[group_df[primary].map(_norm) != ""]
            holdings: list[dict] = []
            for idx, row in sub.iterrows():
                inv_id = _get(row, primary)
                inv_type = _get(row, COL_INV_TYPE)
                industry = _get(row, COL_INDUSTRY)
                row_id = hashlib.sha256(
                    f"{_get(row, COL_ADSH)}|{primary}|{inv_id}|{idx}".encode()
                ).hexdigest()[:16]

                h = {
                    "id": row_id,
                    "adsh": _get(row, COL_ADSH),
                    "cik": str(cik),
                    "bdc_name": str(bdc_name),
                    "period": str(period),
                    "company_name": inv_id[:200],
                    "industry": industry[:100],
                    "investment_type": inv_type[:100],
                    "interest_rate": _safe_float(_get(row, COL_RATE)),
                    "pik_rate": _safe_float(_get(row, COL_PIK)),
                    "cost_basis": _safe_float(_get(row, COL_COST)),
                    "fair_value": _safe_float(_get(row, COL_FV)),
                    "fair_value_pct_nav": _safe_float(_get(row, COL_PCT_NAV)),
                    "maturity_date": _get(row, COL_MATURITY)[:20],
                    # Non-accrual is not reliably tagged as a typed XBRL fact in
                    # SOI.tsv — most BDCs disclose status in footnote text. Left
                    # as 0 until a text-extraction path is wired.
                    "is_nonaccrual": 0,
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
