"""
backend/data/feeds.py — RSS/Atom feed fetcher.

Responsibilities:
  - Poll all feeds in feeds.yaml
  - Deduplicate by URL hash
  - Run startup health check and persist results
  - Store raw items in the articles table (unscored)
"""

import asyncio
import hashlib
import html
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import feedparser

from cache.db import upsert_article, upsert_feed_health
from config import load_feeds

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "SituationMonitor/0.1 (personal dashboard)",
}

TIMEOUT = aiohttp.ClientTimeout(total=15)


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# Map a feed_name prefix to a granular source slug. Order matters: more
# specific prefixes first. The slug is what the frontend chip filter uses,
# so keep it short, lowercase, and stable.
_SOURCE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("Bloomberg",     "bloomberg"),
    ("WSJ",           "wsj"),
    ("FT ",           "ft"),
    ("Financial Times", "ft"),
    ("MarketWatch",   "marketwatch"),
    ("CNBC",          "cnbc"),
    ("NYT",           "nyt"),
    ("Reuters",       "reuters"),
)


def derive_source_type(feed_name: str, kind: str = "news") -> str:
    """Map a feed_name → granular source slug.

    kind: 'news' (publisher feeds) or 'letter' (newsletters). Newsletter feeds
    are all collapsed under 'letter' so the chip set stays small; publisher
    feeds map by prefix (Bloomberg, WSJ, FT, MarketWatch, CNBC, NYT, Reuters).
    Anything that doesn't match falls back to 'news' (a catch-all for things
    like the 'Securitization & ABS News' Google-News aggregator).
    """
    if kind == "letter":
        return "letter"
    for prefix, slug in _SOURCE_PREFIXES:
        if feed_name.startswith(prefix):
            return slug
    return "news"


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Turn an RSS summary/description (often HTML — esp. Google News) into clean text."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)      # drop tags
    text = html.unescape(text)          # decode &amp; &#39; etc.
    return _WS_RE.sub(" ", text).strip()  # collapse whitespace


def _parse_date(entry) -> Optional[str]:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return None


async def _fetch_feed(
    session: aiohttp.ClientSession,
    feed_cfg: dict,
    kind: str = "news",
) -> list[dict]:
    """Fetch a single RSS feed and return parsed article dicts.

    `kind` is the structural family — 'news' or 'letter'. The per-article
    source_type slug is derived from the feed name (see derive_source_type).
    """
    url = feed_cfg.get("url", "")
    name = feed_cfg["name"]
    category = feed_cfg.get("category", "uncategorized")
    source_type = derive_source_type(name, kind)

    if url == "NEEDS_URL" or not url:
        logger.warning(f"[{name}] No URL configured — skipping")
        return []

    try:
        async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"[{name}] HTTP {resp.status}")
                return []
            raw = await resp.text()
    except Exception as e:
        logger.error(f"[{name}] Fetch error: {e}")
        return []

    parsed = feedparser.parse(raw)
    articles = []
    now = datetime.now(timezone.utc).isoformat()

    for entry in parsed.entries[:20]:  # cap at 20 per fetch
        link = getattr(entry, "link", None)
        if not link:
            continue
        title = strip_html(getattr(entry, "title", ""))
        snippet = strip_html(
            getattr(entry, "summary", "") or getattr(entry, "description", "")
        )[:500]

        articles.append(
            {
                "id": _hash_url(link),
                "feed_name": name,
                "feed_category": category,
                "title": title,
                "snippet": snippet,
                "url": link,
                "published_at": _parse_date(entry),
                "fetched_at": now,
                "source_type": source_type,
            }
        )

    return articles


async def _check_feed_health(
    session: aiohttp.ClientSession,
    feed_cfg: dict,
) -> dict:
    url = feed_cfg.get("url", "")
    name = feed_cfg["name"]
    needs_url = feed_cfg.get("needs_url", False) or url == "NEEDS_URL"
    now = datetime.now(timezone.utc).isoformat()

    platform = feed_cfg.get("platform", "rss")

    if needs_url:
        return {
            "feed_name": name,
            "url": url,
            "last_checked": now,
            "is_live": 0,
            "http_status": None,
            "error_msg": "URL not yet configured",
            "needs_url": 1,
            "platform": platform,
        }

    try:
        async with session.head(url, headers=HEADERS, timeout=TIMEOUT) as resp:
            status = resp.status
        # Some servers reject HEAD (e.g. CNBC 403, others 405) but serve GET —
        # which is what the fetcher uses. Confirm with GET before calling it dead.
        if status >= 400:
            async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                status = resp.status
        return {
            "feed_name": name,
            "url": url,
            "last_checked": now,
            "is_live": 1 if status < 400 else 0,
            "http_status": status,
            "error_msg": None if status < 400 else f"HTTP {status}",
            "needs_url": 0,
            "platform": platform,
        }
    except Exception as e:
        return {
            "feed_name": name,
            "url": url,
            "last_checked": now,
            "is_live": 0,
            "http_status": None,
            "error_msg": str(e)[:200],
            "needs_url": 0,
            "platform": platform,
        }


async def run_health_checks() -> dict:
    """Check all feeds and persist results. Returns summary."""
    cfg = load_feeds()
    all_feeds = cfg.get("news_feeds", []) + cfg.get("newsletter_feeds", [])

    async with aiohttp.ClientSession() as session:
        tasks = [_check_feed_health(session, f) for f in all_feeds]
        results = await asyncio.gather(*tasks)

    for r in results:
        upsert_feed_health(r)

    # Drop health rows for feeds no longer in the config (e.g. renamed/removed).
    current_names = [f["name"] for f in all_feeds]
    from cache.db import get_conn

    with get_conn() as conn:
        placeholders = ",".join("?" * len(current_names))
        conn.execute(
            f"DELETE FROM feed_health WHERE feed_name NOT IN ({placeholders})",
            current_names,
        )

    live = sum(1 for r in results if r["is_live"])
    needs_url = sum(1 for r in results if r["needs_url"])
    dead = len(results) - live - needs_url

    summary = {
        "total": len(results),
        "live": live,
        "dead": dead,
        "needs_url": needs_url,
        "details": results,
    }
    logger.info(
        f"Feed health: {live} live / {dead} dead / {needs_url} need URL"
    )
    return summary


async def fetch_all_feeds() -> int:
    """Poll all configured feeds, store new articles. Returns count inserted."""
    cfg = load_feeds()
    tagged = [(f, "news") for f in cfg.get("news_feeds", [])] + [
        (f, "letter") for f in cfg.get("newsletter_feeds", [])
    ]

    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_feed(session, f, kind) for f, kind in tagged]
        results = await asyncio.gather(*tasks)

    inserted = 0
    for articles in results:
        for article in articles:
            try:
                upsert_article(article)
                inserted += 1
            except Exception as e:
                logger.debug(f"Insert skip (likely dupe): {e}")

    logger.info(f"Feed fetch complete — {inserted} new articles")
    return inserted
