"""
backend/data/abs_parser.py — 424B5 ABS prospectus-supplement parser.

Complements the FWP-based parser in data/abs_pricing.py:
  * FWP path:   pricing term sheet, spread printed directly, leaner schema.
  * 424B5 path: final prospectus supplement, coupon printed, spread computed
                against the maturity-matched on-the-run treasury. Carries the
                richer deal-level metadata (CUSIPs, full ratings, depositor,
                servicer, underwriters) that the FWP can't.

Pipeline:
  1. Discover recent 424B5 filings via EDGAR full-text search.
  2. Resolve the primary HTML document for each filing.
  3. Run the structured (BeautifulSoup + regex) extractor.
  4. If confidence is low, fall back to Claude to read the first ~3K chars.
  5. Classify asset class, compute benchmark spread, persist per-tranche rows.

Design principles:
  * Never crash on parse failure — log and continue to the next filing.
  * Always persist `parse_confidence` so the dashboard can flag uncertain rows.
  * Claude fallback is token-cheap: a single ~3K-char excerpt per low-confidence
    filing (Sonnet 4.6, ~$0.002 / filing).
"""

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
import requests
from bs4 import BeautifulSoup

from cache.db import get_conn, upsert_abs_tranche
from config import settings
from data.edgar import edgar_index_url  # CIK-aware index URL builder

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": settings.EDGAR_USER_AGENT, "Accept": "application/json"}
HTML_HEADERS = {"User-Agent": settings.EDGAR_USER_AGENT}
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_BASE = "https://www.sec.gov"

# Claude model for the low-confidence fallback. Kept in one place so model
# upgrades don't require a code search.
CLAUDE_MODEL = "claude-sonnet-4-6"

# ── Asset-class taxonomy ─────────────────────────────────────────────────────
# Keyword matching against issuer name + first ~5K chars of filing text.
# Order matters only for the fallback ("esoteric_other") — the first hit wins.
ASSET_CLASS_TAXONOMY: dict[str, list[str]] = {
    "prime_auto_loan":      ["prime auto", "auto loan", "auto owner trust",
                             "auto receivables", "ford credit auto owner",
                             "honda auto", "toyota auto", "nissan auto",
                             "hyundai auto", "bmw vehicle owner"],
    "subprime_auto_loan":   ["subprime auto", "exeter", "americredit",
                             "santander drive", "westlake",
                             "united auto credit", "consumer portfolio"],
    "auto_lease":           ["auto lease", "vehicle lease", "lease trust"],
    "credit_card":          ["credit card", "issuance trust", "master trust",
                             "chase issuance", "citibank credit card",
                             "american express credit"],
    "equipment":            ["equipment", "CNH", "deere", "john deere",
                             "caterpillar", "marlin", "pawnee",
                             "LEAF", "element fleet"],
    "consumer_loan":        ["consumer loan", "personal loan",
                             "SoFi", "lendingclub", "LC trust", "upstart",
                             "avant", "oportun", "prosper"],
    "student_loan":         ["student loan", "education loan", "SLABS",
                             "navient", "sallie mae", "nelnet"],
    "solar":                ["solar", "sunrun", "sunnova", "mosaic solar"],
    "esoteric_wireless":    ["wireless", "verizon", "T-Mobile",
                             "device payment", "handset"],
    "esoteric_aircraft":    ["aircraft", "AASET", "STAR", "atlas air"],
    "esoteric_whole_biz":   ["whole business", "franchise", "domino",
                             "sonic", "restaurant brands"],
    "esoteric_data_center": ["data center", "digital bridge"],
    "rmbs_non_agency":      ["residential mortgage", "home equity", "RMBS"],
    "cmbs":                 ["commercial mortgage", "CMBS"],
    "clo":                  ["CLO", "collateralized loan obligation"],
}


def _classify_asset_class(text: str) -> str:
    lower = text.lower()
    for cls, keywords in ASSET_CLASS_TAXONOMY.items():
        if any(kw.lower() in lower for kw in keywords):
            return cls
    return "esoteric_other"


# ── Cell parsing primitives ──────────────────────────────────────────────────

def _normalize_rating(raw: Optional[str]) -> Optional[str]:
    """Strip '(sf)' qualifier and surrounding whitespace; '' → None."""
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r"\(sf\)\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or None


def _parse_dollar(s: str) -> Optional[float]:
    """'$143,000,000' → 143000000.0"""
    if not s:
        return None
    cleaned = re.sub(r"[,$\s]", "", s)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_rate(s: str) -> Optional[float]:
    """'4.540%' → 4.54. Floating-rate strings return None (handled separately)."""
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*%", s)
    return float(m.group(1)) if m else None


def _parse_spread_bps(s: str) -> Optional[float]:
    """'SOFR + 55 bps' or '+55 bp' → 55.0"""
    if not s:
        return None
    m = re.search(r"\+\s*([\d.]+)\s*(?:bps|bp)", s, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _parse_wal(s: str) -> Optional[float]:
    """'2.10 years' or '2.10' → 2.10"""
    if not s:
        return None
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else None


# ── EDGAR discovery ──────────────────────────────────────────────────────────

# Phrases that distinguish ABS 424B5s from corporate 424B5s. Searched
# individually so one keyword's 500 doesn't kill the whole pull.
_DISCOVERY_KEYWORDS = [
    "asset-backed notes",
    "receivables trust",
    "auto receivables",
    "equipment trust",
    "consumer loan trust",
    "issuance trust",
    "device payment",
]


def _discover_abs_424b5(days_back: int = 7) -> list[dict]:
    """Search EDGAR full-text for ABS-keyword 424B5 filings in the last N days.

    Returns one dict per unique filing: {accession_no, company_name,
    filed_at, filing_url}.
    """
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")

    results: list[dict] = []
    seen: set[str] = set()

    for keyword in _DISCOVERY_KEYWORDS:
        # NB: EDGAR FTS 500s on dateRange/_source — pass only the basics
        # (see data/edgar.py for the same workaround).
        params = {
            "q": f'"{keyword}"',
            "forms": "424B5",
            "startdt": since,
            "enddt": end,
        }
        try:
            resp = requests.get(EFTS_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
        except Exception as e:
            logger.error("424B5 discovery error [%s]: %s", keyword, e)
            time.sleep(0.5)
            continue

        for hit in hits:
            hit_id = hit.get("_id", "")
            # EFTS _id is "accession_no:primary_doc.htm" — strip the doc part
            # so dedup keys on the filing itself, not on each filing's doc.
            acc = (hit_id or "").split(":")[0]
            if not acc or acc in seen:
                continue
            seen.add(acc)
            src = hit.get("_source", {})
            company = ", ".join(src.get("display_names", []))
            results.append(
                {
                    "accession_no": acc,
                    "hit_id": hit_id,
                    "company_name": company,
                    "filed_at": src.get("file_date", ""),
                    "filing_url": edgar_index_url(hit_id, company),
                }
            )

        time.sleep(0.15)

    logger.info("Discovered %d candidate 424B5 filings", len(results))
    return results


def _get_primary_document_url(accession_no: str, company_str: str) -> Optional[str]:
    """Resolve the URL of the primary 424B5 HTML document.

    EDGAR exposes the filing manifest as `index.json` under the filing folder.
    We pull it, find the 424B5/424B2 entry whose name ends in .htm, and build
    the full archives URL. CIK is parsed from the EFTS company string
    ("Issuer Name (CIK 0001234567)").
    """
    m = re.search(r"CIK\s+(\d+)", company_str or "")
    if not m:
        return None
    cik = str(int(m.group(1)))  # archives path drops leading zeros
    acc_clean = accession_no.replace("-", "")
    index_url = f"{EDGAR_BASE}/Archives/edgar/data/{cik}/{acc_clean}/index.json"

    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        manifest = resp.json()
    except Exception as e:
        logger.debug("Index manifest fetch error [%s]: %s", accession_no, e)
        return None

    items = manifest.get("directory", {}).get("item", [])
    # Prefer the explicit 424B5/424B2 row, then any .htm that isn't an exhibit
    # or the filing-index page itself.
    primary = next(
        (it for it in items
         if it.get("type") in ("424B5", "424B2")
         and (it.get("name") or "").endswith(".htm")),
        None,
    )
    if primary is None:
        primary = next(
            (it for it in items
             if (it.get("name") or "").endswith(".htm")
             and "index" not in (it.get("name") or "").lower()),
            None,
        )
    if primary is None:
        return None
    return f"{EDGAR_BASE}/Archives/edgar/data/{cik}/{acc_clean}/{primary['name']}"


# ── Structured extractor ─────────────────────────────────────────────────────

def _extract_from_html(html: str, metadata: dict) -> dict:
    """Parse the prospectus HTML for deal- and tranche-level fields.

    Confidence flags:
      'high'   — pricing table found with ≥2 tranches
      'medium' — pricing table found with 1 tranche, OR Claude-merged
      'low'    — no pricing table; Claude fallback will be tried
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    result: dict = {
        "issuer_name": None,
        "depositor": None,
        "servicer": None,
        "closing_date": None,
        "cutoff_date": None,
        "underwriters": [],
        "asset_class": _classify_asset_class(text[:5000]),
        "tranches": [],
        "confidence": "low",
    }

    # Issuer name: <title> first, then look for a trust/LLC near the top.
    title = soup.find("title")
    if title:
        result["issuer_name"] = title.get_text(strip=True)[:200]
    for tag in soup.find_all(["h1", "h2", "h3", "b", "strong"])[:30]:
        t = tag.get_text(strip=True)
        if "trust" in t.lower() or "LLC" in t or "Inc." in t:
            result["issuer_name"] = t[:200]
            break

    closing_match = re.search(
        r"[Cc]losing\s+[Dd]ate[^:]*:\s*([A-Z][a-z]+ \d+,\s*\d{4}|\w+ \d{4})",
        text,
    )
    if closing_match:
        result["closing_date"] = closing_match.group(1)

    cutoff_match = re.search(
        r"[Cc]utoff\s+[Dd]ate[^:]*:\s*([A-Z][a-z]+ \d+,\s*\d{4})",
        text,
    )
    if cutoff_match:
        result["cutoff_date"] = cutoff_match.group(1)

    # Find the pricing table AND the header row inside it. Many ABS prospectus
    # tables have multi-row headers (e.g. a section title, then column names);
    # picking row 0 unconditionally mis-aligns columns. We scan rows of each
    # table for the first one that contains BOTH a class-ish token AND an
    # amount/principal-ish token. That's the real column-name row.
    def _row_text(row) -> str:
        return row.get_text(" ", strip=True).lower()

    def _is_header_row(text: str) -> bool:
        has_class = "class" in text or "notes" in text
        has_money = "principal" in text or "amount" in text
        return has_class and has_money

    pricing_table = None
    header_row_idx = 0
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        for i, r in enumerate(rows[: min(6, len(rows))]):
            if _is_header_row(_row_text(r)):
                pricing_table = table
                header_row_idx = i
                break
        if pricing_table is not None:
            break

    if pricing_table is None:
        return result  # confidence stays 'low'

    rows = pricing_table.find_all("tr")
    # Many EDGAR-rendered tables include blank spacer cells between every real
    # column (a leftover of how the original word-processor formatted the
    # table). Build a "compact" view that drops blanks so column indexes line
    # up between the header and the body rows. We track the mapping back to
    # raw cell positions so cell_text() can still index the body row.
    raw_header_cells = rows[header_row_idx].find_all(["th", "td"])

    # Lone "$" and "%" cells aren't real columns — they're typographic
    # spacers. Drop them when compacting so the header/body cell counts
    # line up.
    _spacer_cells = {"$", "%", "(", ")"}

    def _compact(cell_list: list, lower: bool = False) -> tuple[list[str], list[int]]:
        out: list[str] = []
        idx_map: list[int] = []
        for i, c in enumerate(cell_list):
            t = c.get_text(" ", strip=True)
            if t and t not in _spacer_cells:
                out.append(t.lower() if lower else t)
                idx_map.append(i)
        return out, idx_map

    header_cells, _header_raw_idx = _compact(raw_header_cells, lower=True)
    body_rows = rows[header_row_idx + 1:]

    # Column roles inferred from header text. Defaults are positional fallbacks
    # for tables that omit headers entirely.
    def _col(predicates: list[str], default: int) -> int:
        for i, h in enumerate(header_cells):
            if any(p in h for p in predicates):
                return i
        return default

    col_class = _col(["class"], 0)
    col_amount = _col(["principal", "amount"], 1)
    col_rate = _col(["interest", "rate", "coupon"], 2)
    col_final = _col(["payment", "maturity", "date"], -1)
    col_wal = _col(["wal", "average life", "life"], -1)

    # Tranche class names follow a tight shape (A-1, A2, B, Class C, IO, etc.) —
    # short, mostly letters, optional digit suffix. Use this to reject footer
    # rows AND, more importantly, column-mis-mapping cases where the "class"
    # column actually held a principal amount or a date.
    _class_name_re = re.compile(r"^(class\s+)?[A-Z][-A-Z0-9]{0,8}(\s*notes?)?$",
                                re.IGNORECASE)

    tranches: list[dict] = []
    for row in body_rows:
        raw_cells = row.find_all(["td", "th"])
        if len(raw_cells) < 3:
            continue
        # Use the same compaction as the header so col_X indexes align with
        # the body cells, ignoring blank spacer columns. When a body row also
        # contains a "$" or "%" cell on its own (separate from the numeric),
        # we keep those tokens so the dollar/rate parsers can still see them.
        cells_text, _idx = _compact(raw_cells)

        def cell_text(idx: int) -> str:
            if idx < 0 or idx >= len(cells_text):
                return ""
            return cells_text[idx]

        class_raw = cell_text(col_class).strip()
        if not class_raw or class_raw.lower() in ("total", "totals", ""):
            continue
        if len(class_raw) > 20:
            continue
        # Sanity check: if the "class" cell looks like a dollar/percent/date, the
        # column mapping mis-aligned. Drop the row rather than store garbage.
        if not _class_name_re.match(class_raw):
            continue

        amount_raw = cell_text(col_amount)
        rate_raw = cell_text(col_rate)
        final_raw = cell_text(col_final) if col_final >= 0 else ""
        wal_raw = cell_text(col_wal) if col_wal >= 0 else ""

        is_floating = any(kw in rate_raw.lower()
                          for kw in ["sofr", "libor", "+", "bps", "bp"])

        tranche = {
            "class_name": class_raw.replace("\xa0", " "),
            "principal_amount": _parse_dollar(amount_raw),
            "coupon_type": "floating" if is_floating else "fixed",
            "coupon_rate": None if is_floating else _parse_rate(rate_raw),
            "floating_index": "SOFR" if "sofr" in rate_raw.lower()
                              else ("LIBOR" if "libor" in rate_raw.lower() else None),
            "floating_spread_bps": _parse_spread_bps(rate_raw),
            "wal_years": _parse_wal(wal_raw),
            "final_payment_date": final_raw[:50] if final_raw else None,
            "legal_maturity_date": None,
            "price_to_public": None,
            "cusip": None,
            "rating_sp": None,
            "rating_moodys": None,
            "rating_kbra": None,
            "rating_fitch": None,
        }

        if tranche["principal_amount"] is not None:
            tranches.append(tranche)

    result["tranches"] = tranches
    result["confidence"] = "high" if len(tranches) >= 2 else "medium" if tranches else "low"

    # Ratings: typically a separate table or descriptive text. Pattern matches
    # "Class A-1 Notes: Aaa/AAA" or similar; heuristic assignment to tranches.
    rating_pattern = re.compile(
        r"Class\s+([A-Z][-\d]*)\s+[Nn]otes[^:]*:"
        r"[^A-Z]*([A-Za-z]+\+?-?)\s*/\s*([A-Za-z]+\+?-?)",
        re.IGNORECASE,
    )
    for m in rating_pattern.finditer(text[:10000]):
        cls = m.group(1)
        for tranche in tranches:
            if tranche["class_name"].startswith(cls):
                # Heuristic: order is Moody's / S&P. Some filings reverse this;
                # downstream consumers should treat the pair, not a single agency.
                tranche["rating_moodys"] = _normalize_rating(m.group(2))
                tranche["rating_sp"] = _normalize_rating(m.group(3))

    # CUSIPs: 9-char alphanumeric, validated by length + character class.
    cusip_matches = re.findall(r"\b([0-9A-Z]{9})\b", text[:5000])
    valid_cusips = [c for c in cusip_matches if re.fullmatch(r"[0-9A-Z]{9}", c)]
    for i, tranche in enumerate(tranches):
        if i < len(valid_cusips):
            tranche["cusip"] = valid_cusips[i]

    # WAL extraction from prepayment sensitivity tables. The pricing table at
    # the top of the prospectus rarely includes WAL; it lives in per-class
    # sensitivity tables much deeper in the doc, in a row whose first cell
    # reads "Weighted Average Life (years)" followed by 3-7 values across
    # prepayment-speed columns. We attribute each WAL row to a tranche by
    # walking back to the nearest "Class X-Y" mention in the same table, and
    # take the median of the row's numeric values as the canonical WAL.
    _wal_first_cell_re = re.compile(r"^\s*weighted\s+average\s+life", re.IGNORECASE)
    _class_in_table_re = re.compile(r"Class\s+([A-Z][-A-Z0-9]*)", re.IGNORECASE)

    def _class_designator(class_name: str) -> str:
        """Strip 'Class ' prefix / 'notes' suffix and NBSP to get e.g. 'A-1'."""
        cleaned = class_name.replace("\xa0", " ")
        cleaned = re.sub(r"(?i)^class\s+", "", cleaned).strip()
        cleaned = re.sub(r"(?i)\s+notes?$", "", cleaned).strip()
        return cleaned.upper()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        for ri, row in enumerate(rows):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            first = cells[0].get_text(" ", strip=True)
            if not _wal_first_cell_re.match(first):
                continue
            # Extract numeric values from the rest of the row.
            wal_vals = []
            for c in cells[1:]:
                txt = c.get_text(" ", strip=True)
                m = re.fullmatch(r"\d+\.\d+", txt)
                if m:
                    wal_vals.append(float(txt))
            if not wal_vals:
                continue
            # Attribute to tranche: look in *preceding* rows of this same
            # table for "Class X-Y" — sensitivity tables typically have one
            # such header per class block.
            target_class = None
            for back_row in rows[:ri][::-1]:
                back_text = back_row.get_text(" ", strip=True)
                m = _class_in_table_re.search(back_text)
                if m:
                    target_class = m.group(1).upper()
                    break
            if target_class is None:
                continue
            wal_median = sorted(wal_vals)[len(wal_vals) // 2]
            for tranche in tranches:
                designator = _class_designator(tranche["class_name"])
                # Sensitivity tables sometimes label "A-2" even when there's
                # an A-2a and A-2b — accept prefix match in either direction.
                if designator == target_class or designator.startswith(target_class):
                    if tranche.get("wal_years") is None:
                        tranche["wal_years"] = wal_median

    return result


# ── Claude fallback extractor ────────────────────────────────────────────────

_claude_client: anthropic.Anthropic | None = None


def _get_claude_client() -> anthropic.Anthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _claude_client


_CLAUDE_EXTRACTION_PROMPT = """You are an expert structured finance analyst.
Extract ABS deal pricing data from this 424B5 prospectus supplement excerpt.

Return ONLY a JSON object with this exact structure. Use null for missing values.
No preamble, no explanation, raw JSON only.

{
  "issuer_name": "string or null",
  "asset_class_hint": "brief description of what assets back the deal",
  "closing_date": "YYYY-MM-DD or null",
  "cutoff_date": "YYYY-MM-DD or null",
  "tranches": [
    {
      "class_name": "A-1",
      "principal_amount": 143000000,
      "coupon_type": "fixed or floating",
      "coupon_rate": 4.54,
      "floating_index": "SOFR or null",
      "floating_spread_bps": null,
      "wal_years": null,
      "final_payment_date": "string or null",
      "rating_sp": "AAA or null",
      "rating_moodys": "Aaa or null",
      "rating_kbra": "AAA or null"
    }
  ]
}

DOCUMENT EXCERPT:
"""


def _claude_extract(text_excerpt: str) -> Optional[dict]:
    """Send the first ~3K chars to Claude for structured extraction.

    Called only when the structured parser yields low confidence. The pricing
    table is always near the top of a 424B5, so 3K chars is enough; longer
    excerpts spend tokens without improving recall.
    """
    if not settings.ANTHROPIC_API_KEY:
        return None

    try:
        client = _get_claude_client()
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": _CLAUDE_EXTRACTION_PROMPT + text_excerpt[:3000],
            }],
        )
        raw = response.content[0].text.strip()
        # Strip ```json fences if Claude returned them despite instructions.
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.error("Claude extraction error: %s", e)
        return None


# ── Benchmark spread computation ─────────────────────────────────────────────

# WAL → on-the-run treasury tenor mapping. Half-life boundary picks the
# closer tenor; >8.5y collapses to the 10Y benchmark (we don't track 20/30Y).
_TENOR_MAP: list[tuple[float, str, str]] = [
    (1.5, "DGS1",  "UST1Y"),
    (2.5, "DGS2",  "UST2Y"),
    (3.5, "DGS3",  "UST3Y"),
    (6.0, "DGS5",  "UST5Y"),
    (8.5, "DGS7",  "UST7Y"),
    (float("inf"), "DGS10", "UST10Y"),
]


def _treasury_for_wal(wal_years: float) -> tuple[str, str]:
    """Return (fred_series_id, benchmark_label) for a WAL in years."""
    return next((s, b) for thresh, s, b in _TENOR_MAP if wal_years < thresh)


def _get_treasury_rate_on_date(date_str: str, wal_years: float) -> Optional[float]:
    """Latest DGS{tenor} value on or before `date_str` from the metrics table."""
    if not wal_years or not date_str:
        return None
    series_id, _ = _treasury_for_wal(wal_years)
    try:
        target = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None

    with get_conn() as conn:
        row = conn.execute(
            """SELECT value FROM metrics
               WHERE series_id = ? AND date <= ?
               ORDER BY date DESC LIMIT 1""",
            (series_id, target),
        ).fetchone()
    return float(row["value"]) if row and row["value"] is not None else None


def _compute_spread(tranche: dict, filing_date: str) -> dict:
    """Add benchmark / benchmark_rate / spread_to_benchmark / implied_yield.

    Fixed: spread = (coupon - treasury) * 100 bps. Floating: keep printed spread.
    """
    wal = tranche.get("wal_years")
    out = tranche.copy()

    if tranche.get("coupon_type") == "floating":
        out["benchmark"] = tranche.get("floating_index") or "SOFR"
        out["benchmark_rate"] = None  # SOFR snapshot lookup not yet wired in
        out["spread_to_benchmark"] = tranche.get("floating_spread_bps")
        out["implied_yield"] = None
        return out

    coupon = tranche.get("coupon_rate")
    if coupon is not None and wal:
        _, benchmark = _treasury_for_wal(wal)
        tsy_rate = _get_treasury_rate_on_date(filing_date, wal)
        out["benchmark"] = benchmark
        out["benchmark_rate"] = tsy_rate
        out["spread_to_benchmark"] = (
            round((coupon - tsy_rate) * 100, 1) if tsy_rate is not None else None
        )
        out["implied_yield"] = coupon
    else:
        out["benchmark"] = None
        out["benchmark_rate"] = None
        out["spread_to_benchmark"] = None
        out["implied_yield"] = coupon

    return out


# ── Main pipeline ────────────────────────────────────────────────────────────

def fetch_and_parse_abs_424b5(days_back: int = 7) -> int:
    """Discover → fetch → parse → store. Returns count of tranches written.

    Idempotent: filings already in the DB are skipped. Safe to re-run on the
    same window without producing duplicates.
    """
    filings = _discover_abs_424b5(days_back=days_back)
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        existing = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT accession_no FROM abs_new_issues"
            ).fetchall()
        }

    stored = 0
    for filing in filings:
        acc = filing["accession_no"]
        if acc in existing:
            continue

        logger.info("424B5: %s — %s", acc, filing["company_name"][:80])
        doc_url = _get_primary_document_url(acc, filing["company_name"])
        if not doc_url:
            logger.warning("Could not resolve document URL for %s", acc)
            continue

        try:
            resp = requests.get(doc_url, headers=HTML_HEADERS, timeout=30)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.error("424B5 fetch error [%s]: %s", acc, e)
            time.sleep(0.5)
            continue

        time.sleep(0.2)  # stay well under EDGAR 10 req/sec ceiling

        extracted = _extract_from_html(html, filing)

        # Claude fallback only when the structured path failed to find tranches.
        if extracted["confidence"] == "low":
            soup = BeautifulSoup(html, "html.parser")
            text_excerpt = soup.get_text(" ", strip=True)[:3000]
            claude_result = _claude_extract(text_excerpt)
            if claude_result and claude_result.get("tranches"):
                extracted["issuer_name"] = (
                    claude_result.get("issuer_name") or extracted["issuer_name"]
                )
                extracted["closing_date"] = (
                    claude_result.get("closing_date") or extracted["closing_date"]
                )
                extracted["tranches"] = claude_result["tranches"]
                extracted["confidence"] = "medium"  # Claude-assisted
                hint = claude_result.get("asset_class_hint", "") or ""
                extracted["asset_class"] = _classify_asset_class(
                    f"{extracted.get('issuer_name') or ''} {hint}"
                )

        if not extracted["tranches"]:
            logger.warning("No tranches extracted for %s", acc)
            existing.add(acc)  # avoid re-fetching next run
            continue

        total_size = sum(t.get("principal_amount") or 0 for t in extracted["tranches"])

        for tranche in extracted["tranches"]:
            tranche_with_spread = _compute_spread(tranche, filing["filed_at"])
            row_id = hashlib.sha256(
                f"{acc}_{tranche.get('class_name', '')}".encode()
            ).hexdigest()[:16]

            row = {
                "id": row_id,
                "accession_no": acc,
                "edgar_url": doc_url,
                "filing_date": filing["filed_at"],
                "issuer_name": extracted.get("issuer_name"),
                "depositor": extracted.get("depositor"),
                "servicer": extracted.get("servicer"),
                "asset_class": extracted.get("asset_class") or "esoteric_other",
                "closing_date": extracted.get("closing_date"),
                "cutoff_date": extracted.get("cutoff_date"),
                "total_deal_size": total_size,
                "underwriters": json.dumps(extracted.get("underwriters", [])),
                "parse_confidence": extracted.get("confidence", "low"),
                **{k: tranche_with_spread.get(k) for k in (
                    "class_name", "principal_amount", "coupon_type",
                    "coupon_rate", "floating_index", "floating_spread_bps",
                    "wal_years", "final_payment_date", "legal_maturity_date",
                    "price_to_public", "cusip", "rating_sp", "rating_moodys",
                    "rating_kbra", "rating_fitch", "benchmark",
                    "benchmark_rate", "spread_to_benchmark", "implied_yield",
                )},
                "fetched_at": now,
            }

            try:
                upsert_abs_tranche(row)
                stored += 1
            except Exception as e:
                logger.error("Store error [%s_%s]: %s", acc, tranche.get("class_name"), e)

        existing.add(acc)

    logger.info("424B5 parse complete — %d tranches stored", stored)
    return stored
