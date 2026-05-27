"""
backend/data/abs_pricing.py — ABS new-issue spread momentum.

New-issue spreads on auto/equipment ABS are a forward read on the consumer
credit risk premium: when subordinate subprime-auto tranches price wide, the
market is demanding more for consumer risk *before* it shows in realized
delinquencies. There is no FRED series for this — it must be assembled deal by
deal from SEC filings.

Approach (token-free, no LLM): the underwriters file a standardized "pricing
term sheet" as a Free Writing Prospectus (FWP) on pricing day. It carries a
tranche table with a spread column (over the interpolated swaps curve / SOFR).
Ratings FWPs filed earlier have no spread column, so the presence of a parseable
spread table cleanly distinguishes the pricing sheet. We:
  1. full-text-search EDGAR for recent ABS FWPs,
  2. fetch each document and try to parse a tranche pricing table,
  3. store the per-tranche spreads with deal metadata in `abs_pricing`.

The momentum signal then compares tranche spreads across deals over time, by
segment (subprime auto / prime auto / equipment) and seniority.
"""

import io
import logging
import re
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from config import load_data_sources, settings
from cache.db import get_conn, upsert_abs_pricing_tranche

logger = logging.getLogger(__name__)

BASE_EFTS = "https://efts.sec.gov/LATEST/search-index"
HEADERS = {"User-Agent": settings.EDGAR_USER_AGENT, "Accept": "application/json"}

# FWP discovery: phrases that appear in ABS deal/trust names. These find the
# ratings FWP (which names the deal); we then enumerate the trust's *other*
# FWPs to reach the pricing term sheet (which is a terse table and doesn't name
# the deal, so it can't be found by a text search directly).
_DEFAULT_KEYWORDS = [
    "auto receivables",
    "auto owner trust",
    "auto lease",
    "equipment finance",
    "dealer floorplan",
]
BASE_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Issuer/deal-name fragments that mark a deal as subprime auto. Everything else
# auto is treated as prime; "equipment" in the name wins as equipment.
_SUBPRIME = [
    "santander drive", "drive auto", "exeter", "americredit", "carvana",
    "drivetime", "bridgecrest", "westlake", "flagship", "consumer portfolio",
    "cps auto", "united auto credit", "prestige", "american credit acceptance",
    "acac", "veros", "first investors", "gls", "global lending", "skopos",
    "foursight", "regional management", "lobel", "tricolor", "sierra",
]
_EQUIPMENT = ["equipment", "deere", "cnh", "kubota", "dll", "great america", "octane"]

# ── Pricing-table header mapping ─────────────────────────────────────────────
_FIELD_TOKENS = {
    "class": {"CLS", "CLASS"},
    "spread": {"SPREAD", "SPRD", "SPR", "DM"},
    "coupon": {"COUPON", "CPN", "INTERESTRATE", "RATE"},
    "benchmark": {"BNCH", "BENCH", "BENCHMARK"},
    "wal": {"WAL"},
    "rating": {"M/S", "M/F", "MOODYS", "RATING"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(s) -> str:
    """Normalize a header cell to comparable tokens: keep A-Z and slash only."""
    return re.sub(r"[^A-Z/]", "", str(s).upper())


def _num(s):
    """First signed number in a cell. '+ 53' -> 53.0, '99.99' -> 99.99."""
    m = re.search(r"[-+]?\s*\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group().replace(" ", "")) if m else None


def _segment(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in _EQUIPMENT):
        return "equipment"
    if any(k in n for k in _SUBPRIME):
        return "subprime_auto"
    if "auto" in n or "vehicle" in n or "motor" in n:
        return "prime_auto"
    return "other"


def _archive_url(cik: int, accession: str, doc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"


def _parse_pricing_tranches(html: str) -> list[dict]:
    """Find the tranche pricing table and return one dict per offered tranche.

    Returns [] if no table has both a class column and a spread column (i.e.
    this is a ratings sheet or some other FWP, not a pricing term sheet).
    """
    try:
        tables = pd.read_html(io.StringIO(html))  # pandas 3.0 requires a buffer
    except Exception:
        return []

    for t in tables:
        rows = [[str(c).strip() for c in t.iloc[r].tolist()] for r in range(t.shape[0])]

        # Locate the header row: contains a class token AND a spread token.
        hdr_idx = None
        for ri, row in enumerate(rows):
            toks = {_norm(c) for c in row}
            if toks & _FIELD_TOKENS["class"] and toks & _FIELD_TOKENS["spread"]:
                hdr_idx = ri
                break
        if hdr_idx is None:
            continue

        colmap: dict[str, int] = {}
        for ci, cell in enumerate(rows[hdr_idx]):
            n = _norm(cell)
            for field, toks in _FIELD_TOKENS.items():
                if n in toks and field not in colmap:
                    colmap[field] = ci

        out: list[dict] = []
        for row in rows[hdr_idx + 1 :]:
            joined = " ".join(row).upper()
            if "RETAINED" in joined or "NOT OFFERED" in joined:
                continue
            cls = re.sub(r"[\^*†◊]+$", "", row[colmap["class"]].strip()).strip()  # drop footnote marks
            if not cls or cls.lower() == "nan" or cls.startswith("=") or not re.match(r"[A-Za-z]", cls):
                continue
            spread = _num(row[colmap["spread"]]) if "spread" in colmap else None
            if spread is None:
                continue  # rows without a spread aren't offered tranches
            out.append(
                {
                    "class_name": cls,
                    "spread_bps": spread,
                    "coupon": _num(row[colmap["coupon"]]) if "coupon" in colmap else None,
                    "wal": _num(row[colmap["wal"]]) if "wal" in colmap else None,
                    "benchmark": row[colmap["benchmark"]].strip() if "benchmark" in colmap else None,
                    "rating": row[colmap["rating"]].strip() if "rating" in colmap else None,
                }
            )
        if out:
            return out
    return []


def _search_fwp(keyword: str, days_back: int) -> list[dict]:
    """EDGAR full-text search for recent FWP filings matching `keyword`."""
    from datetime import timedelta

    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    params = {
        "q": f'"{keyword}"',
        "forms": "FWP",
        "startdt": start,
        "enddt": end,
    }
    # EDGAR full-text search 500s intermittently on valid queries; retry briefly.
    for attempt in range(3):
        try:
            resp = requests.get(BASE_EFTS, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.json().get("hits", {}).get("hits", [])
        except Exception as e:
            if attempt == 2:
                logger.error("ABS pricing search error (kw=%s): %s", keyword, e)
            else:
                time.sleep(0.5 * (attempt + 1))
    return []


def _already_parsed(accession: str) -> bool:
    with get_conn() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM abs_pricing WHERE accession_no = ? LIMIT 1", (accession,)
            ).fetchone()
            is not None
        )


def _discover_trust_ciks(keywords: list[str], days_back: int) -> dict[int, str]:
    """Name-search recent ABS FWPs; return {cik: trust_name} for trust filers."""
    ciks: dict[int, str] = {}
    for kw in keywords:
        for h in _search_fwp(kw, days_back):
            for dn in h.get("_source", {}).get("display_names", []):
                m = re.search(r"CIK\s+(\d+)", dn)
                name = dn.split("(CIK")[0].strip().rstrip(",")
                # The pricing term sheet is filed under the deal *trust*, whose
                # name is unique per deal (carries the series, e.g. "2026-1").
                if m and "trust" in name.lower():
                    ciks[int(m.group(1))] = name
        time.sleep(0.15)
    return ciks


def _list_trust_fwps(cik: int, cutoff: str) -> list[tuple[str, str, str]]:
    """Return [(accession, primaryDoc, filingDate)] for a trust's recent FWPs."""
    try:
        resp = requests.get(BASE_SUBMISSIONS.format(cik=cik), headers=HEADERS, timeout=15)
        resp.raise_for_status()
        recent = resp.json().get("filings", {}).get("recent", {})
    except Exception as e:
        logger.debug("ABS pricing submissions skip CIK %d: %s", cik, e)
        return []

    out: list[tuple[str, str, str]] = []
    forms = recent.get("form", [])
    for i, form in enumerate(forms):
        if form == "FWP" and recent.get("filingDate", [])[i] >= cutoff:
            out.append(
                (recent["accessionNumber"][i], recent["primaryDocument"][i], recent["filingDate"][i])
            )
    return out


def fetch_abs_pricing(days_back: int = 30) -> int:
    """Discover ABS pricing FWPs, parse spreads, store tranches. Returns rows written."""
    from datetime import timedelta

    cfg = load_data_sources().get("abs_pricing", {})
    keywords = cfg.get("discovery_keywords", _DEFAULT_KEYWORDS)
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    trust_ciks = _discover_trust_ciks(keywords, days_back)
    now = _now()
    count = 0

    for cik, name in trust_ciks.items():
        segment = _segment(name)
        for acc, doc, fdate in _list_trust_fwps(cik, cutoff):
            if _already_parsed(acc):
                continue
            url = _archive_url(cik, acc, doc)
            try:
                resp = requests.get(
                    url, headers={"User-Agent": settings.EDGAR_USER_AGENT}, timeout=20
                )
                resp.raise_for_status()
            except Exception as e:
                logger.debug("ABS pricing doc fetch skip %s: %s", acc, e)
                time.sleep(0.15)
                continue

            tranches = _parse_pricing_tranches(resp.text)
            if tranches:
                for tr in tranches:
                    upsert_abs_pricing_tranche(
                        {
                            "accession_no": acc,
                            "deal_name": name,
                            "issuer": name,
                            "segment": segment,
                            "pricing_date": fdate,
                            "url": url,
                            "fetched_at": now,
                            **tr,
                        }
                    )
                    count += 1
                logger.info("ABS pricing: %s (%s) — %d tranches", name, segment, len(tranches))
            time.sleep(0.15)

    logger.info("ABS pricing fetch complete — %d tranches stored", count)
    return count


def get_abs_pricing_deals(limit: int = 40, segment: str | None = None) -> list[dict]:
    """Recent priced deals, newest first, each with its tranches nested."""
    sql = "SELECT * FROM abs_pricing"
    params: list = []
    if segment:
        sql += " WHERE segment = ?"
        params.append(segment)
    sql += " ORDER BY pricing_date DESC, accession_no, wal"
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    deals: dict[str, dict] = {}
    for r in rows:
        d = deals.setdefault(
            r["accession_no"],
            {
                "accession_no": r["accession_no"],
                "deal_name": r["deal_name"],
                "segment": r["segment"],
                "pricing_date": r["pricing_date"],
                "url": r["url"],
                "tranches": [],
            },
        )
        d["tranches"].append(
            {
                "class_name": r["class_name"],
                "rating": r["rating"],
                "wal": r["wal"],
                "benchmark": r["benchmark"],
                "spread_bps": r["spread_bps"],
                "coupon": r["coupon"],
            }
        )
    return list(deals.values())[:limit]


def get_abs_spread_momentum() -> list[dict]:
    """Per (segment, pricing_date), the senior (AAA) and subordinate spread.

    Senior = the widest-WAL AAA-ish tranche (the benchmark senior spread);
    subordinate = the widest-WAL offered tranche (the mezz/junior risk read).
    Gives a time series the frontend can plot per segment.
    """
    with get_conn() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT segment, pricing_date, accession_no, deal_name, class_name, "
                "rating, wal, spread_bps FROM abs_pricing "
                "WHERE spread_bps IS NOT NULL ORDER BY pricing_date"
            ).fetchall()
        ]

    by_deal: dict[str, dict] = {}
    for r in rows:
        d = by_deal.setdefault(
            r["accession_no"],
            {
                "segment": r["segment"],
                "pricing_date": r["pricing_date"],
                "deal_name": r["deal_name"],
                "tranches": [],
            },
        )
        d["tranches"].append(r)

    out: list[dict] = []
    for deal in by_deal.values():
        trs = [t for t in deal["tranches"] if t["spread_bps"] is not None]
        if not trs:
            continue
        senior = [t for t in trs if (t["rating"] or "").upper().find("AAA") >= 0] or trs
        senior_sp = max(senior, key=lambda t: t["wal"] or 0)["spread_bps"]
        sub_sp = max(trs, key=lambda t: t["wal"] or 0)["spread_bps"]
        out.append(
            {
                "segment": deal["segment"],
                "pricing_date": deal["pricing_date"],
                "deal_name": deal["deal_name"],
                "senior_spread": senior_sp,
                "subordinate_spread": sub_sp,
            }
        )
    out.sort(key=lambda x: x["pricing_date"])
    return out
