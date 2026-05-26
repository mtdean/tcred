"""
backend/data/classifier.py — Claude-powered relevance scoring.

Batches unscored articles and asks Claude to score 1-5 on relevance to:
  macro, credit, structured finance, fintech, data science.

Runs after each feed fetch cycle.
"""

import json
import logging
import os
from typing import Optional

import anthropic

from cache.db import get_unscored_articles, update_article_relevance

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """You are a relevance classifier for a financial markets intelligence dashboard.

Score each article on a scale of 1-5 for relevance to these topics:
  - Macroeconomics and monetary policy (Fed, rates, inflation, GDP, employment)
  - Credit markets (consumer credit, corporate credit, private credit, distressed, CLOs)
  - Structured finance and securitization (ABS, RMBS, CMBS, equipment finance, esoteric ABS)
  - Fintech (lending platforms, payments, banking-as-a-service, regulation)
  - Quantitative finance and data science applied to finance

Scoring rubric:
  5 = Directly and substantively covers one or more of the above topics
  4 = Primarily finance/econ, touches the above topics
  3 = Finance/econ adjacent or high-level overview
  2 = Loosely related (general business, tech with minor finance angle)
  1 = Irrelevant (sports, entertainment, unrelated politics, etc.)

Also return up to 3 topic tags from: [macro, rates, credit, private_credit, abs, structured_finance, 
consumer_credit, fintech, regulation, data_science, distressed, clо, equipment_finance, esoteric]

Return ONLY a JSON array. Each element:
{"id": "<article_id>", "score": <int 1-5>, "tags": ["tag1", "tag2"]}

No preamble. No explanation. Raw JSON only."""


def _build_user_prompt(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        snippet = (a.get("snippet") or "")[:200]
        lines.append(f'ID:{a["id"]} | TITLE:{a["title"]} | SNIPPET:{snippet}')
    return "Score these articles:\n\n" + "\n".join(lines)


async def classify_articles(batch_size: int = 30) -> int:
    """Score unscored articles in batches. Returns count scored."""
    articles = get_unscored_articles(limit=batch_size)
    if not articles:
        return 0

    prompt = _build_user_prompt(articles)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            # ~40 output tokens per article; budget generously so the JSON
            # array is never truncated mid-string (was 1000 → truncated at 50).
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        scored = json.loads(raw)

    except json.JSONDecodeError as e:
        logger.error(f"Classifier JSON parse error: {e}")
        return 0
    except Exception as e:
        logger.error(f"Classifier API error: {e}")
        return 0

    count = 0
    for item in scored:
        try:
            update_article_relevance(
                article_id=item["id"],
                score=int(item["score"]),
                tags=json.dumps(item.get("tags", [])),
            )
            count += 1
        except Exception as e:
            logger.warning(f"Failed to update article {item.get('id')}: {e}")

    logger.info(f"Classified {count} articles")
    return count
