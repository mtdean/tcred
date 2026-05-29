"""
backend/data/abs_reparse.py — re-extraction pass over stored abs_new_issues.

Background: the live parser populates many fields only when its structured
pandas extractor recognizes the prospectus table layout. Ratings are
particularly bad — 0 of 1,173 backfilled rows had any agency rating. WAL is
also missing on ~78% of rows.

This module re-walks each row, fetches the source 424B5 HTML (cached to disk
the first time we touch it, served from disk on every subsequent run), and
calls Claude to re-extract the per-tranche fields we care about. We UPDATE
only NULL fields, so anything the live parser got right is preserved.

After the re-extraction, `_compute_spread` runs against the merged row. For
fixed-rate tranches that now have both coupon and WAL, it computes
spread_to_benchmark = (coupon - matched-tenor UST) * 100 bps. Those rows are
flagged with spread_source='implied'. Floating tranches whose
floating_spread_bps was extracted are flagged 'parsed'.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

import anthropic

from cache.db import get_conn
from config import settings
from data.abs_parser import (
    CLAUDE_MODEL,
    HTML_HEADERS,
    _compute_spread,
    _get_claude_client,
)

logger = logging.getLogger(__name__)

HTML_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "store" / "abs_424b5"
HTML_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# Fields we'll merge from Claude's per-tranche output into the stored row.
# Order matters for _compute_spread: it needs coupon_type / coupon_rate /
# floating_spread_bps / wal_years before it can produce a spread.
_TRANCHE_FIELDS = [
    "coupon_type",
    "coupon_rate",
    "floating_index",
    "floating_spread_bps",
    "wal_years",
    "final_payment_date",
    "rating_sp",
    "rating_moodys",
    "rating_kbra",
    "rating_fitch",
]


def _cache_path(accession_no: str) -> Path:
    """Path on disk for this filing's cached HTML."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", accession_no)
    return HTML_CACHE_DIR / f"{safe}.html"


def _get_html(accession_no: str, edgar_url: str) -> Optional[str]:
    """Serve from disk if cached; else fetch from EDGAR and cache."""
    path = _cache_path(accession_no)
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("HTML cache read failed for %s: %s", accession_no, e)

    try:
        resp = requests.get(edgar_url, headers=HTML_HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.error("HTML fetch failed for %s: %s", accession_no, e)
        return None

    try:
        path.write_text(html, encoding="utf-8")
    except Exception as e:
        logger.warning("HTML cache write failed for %s: %s", accession_no, e)
    return html


# 424B5s are 500K-1M+ chars. Cover/summary has coupon + class names; WAL
# tables are typically 100-300K chars in, rating disclosures 350-400K. A flat
# top-of-document slice misses both. We instead build a focused excerpt:
# always include the cover, then add ~7K-char windows around each anchor we
# care about (WAL, AAA/Aaa, agency names). Cap at 90K chars (~22K tokens).
_REPARSE_EXCERPT_CHARS = 90_000
_REPARSE_COVER_CHARS = 30_000
_REPARSE_WINDOW_BEFORE = 2_000
_REPARSE_WINDOW_AFTER = 5_000

# Patterns to anchor section windows in the long-text body. We target the
# WAL disclosure (which holds the per-class number table) and the pricing
# table itself. Earlier rating anchors were dropped — 424B5s rarely disclose
# agency ratings inline; that data comes from FWP filings (joined separately).
#
# The first "weighted average life" hit is almost always risk-factor
# boilerplate ("delays may extend the WAL of the notes"). The pattern below
# requires a digit within a few words to anchor on the actual table.
_ANCHOR_PATTERNS = [
    re.compile(r"weighted[- ]average life[^.\n]{0,80}\d", re.IGNORECASE),
    re.compile(r"pricing\s+date", re.IGNORECASE),
    re.compile(r"initial principal\s+(?:amount|balance)", re.IGNORECASE),
]


def _build_focused_excerpt(text: str) -> str:
    """Cover (first 30K) + ~7K windows around each WAL/rating anchor.

    Overlapping windows are merged; total capped at _REPARSE_EXCERPT_CHARS.
    Each chunk is preceded by a small marker so Claude knows the layout
    isn't contiguous.
    """
    n = len(text)
    windows: list[tuple[int, int]] = [(0, min(_REPARSE_COVER_CHARS, n))]
    for pat in _ANCHOR_PATTERNS:
        m = pat.search(text, _REPARSE_COVER_CHARS)
        if m:
            start = max(_REPARSE_COVER_CHARS, m.start() - _REPARSE_WINDOW_BEFORE)
            end = min(n, m.end() + _REPARSE_WINDOW_AFTER)
            windows.append((start, end))

    windows.sort()
    merged: list[list[int]] = []
    for s, e in windows:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    parts: list[str] = []
    total = 0
    for s, e in merged:
        budget = _REPARSE_EXCERPT_CHARS - total
        if budget <= 0:
            break
        chunk = text[s:e][:budget]
        parts.append(f"--- chars {s}–{s + len(chunk)} ---\n{chunk}")
        total += len(chunk)

    return "\n\n".join(parts)

_REPARSE_PROMPT = """You are extracting structured pricing data from an ABS 424B5 prospectus supplement.

Return ONLY valid JSON in the schema below. No commentary, no markdown fences.
Use null when a value isn't disclosed.

Focus on the per-tranche pricing table and the weighted-average-life disclosure.
424B5s rarely show agency ratings inline — those come from separate FWPs — so
do NOT invent or guess ratings here; just leave them out of the schema.

For each offered class:
  - wal_years as a decimal (e.g. 1.07, 2.42). The "Weighted Average Life" you
    want appears in a table with numeric years per class, NOT the risk-factor
    sentences that mention WAL in passing.
  - coupon_rate as a percent for fixed tranches (e.g. 4.54 for 4.54%).
  - floating_index ("SOFR" / "LIBOR") and floating_spread_bps (the printed
    margin in bps) for floating tranches.
  - Skip retained / "not offered" / "collateral interest" rows.

Schema:
{
  "issuer_name": "string or null",
  "tranches": [
    {
      "class_name": "A-1",
      "coupon_type": "fixed | floating | null",
      "coupon_rate": 4.54,
      "floating_index": "SOFR | LIBOR | null",
      "floating_spread_bps": null,
      "wal_years": 1.07
    }
  ]
}

DOCUMENT EXCERPT:
"""


def _extract_json_object(raw: str) -> Optional[str]:
    """Find the outermost {...} JSON object inside a Claude reply.

    Claude sometimes preambles the JSON with a reasoning explanation ("I need
    to find..."). We locate the first '{' and walk to its matching '}',
    tracking string state so braces inside string literals don't fool us.
    """
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None  # unterminated


def _reparse_claude(full_text: str) -> Optional[dict]:
    """Build a focused excerpt and call Claude with the reparse prompt."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    excerpt = _build_focused_excerpt(full_text)
    try:
        client = _get_claude_client()
        response = client.messages.create(
            model=CLAUDE_MODEL,
            # Up from 2000 — Claude sometimes preambles with reasoning and a
            # large tranche array (5-10 classes × ~120 chars JSON each) blew
            # past the 2000 limit and truncated the JSON.
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": _REPARSE_PROMPT + excerpt,
            }],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        # Strip any reasoning preamble before the JSON object.
        json_str = _extract_json_object(raw)
        if json_str is None:
            logger.error("reparse Claude no JSON object in reply (len=%d)", len(raw))
            return None
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("reparse Claude JSON parse error: %s", e)
        return None
    except Exception as e:
        logger.error("reparse Claude error: %s", e)
        return None


def _canonical_class(s: str) -> str:
    """Canonical form of a tranche class label for cross-source matching.

    Strips boilerplate words ('class', 'notes', 'certificates') then keeps
    only alphanumerics, uppercased. So 'Class A-2 Notes' → 'A2',
    'Class A-2' → 'A2', 'A-2' → 'A2' all collapse to one key.

    Note: this still treats 'Collateral Interest' → 'COLLATERALINTEREST'
    which won't equal Claude's 'A' or 'B' — exactly the desired behavior.
    """
    if not s:
        return ""
    t = re.sub(r"\b(?:class|notes?|certs?|certificates?)\b", "", s, flags=re.IGNORECASE)
    return re.sub(r"[^A-Za-z0-9]", "", t).upper()


def _match_tranche(
    target_class: str, tranches: list[dict]
) -> Optional[dict]:
    """Pick the Claude-returned tranche whose class_name matches the stored row.

    Canonical-form match: strips 'Class' / 'Notes' wrappers and non-alphanumerics
    before comparing, so 'Class A-2 Notes' (DB) and 'A-2' (Claude) collapse to
    the same key. Earlier substring fallback was removed — it false-matched
    single-letter "A" against 'CollateralInterest'.
    """
    if not tranches:
        return None
    norm_target = _canonical_class(target_class)
    if not norm_target:
        return None
    for t in tranches:
        if _canonical_class(t.get("class_name") or "") == norm_target:
            return t
    return None


def _merge_nulls(stored: dict, new: dict, fields: list[str]) -> dict:
    """Return a copy of `stored` with any NULL field replaced by new[field]."""
    out = dict(stored)
    for f in fields:
        if out.get(f) is None and new.get(f) is not None:
            out[f] = new[f]
    return out


def reparse_row(row: dict) -> dict:
    """Re-extract one stored row. Returns the merged dict (caller persists).

    `row` is a dict copy of the abs_new_issues row to reparse. The returned
    dict carries the same shape with previously-NULL fields filled in where
    Claude found them, and spread_to_benchmark/benchmark/spread_source
    re-derived through _compute_spread.
    """
    html = _get_html(row["accession_no"], row["edgar_url"])
    if not html:
        return row  # caller will skip — nothing extracted

    # 60K excerpt (vs the live parser's 3K) — captures the pricing table and
    # ratings disclosure which live deeper than the cover page.
    soup = BeautifulSoup(html, "html.parser")
    excerpt = soup.get_text(" ", strip=True)
    claude = _reparse_claude(excerpt)
    if not claude:
        return row

    # Deal-level fields. Only fill if currently NULL.
    deal_updates = {
        "issuer_name": claude.get("issuer_name"),
        "closing_date": claude.get("closing_date"),
        "cutoff_date": claude.get("cutoff_date"),
    }
    merged = dict(row)
    for k, v in deal_updates.items():
        if merged.get(k) is None and v:
            merged[k] = v

    # Tranche-level fields.
    tranche = _match_tranche(row.get("class_name", ""), claude.get("tranches", []))
    if tranche:
        merged = _merge_nulls(merged, tranche, _TRANCHE_FIELDS)

    # Re-derive spread. _compute_spread takes a tranche-shaped dict and
    # returns it with benchmark / benchmark_rate / spread_to_benchmark /
    # implied_yield filled in based on coupon_type + wal_years + filing_date
    # against the cached treasury curve.
    pre_spread = merged.get("spread_to_benchmark")
    computed = _compute_spread(merged, merged.get("filing_date") or "")
    # Only overwrite spread_to_benchmark if we didn't already have one and the
    # computation produced one. Preserves real prospectus-spread parses.
    if pre_spread is None and computed.get("spread_to_benchmark") is not None:
        merged["spread_to_benchmark"] = computed["spread_to_benchmark"]
        merged["benchmark"] = computed.get("benchmark") or merged.get("benchmark")
        merged["benchmark_rate"] = (
            computed.get("benchmark_rate")
            if merged.get("benchmark_rate") is None
            else merged.get("benchmark_rate")
        )
        # Floating tranches whose spread came from a printed margin are
        # 'parsed'; fixed tranches whose spread was derived from coupon -
        # treasury are 'implied'. _compute_spread distinguishes via coupon_type.
        merged["spread_source"] = (
            "parsed" if merged.get("coupon_type") == "floating" else "implied"
        )
    if merged.get("implied_yield") is None and computed.get("implied_yield") is not None:
        merged["implied_yield"] = computed["implied_yield"]

    return merged


def _persist_row(merged: dict, original: dict) -> bool:
    """UPDATE the row in abs_new_issues with any newly-filled fields.
    Returns True if at least one field changed."""
    changed = {
        k: v for k, v in merged.items()
        if v != original.get(k) and k != "id"
    }
    if not changed:
        return False

    set_clause = ", ".join(f"{k} = :{k}" for k in changed)
    params = dict(changed)
    params["id"] = original["id"]
    with get_conn() as conn:
        conn.execute(f"UPDATE abs_new_issues SET {set_clause} WHERE id = :id", params)
    return True


def apply_implied_spread() -> int:
    """Compute implied spread for rows that have coupon + WAL but no spread.

    Standalone pass — used after the FWP-join script (which populates WAL on
    some rows but doesn't touch the spread column) and after a reparse that
    fills WAL but Claude didn't get a spread directly. Idempotent: only
    touches rows where spread_to_benchmark IS NULL.

    Fixed coupon: spread = (coupon - matched-tenor UST) * 100 bps.
    Floating tranches are not handled here — their spread is the printed
    margin, which the parser stores directly.

    Returns rows updated.
    """
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT * FROM abs_new_issues
            WHERE spread_to_benchmark IS NULL
              AND wal_years IS NOT NULL
              AND coupon_rate IS NOT NULL
              AND coupon_type = 'fixed'
            """
        ).fetchall()]

    n = 0
    with get_conn() as conn:
        for row in rows:
            computed = _compute_spread(row, row.get("filing_date") or "")
            spread = computed.get("spread_to_benchmark")
            if spread is None:
                continue
            conn.execute(
                """
                UPDATE abs_new_issues
                   SET spread_to_benchmark = :spread,
                       benchmark = :benchmark,
                       benchmark_rate = :benchmark_rate,
                       spread_source = 'implied'
                 WHERE id = :id
                """,
                {
                    "spread": spread,
                    "benchmark": computed.get("benchmark"),
                    "benchmark_rate": computed.get("benchmark_rate"),
                    "id": row["id"],
                },
            )
            n += 1
    return n


def reparse_all(
    limit: Optional[int] = None,
    only_missing_wal: bool = True,
) -> dict:
    """Walk abs_new_issues rows, re-extract, persist updates.

    Returns {"scanned": n, "updated": n, "errors": n}.

    By default only processes rows whose wal_years is NULL (the gating field
    for implied-spread coverage). Set only_missing_wal=False to walk every row
    — useful for re-running with a new prompt.
    """
    # credit_card master trusts and similar revolving structures don't have a
    # contractual WAL — the tranche has no scheduled amortization, so there's
    # nothing for Claude to extract. Skip those asset classes to keep the cost
    # focused on rows that can actually be improved.
    _SKIP_FOR_WAL = ("credit_card",)
    with get_conn() as conn:
        sql = "SELECT * FROM abs_new_issues"
        clauses: list[str] = []
        if only_missing_wal:
            clauses.append("wal_years IS NULL")
            placeholders = ",".join("?" * len(_SKIP_FOR_WAL))
            clauses.append(f"(asset_class IS NULL OR asset_class NOT IN ({placeholders}))")
            params: list = list(_SKIP_FOR_WAL)
        else:
            params = []
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY filing_date ASC, id ASC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    if limit:
        rows = rows[:limit]

    scanned = updated = errors = 0
    if not settings.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set — reparse cannot call Claude")
        return {"scanned": 0, "updated": 0, "errors": 0}

    for i, row in enumerate(rows, 1):
        scanned += 1
        try:
            merged = reparse_row(row)
            if _persist_row(merged, row):
                updated += 1
        except Exception as e:
            errors += 1
            logger.error("reparse row %s failed: %s", row.get("accession_no"), e)
        if i % 50 == 0:
            logger.info(
                "reparse progress: %d/%d scanned, %d updated, %d errors",
                i, len(rows), updated, errors,
            )

    logger.info(
        "reparse complete: %d scanned, %d updated, %d errors",
        scanned, updated, errors,
    )
    return {"scanned": scanned, "updated": updated, "errors": errors}
