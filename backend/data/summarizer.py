"""
backend/data/summarizer.py — Claude-written summaries of full-text articles.

Only fires for articles that (a) shipped a full body in their feed
(content_text set by data/feeds.py) and (b) already scored >= min_score in the
relevance classifier. Headline-only items (Google News, Bloomberg RSS) have no
body to summarize and are never selected, so this stage costs tokens only for
the full-content newsletters/blogs worth reading.

Runs after classification in the feeds job and the manual REFRESH path.
"""

import json
import logging
import os

import anthropic

from cache.db import get_unsummarized_articles, update_article_summary

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

SUMMARIZER_MODEL = "claude-sonnet-4-6"

# Bodies are capped so a batch of MAX_BATCH stays well inside the context
# window even for long newsletters.
MAX_BODY_CHARS = 6000
MAX_BATCH = 8

SYSTEM_PROMPT = """You summarize articles for a macro/credit/structured-finance \
intelligence dashboard. The reader is a credit/securitization professional.

For each article, write a 2-3 sentence summary:
  - Lead with the core claim or finding, not the setup.
  - Keep concrete numbers (rates, spreads, growth figures, deal sizes).
  - Plain prose. No "The article discusses..." framing.

Return ONLY a JSON array. Each element:
{"id": "<article_id>", "summary": "<2-3 sentences>"}

No preamble. No explanation. Raw JSON only."""


def _build_user_prompt(articles: list[dict]) -> str:
    parts = []
    for a in articles:
        body = (a.get("content_text") or "")[:MAX_BODY_CHARS]
        parts.append(f'ID:{a["id"]}\nTITLE:{a["title"]}\nBODY:\n{body}')
    return "Summarize these articles:\n\n" + "\n\n---\n\n".join(parts)


async def summarize_articles(batch_size: int = MAX_BATCH, min_score: int = 4) -> int:
    """Summarize a batch of full-text, high-relevance articles. Returns count."""
    articles = get_unsummarized_articles(min_score=min_score, limit=batch_size)
    if not articles:
        return 0

    prompt = _build_user_prompt(articles)

    try:
        response = client.messages.create(
            model=SUMMARIZER_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if present (same defense as the classifier).
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        summarized = json.loads(raw)

    except json.JSONDecodeError as e:
        logger.error(f"Summarizer JSON parse error: {e}")
        return 0
    except Exception as e:
        logger.error(f"Summarizer API error: {e}")
        return 0

    requested_ids = {a["id"] for a in articles}
    count = 0
    for item in summarized:
        try:
            if item["id"] not in requested_ids:
                continue  # hallucinated id — don't touch other rows
            summary = str(item["summary"]).strip()
            if not summary:
                continue
            update_article_summary(item["id"], summary)
            count += 1
        except Exception as e:
            logger.warning(f"Failed to store summary for {item.get('id')}: {e}")

    logger.info(f"Summarized {count} articles")
    return count
