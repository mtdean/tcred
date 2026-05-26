"""
backend/data/digest.py — on-demand AI digest of recent high-relevance news.

Pulls top-scored articles from a recent window and asks Claude to write a
concise prose briefing for a macro/credit/structured-finance reader.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import anthropic

from cache.db import get_conn

logger = logging.getLogger(__name__)

# Sonnet 4.6 — fast and cheap, plenty capable for headline synthesis.
DIGEST_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a markets analyst writing a terminal-style intelligence \
briefing for a reader focused on macroeconomics, credit markets, structured finance \
(ABS/securitization), and fintech.

Given a list of recent news headlines and snippets, write a tight prose digest:
  - Lead with the single most important development.
  - Group related items into 2-4 short thematic paragraphs (e.g. rates/Fed, credit \
spreads, consumer credit, structured finance/ABS, fintech).
  - Be specific and quantitative where the source allows. No hedging filler.
  - Do NOT invent facts not present in the provided items.
  - 200-350 words. Plain prose, no markdown headers, no bullet lists."""


class DigestError(Exception):
    """Raised when a digest cannot be produced (e.g. missing key, no articles)."""


def _fetch_window(hours_back: int, min_score: int) -> tuple[list[dict], str, str]:
    """Return (articles, oldest_iso, newest_iso) for the requested window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, feed_name, feed_category, title, snippet, published_at, fetched_at,
                   relevance_score
            FROM articles
            WHERE relevance_score >= ?
              AND COALESCE(published_at, fetched_at) >= ?
            ORDER BY relevance_score DESC, COALESCE(published_at, fetched_at) DESC
            LIMIT 60
            """,
            (min_score, cutoff),
        ).fetchall()
    articles = [dict(r) for r in rows]
    if articles:
        dates = [a.get("published_at") or a.get("fetched_at") for a in articles]
        return articles, min(dates), max(dates)
    return articles, cutoff, datetime.now(timezone.utc).isoformat()


def _build_prompt(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        snippet = (a.get("snippet") or "")[:240]
        lines.append(
            f'[{a["feed_category"]}/{a["relevance_score"]}] {a["title"]} — {snippet}'
        )
    return "Recent items:\n\n" + "\n".join(lines)


def generate_digest(hours_back: int = 24, min_score: int = 4) -> dict:
    """
    Generate a prose digest of recent high-relevance articles.

    Raises DigestError if the Anthropic key is missing or there are no articles.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise DigestError("ANTHROPIC_API_KEY not set — cannot generate digest.")

    articles, oldest, newest = _fetch_window(hours_back, min_score)
    if not articles:
        raise DigestError(
            f"No articles scored >= {min_score} in the last {hours_back}h. "
            "Articles must be classified first (needs ANTHROPIC_API_KEY + a refresh)."
        )

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    response = client.messages.create(
        model=DIGEST_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(articles)}],
    )
    summary = response.content[0].text.strip()

    return {
        "summary": summary,
        "article_count": len(articles),
        "hours_back": hours_back,
        "min_score": min_score,
        "date_range": {"from": oldest, "to": newest},
        "model": DIGEST_MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
