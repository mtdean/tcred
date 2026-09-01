"""
backend/data/watchlist_verify.py — Claude entity verification for watchlist hits.

Keyword matching (even word-boundary matching) can't tell Ares Management from
any other Ares, or a real Carvana story from a stock-promo rehash. This module
batch-asks Claude whether each matched article is genuinely about the
watchlist's subject and caches the verdict per (watchlist, article) in
`watchlist_verifications` — so each article is judged at most once per
keyword set (the cache is wiped when a watchlist's keywords change).

Fires only from the manual VERIFY button (POST /api/watchlists/{id}/verify).
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import anthropic

from cache.db import upsert_watchlist_verification

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

VERIFY_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You verify whether news articles genuinely concern the subject of a \
financial watchlist. The watchlist's keywords may be company names or topics; keyword \
matching has already fired, so your job is to reject false positives:
  - a keyword matching a different entity with a similar name
  - the subject appearing only incidentally (a ticker list, a passing mention)
  - stock-promo / press-release rehash with no substantive news about the subject

An article is a match if a reader tracking this watchlist would want to see it.

Return ONLY a JSON array. Each element:
{"id": "<article_id>", "match": true|false, "reason": "<one short clause>"}

No preamble. No explanation. Raw JSON only."""


def _build_user_prompt(wl: dict, articles: list[dict]) -> str:
    head = [
        f"WATCHLIST: {wl['name']}",
        f"KEYWORDS: {', '.join(wl.get('keywords') or [])}",
    ]
    if wl.get("description"):
        head.append(f"DESCRIPTION: {wl['description']}")
    lines = []
    for a in articles:
        snippet = (a.get("snippet") or "")[:300]
        pub = a.get("publisher") or a.get("feed_name") or ""
        lines.append(
            f'ID:{a["id"]} | SOURCE:{pub} | TITLE:{a["title"]} | SNIPPET:{snippet}'
        )
    return "\n".join(head) + "\n\nVerify these articles:\n\n" + "\n".join(lines)


def verify_watchlist(watchlist_id: str, max_articles: int = 40) -> Optional[dict]:
    """Judge unverified article matches for one watchlist. Returns summary
    counts, or None when the watchlist doesn't exist.
    """
    from data.watchlists import get_watchlist, run_watchlist

    wl = get_watchlist(watchlist_id)
    if wl is None:
        return None

    result = run_watchlist(watchlist_id)
    articles = (result or {}).get("matches", {}).get("articles", [])
    pending = [a for a in articles if a.get("verification") is None][:max_articles]
    if not pending:
        return {"verified": 0, "matches": 0, "rejects": 0, "pending": 0}

    prompt = _build_user_prompt(wl, pending)

    try:
        response = client.messages.create(
            model=VERIFY_MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        verdicts = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Watchlist verify JSON parse error: {e}")
        return {"verified": 0, "matches": 0, "rejects": 0, "pending": len(pending)}
    except Exception as e:
        logger.error(f"Watchlist verify API error: {e}")
        return {"verified": 0, "matches": 0, "rejects": 0, "pending": len(pending)}

    pending_ids = {a["id"] for a in pending}
    now = datetime.now(timezone.utc).isoformat()
    n_match = n_reject = 0
    for item in verdicts:
        try:
            if item["id"] not in pending_ids:
                continue  # hallucinated id
            verdict = "match" if bool(item["match"]) else "reject"
            upsert_watchlist_verification({
                "watchlist_id": watchlist_id,
                "article_id": item["id"],
                "verdict": verdict,
                "reason": str(item.get("reason") or "")[:300] or None,
                "verified_at": now,
            })
            if verdict == "match":
                n_match += 1
            else:
                n_reject += 1
        except Exception as e:
            logger.warning(f"Failed to store verdict for {item.get('id')}: {e}")

    total = n_match + n_reject
    logger.info(
        "Watchlist %s verify: %d judged (%d match / %d reject)",
        watchlist_id, total, n_match, n_reject,
    )
    return {
        "verified": total,
        "matches": n_match,
        "rejects": n_reject,
        "pending": len(pending) - total,
    }
