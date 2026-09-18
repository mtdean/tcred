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

# SEC publishes BDC dataset ZIPs linked from the index page below. The
# directory has moved before (structureddata → datastandardsinnovation in
# 2026), so discovery matches any /files/…_bdc.zip link rather than pinning
# the path. Historical files are quarterly (e.g. 2024q3_bdc.zip); from
# 2025_04 onward the release cadence switched to monthly (e.g. 2026_04_bdc.zip).
BDC_DATA_INDEX = "https://www.sec.gov/data-research/sec-markets-data/bdc-data-sets"
BDC_ZIP_HREF_RE = r'href="(/files/[^"]+_bdc\.zip)"'


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
        matches = re.findall(BDC_ZIP_HREF_RE, resp.text)
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
    adsh: Optional[str] = None,
    filed: Optional[str] = None,
) -> dict:
    """Roll holdings up to BDC-level summary. Returns {} for empty input.

    `adsh` / `filed` identify the source filing (most-recent for this period).
    They're used by the API to build a canonical EDGAR filing-index URL.
    """
    if not holdings:
        return {}

    total_fv = sum(h["fair_value"] or 0 for h in holdings)
    total_cost = sum(h["cost_basis"] or 0 for h in holdings)
    nonaccrual = [h for h in holdings if h["is_nonaccrual"]]
    nonaccrual_fv = sum(h["fair_value"] or 0 for h in nonaccrual)
    nonaccrual_cost = sum(h["cost_basis"] or 0 for h in nonaccrual)

    # Lien classification: BDCs encode the lien position in different columns
    # depending on their breakdown axis. Some put it in Investment Type Axis
    # ("Senior Secured Loans, First Lien"); most put it inline in the
    # identifier ("Acme Corp, First lien term loan"). Scan both fields.
    # `tagged_fv` tracks holdings we *could* classify — if it's 0 the entire
    # BDC's lien mix is unknown (not "0% first lien"), so we return NULL.
    by_lien: dict[str, float] = {"first": 0.0, "second": 0.0, "equity": 0.0}
    tagged_fv = 0.0
    for h in holdings:
        blob = (
            (h.get("investment_type") or "")
            + " | "
            + (h.get("company_name") or "")
        ).lower()
        fv = h["fair_value"] or 0
        if "first lien" in blob or "1st lien" in blob or "first-lien" in blob:
            by_lien["first"] += fv
            tagged_fv += fv
        elif "second lien" in blob or "2nd lien" in blob or "second-lien" in blob:
            by_lien["second"] += fv
            tagged_fv += fv
        elif (
            "common stock" in blob
            or "preferred stock" in blob
            or "common shares" in blob
            or "preferred shares" in blob
            or "warrant" in blob
            or "equity interest" in blob
        ):
            by_lien["equity"] += fv
            tagged_fv += fv

    # Weighted average yield over INCOME-GENERATING holdings only.
    # Including the equity/warrant FV in the denominator dilutes the rate
    # toward zero and misrepresents what the debt portfolio is earning.
    wa_rate_num = sum(
        (h["interest_rate"] or 0) * (h["fair_value"] or 0)
        for h in holdings if h["interest_rate"]
    )
    rate_bearing_fv = sum(
        h["fair_value"] or 0 for h in holdings if h["interest_rate"]
    )
    wa_rate = wa_rate_num / rate_bearing_fv if rate_bearing_fv else None

    row_id = hashlib.sha256(f"{cik}_{period}".encode()).hexdigest()[:16]

    return {
        "id": row_id,
        "cik": cik,
        "bdc_name": bdc_name,
        "period": period,
        "adsh": adsh,
        "filed": filed,
        "total_fair_value": total_fv,
        "total_cost_basis": total_cost,
        "nonaccrual_fv": nonaccrual_fv,
        "nonaccrual_cost": nonaccrual_cost,
        "nonaccrual_rate_fv": nonaccrual_fv / total_fv if total_fv else None,
        "nonaccrual_rate_cost": nonaccrual_cost / total_cost if total_cost else None,
        # When nothing classified, the BDC's lien mix is unknown (NULL),
        # not "0% in every bucket". Largest BDCs (BPCF / ARCC) hit this path.
        "pct_first_lien":
            by_lien["first"] / total_fv if (total_fv and tagged_fv) else None,
        "pct_second_lien":
            by_lien["second"] / total_fv if (total_fv and tagged_fv) else None,
        "pct_equity":
            by_lien["equity"] / total_fv if (total_fv and tagged_fv) else None,
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


# ── Sector normalization ─────────────────────────────────────────────────────
# BDCs free-type their Industry Sector Axis members ("Software", "Software
# Sector", "Application Software", "Health Care Technology"...). Keyword rules
# collapse them into ~12 canonical buckets so cross-BDC aggregation works.
# ORDER MATTERS: first match wins — healthcare before tech so "Health Care
# Technology" lands in Healthcare (mirrors GICS), structured/funds before
# financials so "Structured Note" doesn't land in Financials.
SECTOR_RULES: list[tuple[str, list[str]]] = [
    ("Funds & Structured", [
        "structured", "collateralized", "clo", "asset backed", "asset-backed",
        "joint venture", "investment fund", "funds", "fund ", " fund",
        "investment vehicle", "multi sector", "multi-sector",
        "government securit", "treasur",
    ]),
    ("Healthcare", [
        "health", "pharma", "biotech", "medical", "life science", "hospital",
    ]),
    ("Software & Tech", [
        "software", "information technology", "it service", "itservice",
        "itconsulting", "technology", "internet", "semiconduct", "computer",
        "digital", "electronic", "hardware", "cyber", "high tech", "saas",
    ]),
    ("Media & Telecom", [
        "media", "telecom", "entertainment", "broadcast", "cable",
        "publishing", "wireless", "communication", "satellite",
    ]),
    ("Financials & Insurance", [
        "financ", "insurance", "bank", "lending", "asset management",
        "capital market", "consumer credit", "mortgage", "leasing",
        "thrifts", "credit services",
    ]),
    ("Business Services", [
        "professional service", "business service", "commercial service",
        "diversified support", "human resource", "staffing", "consulting",
        "services business", "services: business", "services, business",
        "facilities service", "office service", "security service",
        "environmental", "research and consulting", "conglomerate service",
    ]),
    ("Real Estate", [
        "real estate", "reit", "property management",
    ]),
    ("Energy & Power", [
        "energy", "oil", "gas", "power", "utilit", "renewable", "pipeline",
        "coal", "solar", "electricity",
    ]),
    ("Transportation", [
        "transport", "logistics", "airline", "airport", "marine", "shipping",
        "freight", "rail", "trucking", "cargo",
    ]),
    ("Chemicals & Materials", [
        "chemical", "packaging", "container", "paper", "metal", "mining",
        "forest", "glass", "steel", "cement", "materials",
    ]),
    ("Consumer & Retail", [
        "consumer", "retail", "food", "beverage", "restaurant",
        "personal care", "household", "apparel", "leisure", "hotel", "gaming",
        "education", "textile", "luxury", "grocery", "distribut",
        "wholesale", "e-commerce", "recreation", "personal product",
        "cannabis",
    ]),
    ("Industrials", [
        "industrial", "machinery", "building product", "construction",
        "aerospace", "defense", "auto", "capital good", "manufactur",
        "electrical equipment", "trading compan", "engineering",
        "infrastructure", "equipment",
    ]),
]

SECTOR_OTHER = "Other"

# Some filers abuse the Industry Sector Axis for things that aren't industries:
# portfolio totals, lien/instrument types, geography, or individual issuer
# names. These rows are skipped entirely (not bucketed into Other) so they
# don't distort sector shares.
_JUNK_INDUSTRY_SUBSTRINGS = [
    "total", "lien", "secured debt", "unsecured debt", "senior loan",
    "term loan", "delayed draw", "revolver", "unitranche", "geographic",
    "subordinated note", "senior note",
]
_ISSUER_SUFFIX_RE = re.compile(
    r"(,?\s(llc|l\.p\.|lp|l\.l\.c\.|inc\.?|corp\.?|ltd\.?|co\.)|"
    r"(holdings|acquisitionco|midco|topco|bidco|buyer|parent))\s*$",
    re.IGNORECASE,
)


def is_junk_industry(raw: str) -> bool:
    """True when an Industry Sector Axis member isn't actually an industry."""
    blob = _norm(raw).lower()
    if not blob or blob in ("industry", "other", "sector"):
        # bare "Other"/"industry" members are legit catch-alls — keep them
        return blob in ("industry", "sector")
    if any(kw in blob for kw in _JUNK_INDUSTRY_SUBSTRINGS):
        return True
    if _ISSUER_SUFFIX_RE.search(blob):
        return True
    return False


def normalize_sector(raw: str) -> str:
    """Map a raw Industry Sector Axis member string to a canonical sector."""
    blob = _norm(raw).lower()
    if not blob:
        return SECTOR_OTHER
    # camelCase XBRL member names arrive squashed ("HealthcareSector") —
    # also match with spaces stripped from the keyword.
    squashed = blob.replace(" ", "")
    for sector, keywords in SECTOR_RULES:
        for kw in keywords:
            if kw in blob or kw.replace(" ", "") in squashed:
                return sector
    return SECTOR_OTHER


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


def _extract_industry_breakdown(df: pd.DataFrame, conn, now: str) -> int:
    """Persist per-BDC industry-sector totals into bdc_industry.

    Input: the SOI frame AFTER rollup-row filtering and numeric prep
    (_cost_num/_fv_num present), BEFORE the exactly-one-breakdown-axis filter —
    industry disclosures often ride multi-axis rows (industry × investment
    type × affiliation) that the holdings pipeline rightly drops.

    Per (cik, ddate): among rows with Industry Sector Axis set, pick the
    axis-combination whose fair-value sum is LARGEST — the most complete
    partition of the portfolio. (Opposite of _pick_primary_breakdown's
    smallest-total heuristic: there we dedupe repeated totals, here sparser
    combos are usually partial disclosures — e.g. BPCF's industry×type combo
    sums to $6B of an $84B book while its affiliation×industry×type combo
    covers $74B.) Rows within one combo are distinct member tuples, so
    summing them by industry cannot double count.
    """
    IND = "Industry Sector Axis"
    if IND not in df.columns:
        return 0

    ind = df[df[IND].map(_norm) != ""].copy()
    ind = ind[ind[["_cost_num", "_fv_num"]].notna().any(axis=1)]
    if ind.empty:
        return 0

    present_axes = [a for a in _BREAKDOWN_AXES if a in ind.columns]
    axis_flags = ind[present_axes].map(_norm).ne("")
    ind["_combo"] = axis_flags.apply(
        lambda r: "+".join(a for a, v in zip(present_axes, r) if v), axis=1
    )

    stored = 0
    for (cik, bdc_name, ddate), group in ind.groupby(["cik", "name", "ddate"]):
        # Most-recently-filed view wins when several filings cover this ddate.
        if "filed" in group.columns:
            newest = group["filed"].map(_norm).max()
            if newest:
                group = group[group["filed"].map(_norm) == newest]

        # Pick the combo with the largest FV coverage (cost as tiebreak
        # when FV is entirely missing).
        best_combo, best_total = None, 0.0
        for combo, sub in group.groupby("_combo"):
            total = sub["_fv_num"].fillna(0).sum()
            if total <= 0:
                total = sub["_cost_num"].fillna(0).sum()
            if total > best_total:
                best_combo, best_total = combo, total
        if best_combo is None:
            continue

        sub = group[group["_combo"] == best_combo]
        agg = sub.groupby(sub[IND].map(_norm)).agg(
            cost=("_cost_num", "sum"), fv=("_fv_num", "sum")
        )

        # Sanity guard: some filers ship wrong-scale XBRL values (e.g. Runway
        # Growth's 2025-06 SOI reports $86B cost against $87mm FV on a ~$1B
        # book). A whole-portfolio mark outside [0.3, 3.0] is a data error,
        # not a credit event — skip the BDC-period entirely.
        tot_cost = agg["cost"].fillna(0).sum()
        tot_fv = agg["fv"].fillna(0).sum()
        if tot_cost > 0 and tot_fv > 0 and not (0.3 <= tot_fv / tot_cost <= 3.0):
            logger.debug(
                f"BDC industry skip {bdc_name} @ {ddate}: implausible "
                f"portfolio mark {tot_fv / tot_cost:.3f}"
            )
            continue
        for industry_raw, row in agg.iterrows():
            if not industry_raw or is_junk_industry(industry_raw):
                continue
            rec = {
                "id": hashlib.sha256(
                    f"{cik}|{ddate}|{industry_raw}".encode()
                ).hexdigest()[:16],
                "cik": str(cik),
                "bdc_name": str(bdc_name),
                "period": str(ddate),
                "industry_raw": industry_raw[:120],
                "sector": normalize_sector(industry_raw),
                "cost_basis": float(row["cost"]) if pd.notna(row["cost"]) else None,
                "fair_value": float(row["fv"]) if pd.notna(row["fv"]) else None,
                "fetched_at": now,
            }
            try:
                cols = ", ".join(rec.keys())
                placeholders = ", ".join(f":{k}" for k in rec.keys())
                conn.execute(
                    f"INSERT OR REPLACE INTO bdc_industry ({cols}) VALUES ({placeholders})",
                    rec,
                )
                stored += 1
            except Exception as e:
                logger.debug(f"BDC industry insert skip: {e}")
    return stored


def _ingest_dataframe(df: pd.DataFrame, source: str = "") -> int:
    """Clean SOI.tsv DataFrame, dedupe across XBRL breakdown axes, persist
    holdings + per-(cik, ddate) summaries. Returns total holdings inserted.

    Extracted from fetch_bdc_data so both single-zip fetch and multi-zip
    backfill share one pipeline. `source` is used only for log messages.

    The SOI.tsv schema does NOT match PHASE7.md's spec. It's a sparse fact
    table in XBRL long-form, with each total repeated under multiple
    breakdown axes. See _pick_primary_breakdown for the de-duplication strategy.
    """
    if df is None or df.empty:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    tag = f" [{source}]" if source else ""

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
    COL_PCT_NAV  = "Investment Owned, Net Assets, Percentage"
    COL_MATURITY = "Investment Maturity Date"

    # Cost / fair-value column names changed across SOI vintages. Pre-2025 ZIPs
    # only have "Investment Owned, Cost" / "...Fair Value". The 2026_04 ZIP
    # carries both old and new names; we prefer the new XBRL tags where
    # present and fall back to the legacy names otherwise.
    def _pick_col(*candidates: str) -> Optional[str]:
        for c in candidates:
            if c in df.columns:
                return c
        return None

    COL_COST = _pick_col("Adjusted cost basis", "Investment Owned, Cost")
    COL_FV   = _pick_col("Initial fair value of Investment", "Investment Owned, Fair Value")

    required = [COL_ADSH, COL_CIK, COL_NAME, COL_DDATE, COL_PERIOD]
    missing = [c for c in required if c not in df.columns]
    if missing or COL_COST is None or COL_FV is None:
        logger.error(
            f"BDC SOI{tag} missing expected columns: "
            f"required={missing} cost={COL_COST} fv={COL_FV}. "
            f"Available: {list(df.columns)[:20]}"
        )
        return 0

    # Keep ddates within ~1 year of each filing's reporting period — this
    # captures the current observation plus the prior comparative (10-Q
    # carries Q-1, 10-K carries Y-1). Drops ancient comparatives (10-K Year-2
    # / Year-3 columns) that would otherwise inflate the trend with stale data.
    df["_period_dt"] = pd.to_datetime(df[COL_PERIOD], errors="coerce")
    df["_ddate_dt"] = pd.to_datetime(df[COL_DDATE], errors="coerce")
    df = df[
        df["_ddate_dt"].notna()
        & df["_period_dt"].notna()
        & ((df["_period_dt"] - df["_ddate_dt"]).dt.days <= 380)
        & ((df["_period_dt"] - df["_ddate_dt"]).dt.days >= 0)
    ]

    # Need cost OR fair value to be useful.
    df["_cost_num"] = pd.to_numeric(df[COL_COST], errors="coerce")
    df["_fv_num"] = pd.to_numeric(df[COL_FV], errors="coerce")
    df = df[df[["_cost_num", "_fv_num"]].notna().any(axis=1)]

    # Drop rows that are rollup totals (any rollup-axis member set).
    for axis in _ROLLUP_AXES:
        if axis in df.columns:
            df = df[df[axis].map(_norm) == ""]

    if df.empty:
        logger.warning(f"BDC SOI{tag} produced 0 usable holdings after rollup filter")
        return 0

    # Industry-sector breakdown BEFORE the single-axis filter (industry
    # disclosures often ride multi-axis rows the holdings pipeline drops).
    with get_conn() as conn:
        n_ind = _extract_industry_breakdown(df, conn, now)
    logger.info(f"BDC industry breakdown{tag}: {n_ind} sector rows stored")

    # Each row should have exactly one breakdown axis populated; multi-axis
    # rows are sub-partitions and re-introduce double counting.
    present_axes = [a for a in _BREAKDOWN_AXES if a in df.columns]
    df["_n_breakdowns"] = (
        df[present_axes].map(_norm).ne("").sum(axis=1)
    )
    df = df[df["_n_breakdowns"] == 1]

    # If the same (cik, ddate) appears in multiple filings (e.g. a 10-K's
    # Year-1 comparative also shows up as the next year's 10-K Year-2), keep
    # the most-recently-filed view. Need a stable key per row to dedupe; we
    # dedupe by (cik, ddate, primary_axis_member) using `filed` for recency.
    sort_cols = ["filed"] if "filed" in df.columns else [COL_ADSH]
    df = df.sort_values(sort_cols, ascending=False).copy()

    unique_bdcs = df[[COL_CIK, COL_NAME]].drop_duplicates()
    logger.info(f"BDC dataset{tag}: {len(df)} candidate rows across {len(unique_bdcs)} BDCs")

    def _get(row, col: str) -> str:
        return _norm(row.get(col) if col in df.columns else "")

    # Group by ddate (the observation date), not the filing's reporting period.
    # This way ARES's Q1-filing prior-comparative rows roll up under Q4 2025,
    # giving the trend chart history even when only one BDC has filed Q1.
    with get_conn() as conn:
        for (cik, bdc_name, ddate), group_df in df.groupby(
            [COL_CIK, COL_NAME, COL_DDATE]
        ):
            primary = _pick_primary_breakdown(group_df, COL_FV, COL_COST)
            if primary is None:
                logger.debug(f"BDC {bdc_name} @ {ddate}: no usable breakdown axis")
                continue

            sub = group_df[group_df[primary].map(_norm) != ""]
            holdings: list[dict] = []
            for idx, row in sub.iterrows():
                inv_id = _get(row, primary)
                inv_type = _get(row, COL_INV_TYPE)
                industry = _get(row, COL_INDUSTRY)
                row_id = hashlib.sha256(
                    f"{_get(row, COL_ADSH)}|{primary}|{inv_id}|{ddate}|{idx}".encode()
                ).hexdigest()[:16]

                # Non-accrual: not a typed XBRL fact in SOI. Some BDCs do
                # append "Non-accrual status" / "Non-Accrual Loans" inline to
                # the Identifier or Type axis, so we text-scan those strings.
                # Catches the explicit-tagger BDCs; silent on the rest.
                is_na_text = (
                    "non-accrual" in inv_id.lower()
                    or "nonaccrual" in inv_id.lower()
                    or "non-accrual" in inv_type.lower()
                    or "nonaccrual" in inv_type.lower()
                )

                h = {
                    "id": row_id,
                    "adsh": _get(row, COL_ADSH),
                    "cik": str(cik),
                    "bdc_name": str(bdc_name),
                    "period": str(ddate),  # observation date, not filing period
                    "company_name": inv_id[:200],
                    "industry": industry[:100],
                    "investment_type": inv_type[:100],
                    "interest_rate": _safe_float(_get(row, COL_RATE)),
                    "pik_rate": _safe_float(_get(row, COL_PIK)),
                    "cost_basis": _safe_float(_get(row, COL_COST)),
                    "fair_value": _safe_float(_get(row, COL_FV)),
                    "fair_value_pct_nav": _safe_float(_get(row, COL_PCT_NAV)),
                    "maturity_date": _get(row, COL_MATURITY)[:20],
                    "is_nonaccrual": 1 if is_na_text else 0,
                    "fetched_at": now,
                }
                holdings.append(h)

                try:
                    cols = ", ".join(h.keys())
                    placeholders = ", ".join(f":{k}" for k in h.keys())
                    conn.execute(
                        f"INSERT OR REPLACE INTO bdc_holdings ({cols}) VALUES ({placeholders})",
                        h,
                    )
                    stored += 1
                except Exception as e:
                    logger.debug(f"BDC holding insert skip: {e}")

            # Source filing: rows are sorted by 'filed' DESC, so the first
            # row of each group came from the most-recently-filed view.
            first_row = sub.iloc[0] if not sub.empty else None
            src_adsh = _get(first_row, COL_ADSH) if first_row is not None else None
            src_filed = (
                _get(first_row, "filed")
                if first_row is not None and "filed" in df.columns
                else None
            )

            summary = _compute_bdc_summary(
                holdings, str(bdc_name), str(cik), str(ddate),
                adsh=src_adsh or None, filed=src_filed or None,
            )
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

    logger.info(f"BDC data stored{tag}: {stored} holdings")
    return stored


def fetch_bdc_data() -> int:
    """Discover the latest BDC ZIP, download, parse, ingest. Returns holdings stored."""
    zip_url = _get_latest_bdc_zip_url()
    if not zip_url:
        logger.error("Could not determine BDC ZIP URL")
        return 0

    df = _download_and_parse_soi(zip_url)
    if df is None or df.empty:
        return 0

    source = zip_url.rsplit("/", 1)[-1]
    return _ingest_dataframe(df, source=source)


def _list_all_bdc_zip_urls() -> list[str]:
    """Scrape the index page for all ZIP links (quarterly + monthly), sorted
    oldest → newest. Backfill iterates these in chronological order so newer
    filings overwrite older ones in bdc_summary's INSERT OR REPLACE."""
    try:
        resp = requests.get(BDC_DATA_INDEX, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        matches = re.findall(BDC_ZIP_HREF_RE, resp.text)
        unique = sorted(set(matches), key=_sort_key)  # oldest first
        return [f"https://www.sec.gov{p}" for p in unique]
    except Exception as e:
        logger.error(f"BDC ZIP list discovery error: {e}")
        return []


def backfill_bdc_data(since_year: Optional[int] = None) -> dict:
    """Iterate every historical BDC ZIP in chronological order, ingest each.

    `since_year`: optional 4-digit year; ZIPs older than Jan of that year are skipped.
    Returns {"zips_processed": n, "total_holdings": n, "per_zip": [...]}.

    Ingestion uses INSERT OR IGNORE on bdc_holdings (deduped by adsh+axis+id)
    and INSERT OR REPLACE on bdc_summary (per cik+ddate). Going oldest →
    newest means the most recently filed view of any (cik, ddate) wins, which
    matches the dedupe logic in _ingest_dataframe.
    """
    urls = _list_all_bdc_zip_urls()
    if since_year is not None:
        urls = [u for u in urls if _sort_key(u)[0] >= since_year]
    if not urls:
        logger.error("No BDC ZIPs discovered for backfill")
        return {"zips_processed": 0, "total_holdings": 0, "per_zip": []}

    logger.info(f"BDC backfill starting: {len(urls)} ZIPs")
    per_zip: list[dict] = []
    total = 0
    for i, zip_url in enumerate(urls, 1):
        name = zip_url.rsplit("/", 1)[-1]
        logger.info(f"BDC backfill [{i}/{len(urls)}] {name}")
        df = _download_and_parse_soi(zip_url)
        if df is None or df.empty:
            per_zip.append({"zip": name, "holdings": 0, "status": "empty"})
            continue
        n = _ingest_dataframe(df, source=name)
        total += n
        per_zip.append({"zip": name, "holdings": n, "status": "ok"})

    logger.info(f"BDC backfill complete: {total} holdings across {len(urls)} ZIPs")
    return {"zips_processed": len(urls), "total_holdings": total, "per_zip": per_zip}


def get_watch_list() -> list[dict]:
    """Curated watch list from config/data_sources.yaml. Foundation-provided."""
    cfg = load_data_sources()
    return cfg.get("bdc", {}).get("watch_list", [])


def get_bdc_nonaccrual_trend() -> list[dict]:
    """Aggregate non-accrual rate across all BDCs by observation period.
    Core private-credit stress indicator.

    Aggregates are NAV-weighted rather than simple averages so a tiny BDC with
    an outlier mark doesn't dominate the trend versus a $25bn BDC near par.
    Filtered to periods with ≥5 BDCs so off-cycle single-BDC ddates (e.g.
    Saratoga's Feb fiscal close) don't whipsaw the mark-to-cost line.

    The WA-rate aggregate excludes BDCs with wa_interest_rate = 0/NULL from
    both numerator and denominator; many of the largest BDCs aggregate at
    issuer level and don't tag tranche rates, which would otherwise dilute
    the industry rate toward zero.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                period,
                COUNT(DISTINCT cik)                                          AS n_bdcs,
                SUM(nonaccrual_fv)                                           AS total_nonaccrual_fv,
                SUM(total_fair_value)                                        AS total_fv,
                SUM(total_cost_basis)                                        AS total_cost,
                CASE WHEN SUM(total_fair_value) > 0
                     THEN SUM(nonaccrual_fv) * 1.0 / SUM(total_fair_value)
                     ELSE NULL END                                           AS avg_nonaccrual_rate,
                CASE WHEN SUM(total_cost_basis) > 0
                     THEN SUM(total_fair_value) * 1.0 / SUM(total_cost_basis)
                     ELSE NULL END                                           AS avg_mark_to_cost,
                CASE WHEN SUM(CASE WHEN wa_interest_rate > 0
                                   THEN total_fair_value ELSE 0 END) > 0
                     THEN SUM(CASE WHEN wa_interest_rate > 0
                                   THEN wa_interest_rate * total_fair_value
                                   ELSE 0 END) * 1.0
                          / SUM(CASE WHEN wa_interest_rate > 0
                                     THEN total_fair_value ELSE 0 END)
                     ELSE NULL END                                           AS avg_wa_rate
            FROM bdc_summary
            WHERE total_fair_value > 0
            GROUP BY period
            HAVING COUNT(DISTINCT cik) >= 5
            ORDER BY period ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _edgar_filing_url(cik: Optional[str], adsh: Optional[str]) -> Optional[str]:
    """Canonical EDGAR filing-index URL from CIK + accession number.

    Example: cik=1287750, adsh=0001287750-25-000026 →
    https://www.sec.gov/Archives/edgar/data/1287750/000128775025000026/0001287750-25-000026-index.htm
    """
    if not cik or not adsh:
        return None
    try:
        cik_int = int(str(cik).lstrip("0") or "0")
        if cik_int == 0:
            return None
    except ValueError:
        return None
    adsh_clean = str(adsh).replace("-", "")
    if len(adsh_clean) != 18:
        return None
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
        f"{adsh_clean}/{adsh}-index.htm"
    )


def _attach_filing_url(row: dict) -> dict:
    """Annotate a bdc_summary row with `filing_url` if cik+adsh are present.

    Also nulls out implausible portfolio marks (outside [0.3, 3.0]) — a few
    filers ship wrong-scale XBRL cost values (e.g. Prospect's 2959% mark),
    which is a data error, not a credit event.
    """
    row["filing_url"] = _edgar_filing_url(row.get("cik"), row.get("adsh"))
    mark = row.get("mark_to_cost")
    if mark is not None and not (0.3 <= mark <= 3.0):
        row["mark_to_cost"] = None
    return row


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
    return [_attach_filing_url(dict(r)) for r in rows]


def get_bdc_summary_latest_per_bdc() -> list[dict]:
    """Each BDC's most-recent observation across all periods.

    Different BDCs report on different fiscal calendars (Saratoga is off-cycle,
    some haven't filed Q1 yet), so a single global "latest period" leaves most
    BDCs missing. Joining bdc_summary against MAX(period) per cik gives the
    freshest available snapshot per BDC. Sorted by NAV desc.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.*
            FROM bdc_summary s
            JOIN (
                SELECT cik, MAX(period) AS max_period
                FROM bdc_summary
                WHERE total_fair_value > 0
                GROUP BY cik
            ) m
              ON s.cik = m.cik AND s.period = m.max_period
            ORDER BY s.total_fair_value DESC
            """
        ).fetchall()
    return [_attach_filing_url(dict(r)) for r in rows]


def get_bdc_aggregate_trend() -> list[dict]:
    """Per-period industry aggregates: NAV total ($), NAV-weighted WA rate,
    NAV-weighted portfolio mix (1st lien / 2nd lien / equity), mark-to-cost.

    Filters to periods with at least 5 reporting BDCs so off-cycle single-BDC
    observations (e.g. Saratoga's Feb fiscal close) don't add spikes to the chart.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                period,
                COUNT(DISTINCT cik) AS n_bdcs,
                SUM(total_fair_value) AS total_fv,
                SUM(total_cost_basis) AS total_cost,
                CASE WHEN SUM(total_fair_value) > 0
                     THEN SUM(total_fair_value) * 1.0 / SUM(total_cost_basis)
                     ELSE NULL END AS mark_to_cost,
                -- WA rate: exclude BDCs with no rate data (NULL/0) from
                -- both numerator and denominator to avoid zero-dilution.
                CASE WHEN SUM(CASE WHEN wa_interest_rate > 0
                                   THEN total_fair_value ELSE 0 END) > 0
                     THEN SUM(CASE WHEN wa_interest_rate > 0
                                   THEN wa_interest_rate * total_fair_value
                                   ELSE 0 END) * 1.0
                          / SUM(CASE WHEN wa_interest_rate > 0
                                     THEN total_fair_value ELSE 0 END)
                     ELSE NULL END AS wa_interest_rate,
                -- Lien %s: NULL rows (BDCs we couldn't classify) are
                -- excluded from both sides; NAV-weighted over classifiable BDCs.
                CASE WHEN SUM(CASE WHEN pct_first_lien IS NOT NULL
                                   THEN total_fair_value ELSE 0 END) > 0
                     THEN SUM(CASE WHEN pct_first_lien IS NOT NULL
                                   THEN pct_first_lien * total_fair_value
                                   ELSE 0 END) * 1.0
                          / SUM(CASE WHEN pct_first_lien IS NOT NULL
                                     THEN total_fair_value ELSE 0 END)
                     ELSE NULL END AS pct_first_lien,
                CASE WHEN SUM(CASE WHEN pct_second_lien IS NOT NULL
                                   THEN total_fair_value ELSE 0 END) > 0
                     THEN SUM(CASE WHEN pct_second_lien IS NOT NULL
                                   THEN pct_second_lien * total_fair_value
                                   ELSE 0 END) * 1.0
                          / SUM(CASE WHEN pct_second_lien IS NOT NULL
                                     THEN total_fair_value ELSE 0 END)
                     ELSE NULL END AS pct_second_lien,
                CASE WHEN SUM(CASE WHEN pct_equity IS NOT NULL
                                   THEN total_fair_value ELSE 0 END) > 0
                     THEN SUM(CASE WHEN pct_equity IS NOT NULL
                                   THEN pct_equity * total_fair_value
                                   ELSE 0 END) * 1.0
                          / SUM(CASE WHEN pct_equity IS NOT NULL
                                     THEN total_fair_value ELSE 0 END)
                     ELSE NULL END AS pct_equity
            FROM bdc_summary
            WHERE total_fair_value > 0
            GROUP BY period
            HAVING COUNT(DISTINCT cik) >= 5
            ORDER BY period ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_bdc_sector_trend(min_bdcs: int = 3) -> list[dict]:
    """Cross-BDC sector performance time series from bdc_industry.

    Per (period, sector): total fair value / cost, mark-to-cost, share of
    that period's total FV, and the number of BDCs contributing. Mark-to-cost
    is computed only over rows carrying BOTH cost and FV so cost-only or
    FV-only disclosures don't skew the ratio. Periods with < 5 reporting
    BDCs overall are dropped (off-cycle fiscal closes), and sectors with
    < `min_bdcs` contributors in a period are dropped as too thin.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            WITH good_periods AS (
                SELECT period
                FROM bdc_industry
                GROUP BY period
                HAVING COUNT(DISTINCT cik) >= 5
            ),
            period_totals AS (
                SELECT period, SUM(fair_value) AS period_fv
                FROM bdc_industry
                WHERE fair_value > 0
                GROUP BY period
            )
            SELECT
                b.period,
                b.sector,
                COUNT(DISTINCT b.cik)  AS n_bdcs,
                SUM(b.fair_value)      AS total_fv,
                SUM(b.cost_basis)      AS total_cost,
                CASE WHEN SUM(CASE WHEN b.cost_basis > 0 AND b.fair_value > 0
                                   THEN b.cost_basis ELSE 0 END) > 0
                     THEN SUM(CASE WHEN b.cost_basis > 0 AND b.fair_value > 0
                                   THEN b.fair_value ELSE 0 END) * 1.0
                          / SUM(CASE WHEN b.cost_basis > 0 AND b.fair_value > 0
                                     THEN b.cost_basis ELSE 0 END)
                     ELSE NULL END     AS mark_to_cost,
                CASE WHEN t.period_fv > 0
                     THEN SUM(CASE WHEN b.fair_value > 0
                                   THEN b.fair_value ELSE 0 END) * 1.0
                          / t.period_fv
                     ELSE NULL END     AS fv_share
            FROM bdc_industry b
            JOIN good_periods g ON g.period = b.period
            LEFT JOIN period_totals t ON t.period = b.period
            GROUP BY b.period, b.sector
            HAVING COUNT(DISTINCT b.cik) >= ?
            ORDER BY b.period ASC, total_fv DESC
            """,
            (min_bdcs,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_bdc_sector_details() -> dict:
    """Per-BDC drill-down behind each sector's aggregate mark.

    For the two most recent well-covered periods (≥5 reporting BDCs), returns
    {sector: [{cik, bdc_name, fair_value, cost_basis, mark_to_cost,
    prior_mark, delta_bps}, ...]} sorted by fair value desc — who drove the
    sector's move, and how dispersed the marks are across managers.

    One payload for all sectors (no params) so the static-snapshot build can
    bake it as a single file.
    """
    with get_conn() as conn:
        periods = [r["period"] for r in conn.execute(
            """
            SELECT period FROM bdc_industry
            GROUP BY period HAVING COUNT(DISTINCT cik) >= 5
            ORDER BY period DESC LIMIT 2
            """
        )]
        if not periods:
            return {"latest_period": None, "prior_period": None, "sectors": {}}
        latest_p = periods[0]
        prior_p = periods[1] if len(periods) > 1 else None

        rows = conn.execute(
            """
            SELECT period, sector, cik, bdc_name,
                   SUM(cost_basis) AS cost, SUM(fair_value) AS fv
            FROM bdc_industry
            WHERE period IN (?, ?)
            GROUP BY period, sector, cik
            """,
            (latest_p, prior_p or ""),
        ).fetchall()

    def _mark(fv, cost):
        return fv / cost if fv and cost and cost > 0 else None

    prior_marks: dict[tuple[str, str], Optional[float]] = {}
    for r in rows:
        if r["period"] == prior_p:
            prior_marks[(r["sector"], r["cik"])] = _mark(r["fv"], r["cost"])

    sectors: dict[str, list[dict]] = {}
    for r in rows:
        if r["period"] != latest_p:
            continue
        mark = _mark(r["fv"], r["cost"])
        prior = prior_marks.get((r["sector"], r["cik"]))
        sectors.setdefault(r["sector"], []).append({
            "cik": r["cik"],
            "bdc_name": r["bdc_name"],
            "fair_value": r["fv"],
            "cost_basis": r["cost"],
            "mark_to_cost": mark,
            "prior_mark": prior,
            "delta_bps": (mark - prior) * 10_000
                         if mark is not None and prior is not None else None,
        })
    for entries in sectors.values():
        entries.sort(key=lambda d: -(d["fair_value"] or 0))

    return {"latest_period": latest_p, "prior_period": prior_p, "sectors": sectors}


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
