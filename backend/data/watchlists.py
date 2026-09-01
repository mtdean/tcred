"""
backend/data/watchlists.py — persisted saved searches + match engine.

A watchlist is a name + list of keywords + optional per-source filters. The
match engine searches:

  * articles               — title + snippet (case-insensitive substring; OR
                             across keywords). Honors min_score and the optional
                             news_categories filter.
  * edgar_filings          — company_name + description. Honors the optional
                             edgar_asset_classes / edgar_form_types filters.
  * regulatory_actions     — title + abstract. Honors regulatory_agencies.

Match semantics: OR across keywords (a record matches if ANY keyword appears
in the searched text); AND with filters (record must satisfy every active
filter). Both keyword and field comparisons are case-insensitive.

Storage: rows in `watchlists`. JSON-typed columns (keywords, filter arrays)
are kept as TEXT and (de)serialized in this module so callers see Python
lists.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from cache import db

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _watchlist_id() -> str:
    return "wl_" + secrets.token_hex(8)


# ── (de)serialize JSON columns ────────────────────────────────────────────

_JSON_COLUMNS = (
    "keywords",
    "news_categories",
    "edgar_asset_classes",
    "edgar_form_types",
    "regulatory_agencies",
)


def _row_to_dict(row) -> dict:
    d = dict(row)
    for col in _JSON_COLUMNS:
        val = d.get(col)
        if val is None or val == "":
            d[col] = [] if col == "keywords" else None
            continue
        try:
            d[col] = json.loads(val)
        except (TypeError, json.JSONDecodeError):
            d[col] = [] if col == "keywords" else None
    return d


def _serialize_field(value: Any, allow_none: bool = True) -> Optional[str]:
    if value is None and allow_none:
        return None
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, str):
        # Treat empty string as null for nullable fields.
        return value or (None if allow_none else "")
    return json.dumps(value)


def _validate_keywords(keywords: Any) -> list[str]:
    if not isinstance(keywords, list) or not keywords:
        raise ValueError("keywords must be a non-empty list of strings")
    cleaned: list[str] = []
    for kw in keywords:
        if not isinstance(kw, str):
            raise ValueError("keywords must be strings")
        s = kw.strip()
        if s:
            cleaned.append(s)
    if not cleaned:
        raise ValueError("keywords must contain at least one non-empty string")
    return cleaned


# ── CRUD ───────────────────────────────────────────────────────────────────

def create_watchlist(payload: dict) -> dict:
    """Insert a new watchlist; returns the stored row as a dict."""
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    keywords = _validate_keywords(payload.get("keywords"))
    now = _now_iso()
    row = {
        "id": _watchlist_id(),
        "name": name,
        "description": (payload.get("description") or None),
        "keywords": json.dumps(keywords),
        "news_categories": _serialize_field(payload.get("news_categories")),
        "edgar_asset_classes": _serialize_field(payload.get("edgar_asset_classes")),
        "edgar_form_types": _serialize_field(payload.get("edgar_form_types")),
        "regulatory_agencies": _serialize_field(payload.get("regulatory_agencies")),
        "min_score": int(payload.get("min_score") or 3),
        "created_at": now,
        "updated_at": now,
        "last_viewed_at": None,
    }
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO watchlists
               (id, name, description, keywords, news_categories,
                edgar_asset_classes, edgar_form_types, regulatory_agencies,
                min_score, created_at, updated_at, last_viewed_at)
               VALUES
               (:id, :name, :description, :keywords, :news_categories,
                :edgar_asset_classes, :edgar_form_types, :regulatory_agencies,
                :min_score, :created_at, :updated_at, :last_viewed_at)""",
            row,
        )
    return get_watchlist(row["id"])  # type: ignore[return-value]


def list_watchlists() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlists ORDER BY updated_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_watchlist(watchlist_id: str) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM watchlists WHERE id = ?", (watchlist_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_watchlist(watchlist_id: str, patch: dict) -> Optional[dict]:
    current = get_watchlist(watchlist_id)
    if current is None:
        return None

    updates: dict[str, Any] = {}
    if "name" in patch:
        name = (patch.get("name") or "").strip()
        if not name:
            raise ValueError("name cannot be empty")
        updates["name"] = name
    if "description" in patch:
        updates["description"] = patch.get("description") or None
    if "keywords" in patch:
        updates["keywords"] = json.dumps(_validate_keywords(patch["keywords"]))
    for key in ("news_categories", "edgar_asset_classes",
                "edgar_form_types", "regulatory_agencies"):
        if key in patch:
            updates[key] = _serialize_field(patch[key])
    if "min_score" in patch:
        updates["min_score"] = int(patch["min_score"])

    if not updates:
        return current

    updates["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = watchlist_id

    with db.get_conn() as conn:
        conn.execute(f"UPDATE watchlists SET {set_clause} WHERE id = :id", updates)
    if "keywords" in updates:
        # Cached verdicts answered "does this article match the OLD keywords" —
        # stale the moment the question changes.
        db.delete_watchlist_verifications(watchlist_id)
    return get_watchlist(watchlist_id)


def delete_watchlist(watchlist_id: str) -> bool:
    db.delete_watchlist_verifications(watchlist_id)
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))
        return cur.rowcount > 0


def mark_viewed(watchlist_id: str) -> Optional[dict]:
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE watchlists SET last_viewed_at = ? WHERE id = ?",
            (_now_iso(), watchlist_id),
        )
        if cur.rowcount == 0:
            return None
    return get_watchlist(watchlist_id)


# ── Match engine ───────────────────────────────────────────────────────────

def _keyword_re(keywords: list[str]) -> re.Pattern:
    """Build a case-insensitive regex matching ANY of the supplied keywords at
    word boundaries. Each keyword is regex-escaped, so phrase-like inputs
    (e.g. 'subprime auto') work as expected.

    Boundary matching is what keeps company-name watchlists usable: plain
    substring matching had "Ares" hitting every "shares" and "Affirm" hitting
    every "affirmed". Lookarounds (rather than \\b) so keywords that start or
    end with non-word chars ("S&P") still match.
    """
    escaped = [rf"(?<!\w){re.escape(k)}(?!\w)" for k in keywords if k]
    pattern = "|".join(escaped) if escaped else r"$.^"  # never matches if empty
    return re.compile(pattern, re.IGNORECASE)


def _match_articles(rx: re.Pattern, wl: dict, limit: int) -> list[dict]:
    from data.feeds import publisher_tier, publisher_tier_rank

    sql = (
        "SELECT id, feed_name, feed_category, title, snippet, url, "
        "published_at, fetched_at, relevance_score, relevance_tags, "
        "source_type, publisher "
        "FROM articles WHERE relevance_score >= ? AND duplicate_of IS NULL"
    )
    params: list = [wl.get("min_score") or 3]
    cats = wl.get("news_categories") or []
    if cats:
        placeholders = ",".join("?" * len(cats))
        sql += f" AND feed_category IN ({placeholders})"
        params.extend(cats)
    sql += " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 500"
    with db.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    matched: list[dict] = []
    for r in rows:
        haystack = f"{r['title'] or ''} | {r['snippet'] or ''}"
        if rx.search(haystack):
            d = dict(r)
            d["publisher_tier"] = publisher_tier(d.get("publisher"))
            matched.append(d)

    # Trusted publishers first, junk last; newest first within each tier
    # (stable two-pass sort). Sorting happens over the full candidate set so
    # a junk hit can't crowd a trusted one out of the limit window.
    matched.sort(
        key=lambda a: a.get("published_at") or a.get("fetched_at") or "",
        reverse=True,
    )
    matched.sort(key=lambda a: publisher_tier_rank(a.get("publisher")))
    return matched[:limit]


def _match_edgar(rx: re.Pattern, wl: dict, limit: int) -> list[dict]:
    sql = ("SELECT accession_no, company_name, form_type, filed_at, "
           "description, url, asset_class, issuance_type "
           "FROM edgar_filings WHERE 1=1")
    params: list = []
    cls = wl.get("edgar_asset_classes") or []
    if cls:
        placeholders = ",".join("?" * len(cls))
        sql += f" AND asset_class IN ({placeholders})"
        params.extend(cls)
    forms = wl.get("edgar_form_types") or []
    if forms:
        placeholders = ",".join("?" * len(forms))
        sql += f" AND form_type IN ({placeholders})"
        params.extend(forms)
    sql += " ORDER BY filed_at DESC LIMIT 2000"
    with db.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    matched: list[dict] = []
    for r in rows:
        haystack = f"{r['company_name'] or ''} | {r['description'] or ''}"
        if rx.search(haystack):
            matched.append(dict(r))
            if len(matched) >= limit:
                break
    return matched


def _match_regulatory(rx: re.Pattern, wl: dict, limit: int) -> list[dict]:
    sql = ("SELECT id, agency, action_type, title, abstract, publication_date, "
           "html_url, relevance_score FROM regulatory_actions WHERE 1=1")
    params: list = []
    agencies = wl.get("regulatory_agencies") or []
    if agencies:
        placeholders = ",".join("?" * len(agencies))
        sql += f" AND agency IN ({placeholders})"
        params.extend(agencies)
    sql += " ORDER BY publication_date DESC LIMIT 2000"
    with db.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    matched: list[dict] = []
    for r in rows:
        haystack = f"{r['title'] or ''} | {r['abstract'] or ''}"
        if rx.search(haystack):
            matched.append(dict(r))
            if len(matched) >= limit:
                break
    return matched


def run_watchlist(
    watchlist_id: str,
    per_source_limit: int = 100,
) -> Optional[dict]:
    """Execute a watchlist's saved search; return the matches grouped by source."""
    wl = get_watchlist(watchlist_id)
    if wl is None:
        return None
    rx = _keyword_re(wl.get("keywords") or [])
    articles = _match_articles(rx, wl, per_source_limit)
    edgar = _match_edgar(rx, wl, per_source_limit)
    regulatory = _match_regulatory(rx, wl, per_source_limit)

    # Attach cached Claude entity-verification verdicts (see watchlist_verify).
    verdicts = db.get_watchlist_verifications(
        watchlist_id, [a["id"] for a in articles]
    )
    for a in articles:
        v = verdicts.get(a["id"])
        a["verification"] = (
            {"verdict": v["verdict"], "reason": v["reason"]} if v else None
        )
    return {
        "watchlist": wl,
        "as_of": _now_iso(),
        "matches": {
            "articles": articles,
            "edgar_filings": edgar,
            "regulatory_actions": regulatory,
        },
        "counts": {
            "articles": len(articles),
            "edgar_filings": len(edgar),
            "regulatory_actions": len(regulatory),
            "total": len(articles) + len(edgar) + len(regulatory),
        },
    }
