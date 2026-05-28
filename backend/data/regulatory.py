"""
backend/data/regulatory.py — Regulatory Flow Monitor (Phase 7, Module 2).

Pipeline:
  1. fetch_regulatory_actions(days_back):  Federal Register API + agency RSS,
                                            token-free, callable from scheduler.
  2. score_regulatory_actions(limit):      Claude relevance scoring — MANUAL only,
                                            triggered via POST /regulatory/score.
  3. get_regulatory_actions(...):          filtered list query for the UI.

Hard constraint: data fetch never spends Claude tokens. Scoring is a separate
endpoint so the daily scheduler can keep pulling sources without burning tokens;
the user pays only when they explicitly press SCORE in the UI.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
import feedparser
import requests

from cache.db import get_conn
from config import load_data_sources, settings

logger = logging.getLogger(__name__)

# Match the Claude model used elsewhere in the codebase (abs_parser.py).
CLAUDE_MODEL = "claude-sonnet-4-6"

# Federal Register document types we want. Press releases come in via RSS.
FR_DOC_TYPES = ["RULE", "PROPOSED_RULE", "NOTICE"]

RELEVANCE_SYSTEM_PROMPT = """You are a regulatory analyst specializing in consumer credit,
asset-backed securities, and financial regulation.

Score each regulatory action for relevance to these topics on a 1-5 scale:
  5 = Directly affects ABS/securitization, consumer credit underwriting, capital treatment of loans/securities, fair lending, data furnishing, or fintech lending
  4 = Affects bank lending broadly, financial institution capital/liquidity, credit reporting, or payment systems
  3 = Financial regulation adjacent — affects market structure, disclosure, or investor protections
  2 = Tangentially related — general banking or financial services
  1 = Not relevant — unrelated to credit or financial markets

Return up to 3 tags from:
[consumer_credit, abs_securitization, capital_requirements, fair_lending,
 data_privacy, credit_reporting, fintech_regulation, bank_lending,
 liquidity, market_structure, enforcement, interest_rates]

Return ONLY a JSON array, no preamble:
[{"id": "...", "score": N, "tags": ["tag1", "tag2"]}]"""


# ── Federal Register fetch ───────────────────────────────────────────────────

def _fetch_federal_register(agencies: list[dict], days_back: int) -> list[dict]:
    """Pull recent RULE / PROPOSED_RULE / NOTICE documents per agency."""
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    cfg = load_data_sources()
    fr_url = cfg["regulatory"]["federal_register_api"]
    results: list[dict] = []

    for ag in agencies:
        slug = ag["slug"]
        short = ag["short"]
        params = {
            "conditions[agencies][]": slug,
            "conditions[publication_date][gte]": since,
            "conditions[type][]": FR_DOC_TYPES,
            "fields[]": [
                "document_number", "title", "abstract",
                "publication_date", "effective_on",
                "comments_close_on", "docket_ids",
                "cfr_references", "html_url", "pdf_url", "type",
            ],
            "per_page": 50,
            "order": "newest",
        }
        try:
            resp = requests.get(
                fr_url,
                params=params,
                headers={"User-Agent": settings.EDGAR_USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("Federal Register error [%s]: %s", slug, e)
            time.sleep(0.2)
            continue

        for doc in data.get("results", []):
            doc_num = doc.get("document_number") or ""
            if not doc_num:
                continue
            # CFR refs arrive as a list of {title, part} dicts; flatten to strings.
            cfr_refs = []
            for ref in doc.get("cfr_references") or []:
                if isinstance(ref, dict):
                    t = ref.get("title")
                    p = ref.get("part")
                    if t and p:
                        cfr_refs.append(f"{t} CFR {p}")
                elif isinstance(ref, str):
                    cfr_refs.append(ref)

            results.append({
                "id": doc_num,
                "agency": short,
                "action_type": doc.get("type") or "NOTICE",
                "title": (doc.get("title") or "")[:500],
                "abstract": (doc.get("abstract") or "")[:2000],
                "publication_date": doc.get("publication_date") or None,
                "effective_date": doc.get("effective_on") or None,
                "comment_close_date": doc.get("comments_close_on") or None,
                "document_number": doc_num,
                "docket_id": json.dumps(doc.get("docket_ids") or []),
                "cfr_references": json.dumps(cfr_refs),
                "html_url": doc.get("html_url") or None,
                "pdf_url": doc.get("pdf_url") or None,
            })

        time.sleep(0.2)  # be polite to federalregister.gov

    return results


# ── Agency RSS fetch ─────────────────────────────────────────────────────────

def _rss_id(agency: str, link: str) -> str:
    """Deterministic id so re-runs of the same RSS entry collapse on INSERT OR IGNORE."""
    return hashlib.sha256(f"rss_{agency}_{link}".encode()).hexdigest()[:16]


def _rss_pub_date(entry) -> Optional[str]:
    """Best-effort ISO date from a feedparser entry (YYYY-MM-DD)."""
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                pass
    # Fall back to the raw string if present.
    raw = entry.get("published") or entry.get("updated") or ""
    return raw[:10] if raw else None


def _fetch_agency_rss(rss_feeds: dict) -> list[dict]:
    """Pull the last ~20 entries from each agency press-release feed."""
    results: list[dict] = []

    for agency, url in rss_feeds.items():
        try:
            # feedparser is sync; pass UA via request_headers (works for HTTPS feeds).
            feed = feedparser.parse(
                url,
                request_headers={"User-Agent": settings.EDGAR_USER_AGENT},
            )
        except Exception as e:
            logger.error("Agency RSS error [%s]: %s", agency, e)
            continue

        for entry in (getattr(feed, "entries", []) or [])[:20]:
            link = entry.get("link") or ""
            if not link:
                continue
            title = (entry.get("title") or "")[:500]
            summary = (entry.get("summary") or entry.get("description") or "")[:2000]
            results.append({
                "id": _rss_id(agency, link),
                "agency": agency,
                "action_type": "PRESS_RELEASE",
                "title": title,
                "abstract": summary,
                "publication_date": _rss_pub_date(entry),
                "effective_date": None,
                "comment_close_date": None,
                "document_number": None,
                "docket_id": None,
                "cfr_references": None,
                "html_url": link,
                "pdf_url": None,
            })

        time.sleep(0.2)

    return results


# ── Public API: fetch (token-free) ───────────────────────────────────────────

def fetch_regulatory_actions(days_back: int = 7) -> int:
    """Fetch + persist new regulatory actions. NEVER calls Claude.

    relevance_score and relevance_tags stay NULL until the user presses SCORE.
    """
    cfg = load_data_sources().get("regulatory") or {}
    agencies = cfg.get("fr_agencies") or []
    rss_feeds = cfg.get("agency_rss") or {}

    actions = _fetch_federal_register(agencies, days_back=days_back)
    actions += _fetch_agency_rss(rss_feeds)
    if not actions:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    cols = (
        "id", "agency", "action_type", "title", "abstract",
        "publication_date", "effective_date", "comment_close_date",
        "document_number", "docket_id", "cfr_references",
        "html_url", "pdf_url", "fetched_at",
    )
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)

    with get_conn() as conn:
        for action in actions:
            row = {c: action.get(c) for c in cols}
            row["fetched_at"] = now
            try:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO regulatory_actions ({col_list}) "
                    f"VALUES ({placeholders})",
                    row,
                )
                stored += cur.rowcount
            except Exception as e:
                logger.debug("Regulatory insert skip [%s]: %s", row["id"], e)

    logger.info("Regulatory: %d new actions stored", stored)
    return stored


# ── Public API: score (Claude, MANUAL only) ──────────────────────────────────

_claude_client: anthropic.Anthropic | None = None


def _get_claude_client() -> anthropic.Anthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _claude_client


def _build_user_prompt(batch: list[dict]) -> str:
    lines = []
    for a in batch:
        lines.append(
            f'ID:{a["id"]} | AGENCY:{a["agency"]} | TYPE:{a["action_type"]} | '
            f'TITLE:{a["title"]} | ABSTRACT:{(a.get("abstract") or "")[:300]}'
        )
    return "Score these regulatory actions:\n\n" + "\n".join(lines)


def score_regulatory_actions(limit: int = 100) -> int:
    """Score unscored rows with Claude. Manual-only — called from the SCORE button.

    Pulls oldest-first by publication_date DESC so the most recent unscored
    actions get attention first, batches up to 20 per Claude call.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("Regulatory score: ANTHROPIC_API_KEY not set; skipping")
        return 0

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, agency, action_type, title, abstract
               FROM regulatory_actions
               WHERE relevance_score IS NULL
               ORDER BY publication_date DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    actions = [dict(r) for r in rows]
    if not actions:
        return 0

    client = _get_claude_client()
    batch_size = 20
    scored_count = 0

    for i in range(0, len(actions), batch_size):
        batch = actions[i:i + batch_size]
        prompt = _build_user_prompt(batch)

        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                # ~50 output tokens per action; budget for full JSON.
                max_tokens=4000,
                system=RELEVANCE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            scored = json.loads(raw.strip())
        except json.JSONDecodeError as e:
            logger.error("Regulatory scorer JSON parse error: %s", e)
            continue
        except Exception as e:
            logger.error("Regulatory scorer API error: %s", e)
            continue

        with get_conn() as conn:
            for item in scored:
                try:
                    aid = item.get("id")
                    score = int(item.get("score", 0))
                    tags = json.dumps(item.get("tags") or [])
                    if not aid or score < 1 or score > 5:
                        continue
                    conn.execute(
                        "UPDATE regulatory_actions "
                        "SET relevance_score = ?, relevance_tags = ? "
                        "WHERE id = ?",
                        (score, tags, aid),
                    )
                    scored_count += 1
                except Exception as e:
                    logger.warning("Failed to update regulatory %s: %s", item.get("id"), e)

    logger.info("Regulatory: scored %d actions", scored_count)
    return scored_count


# ── Public API: query (for the UI) ───────────────────────────────────────────

def get_regulatory_actions(
    agency: Optional[str] = None,
    action_type: Optional[str] = None,
    min_score: int = 1,
    days_back: int = 90,
    limit: int = 100,
) -> list[dict]:
    """Recent regulatory actions matching the filter.

    Unscored rows (relevance_score IS NULL) are always returned so the user
    sees them before pressing SCORE; once scored they're filtered by min_score.
    """
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    sql = """
        SELECT *
        FROM regulatory_actions
        WHERE (publication_date >= ? OR publication_date IS NULL)
          AND (relevance_score >= ? OR relevance_score IS NULL)
    """
    params: list = [since, min_score]

    if agency:
        sql += " AND agency = ?"
        params.append(agency)
    if action_type:
        sql += " AND action_type = ?"
        params.append(action_type)

    sql += " ORDER BY publication_date DESC, fetched_at DESC LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
