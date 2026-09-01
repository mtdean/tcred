"""
backend/data/article_dedup.py — token-set similarity dedup over recent articles.

The original feed pipeline dedupes by URL hash, so the same story from Reuters
+ WSJ + a blog shows up three times. This pass groups them by title similarity
within a recent window, picks a primary per cluster, and marks the rest as
duplicates so /api/articles can hide them by default and show "+N sources"
instead.

Algorithm:
  1. Pull recent articles (last `window_hours`, default 72).
  2. Normalize each title:
        - lowercase
        - strip publisher suffix ("- Reuters", " | WSJ", " — Bloomberg", ...)
        - collapse punctuation
        - drop stop words
     The token set is what we compare on.
  3. Cluster: for each unclustered article (in published-time order), find any
     earlier clustered article whose token Jaccard ≥ THRESHOLD. If found, join
     that cluster; otherwise start a new one. Empty token sets are skipped
     (each gets a degenerate single-member cluster keyed by its own id).
  4. Pick a primary per cluster:
        - highest relevance_score
        - tie-break: earliest published_at
        - tie-break: lowest id (deterministic)
     Every other member's `duplicate_of` is set to the primary's id;
     `cluster_id` is the same for every member.

This is deliberately cheap. No embeddings, no external APIs. It catches the
common case — same wording across reposts — and misses the harder case of
paraphrased headlines, which is the right floor for a personal dashboard.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from cache import db

logger = logging.getLogger(__name__)


JACCARD_THRESHOLD = 0.7

# Trailing "- Publisher" / " | Publisher" / " — Publisher" patterns. RSS feeds
# from Reuters/WSJ/FT/etc commonly append the publisher this way.
_PUBLISHER_SUFFIX_RE = re.compile(
    r"\s*[\-\|–—:]\s*(Reuters|Bloomberg|WSJ|Wall Street Journal|"
    r"FT|Financial Times|MarketWatch|CNBC|NYT|New York Times|"
    r"The Wall Street Journal|The New York Times|The Financial Times|"
    r"Yahoo Finance|Business Insider|Forbes|Barron's|Barrons|"
    r"AP|Associated Press|BBC|Politico|Axios|Bloomberg Markets|"
    r"Bloomberg Opinion|Bloomberg.com)\s*$",
    re.IGNORECASE,
)

_PUNCT_RE = re.compile(r"[^\w\s'\-]+")
_WS_RE = re.compile(r"\s+")

# Light stop-word list. Aggressive stop-word removal hurts dedup recall on
# short news headlines, so we keep this minimal — just the structural words
# that appear in nearly every headline regardless of topic.
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "amid", "after", "before", "into", "over", "out", "up", "down",
    "this", "that", "these", "those", "it", "its", "their", "his", "her",
    "than", "but", "so", "if", "then", "also", "vs",
})


def _normalize_title(title: str) -> str:
    """Stripped + lowercased title, publisher suffix removed."""
    if not title:
        return ""
    s = title.strip()
    # Apply the suffix strip repeatedly — some headlines have two ("X - WSJ - Reuters").
    for _ in range(3):
        new = _PUBLISHER_SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    return s.lower()


def _tokens(title: str) -> set[str]:
    """Tokenize the normalized title; drop stop words and very short tokens."""
    norm = _normalize_title(title)
    norm = _PUNCT_RE.sub(" ", norm)
    norm = _WS_RE.sub(" ", norm).strip()
    if not norm:
        return set()
    return {
        tok for tok in norm.split()
        if len(tok) > 1 and tok not in _STOP_WORDS
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _new_cluster_id() -> str:
    return "clu_" + secrets.token_hex(6)


def _fetch_recent_articles(window_hours: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, published_at, fetched_at, relevance_score, publisher
            FROM articles
            WHERE COALESCE(published_at, fetched_at) >= ?
            ORDER BY COALESCE(published_at, fetched_at) ASC, id ASC
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def _cluster_articles(articles: list[dict]) -> list[list[str]]:
    """Group article ids into clusters by Jaccard similarity ≥ THRESHOLD.

    Returns a list of clusters, each a list of article ids. Articles whose
    token set is empty get their own single-member cluster.
    """
    cluster_tokens: list[set[str]] = []   # representative tokens per cluster
    cluster_members: list[list[str]] = []  # ids per cluster

    for a in articles:
        toks = _tokens(a.get("title") or "")
        if not toks:
            cluster_tokens.append(set())
            cluster_members.append([a["id"]])
            continue

        joined_idx: Optional[int] = None
        best_score = JACCARD_THRESHOLD
        for i, rep in enumerate(cluster_tokens):
            if not rep:
                continue
            score = _jaccard(toks, rep)
            if score >= best_score:
                best_score = score
                joined_idx = i

        if joined_idx is not None:
            cluster_members[joined_idx].append(a["id"])
            # Tighten the representative by intersecting with the new member —
            # this keeps the cluster from drifting if a later member happens
            # to be borderline-similar to the seed but disjoint from the rest.
            cluster_tokens[joined_idx] = cluster_tokens[joined_idx] & toks or toks
        else:
            cluster_tokens.append(toks)
            cluster_members.append([a["id"]])

    return cluster_members


def _pick_primary(members: list[dict]) -> dict:
    """Highest relevance_score, then best publisher tier, then earliest
    published_at, then lowest id.

    The tier check (trusted < unknown < junk) outranks recency on purpose:
    press-release mills syndicate a story minutes before real outlets pick it
    up, and "earliest wins" was making the junk version the visible card.
    """
    from data.feeds import publisher_tier_rank

    def _key(a: dict) -> tuple:
        score = a.get("relevance_score") or 0
        when = a.get("published_at") or a.get("fetched_at") or ""
        return (-int(score), publisher_tier_rank(a.get("publisher")), when, a["id"])
    return sorted(members, key=_key)[0]


def dedup_recent_articles(window_hours: int = 72) -> dict:
    """Cluster + tag recent articles. Returns summary counts."""
    articles = _fetch_recent_articles(window_hours)
    if not articles:
        return {"processed": 0, "clusters": 0, "duplicates": 0}

    by_id = {a["id"]: a for a in articles}
    clusters = _cluster_articles(articles)

    now = datetime.now(timezone.utc).isoformat()
    duplicate_writes: list[tuple] = []
    cluster_writes: list[tuple] = []
    n_dupes = 0

    for member_ids in clusters:
        members = [by_id[mid] for mid in member_ids]
        cluster_id = _new_cluster_id()
        primary = _pick_primary(members)
        for m in members:
            is_dup = m["id"] != primary["id"]
            cluster_writes.append((cluster_id, now, m["id"]))
            duplicate_writes.append(
                (primary["id"] if is_dup else None, m["id"])
            )
            if is_dup:
                n_dupes += 1

    with db.get_conn() as conn:
        conn.executemany(
            "UPDATE articles SET cluster_id = ?, deduped_at = ? WHERE id = ?",
            cluster_writes,
        )
        conn.executemany(
            "UPDATE articles SET duplicate_of = ? WHERE id = ?",
            duplicate_writes,
        )

    logger.info(
        "Dedup: processed %d articles → %d clusters (%d duplicates)",
        len(articles), len(clusters), n_dupes,
    )
    return {
        "processed": len(articles),
        "clusters": len(clusters),
        "duplicates": n_dupes,
        "window_hours": window_hours,
    }


# ── Query helpers for /api/articles ─────────────────────────────────────────

def annotate_with_sources(rows: list[dict]) -> list[dict]:
    """Attach n_sources + other_sources (feed_name list) to each primary row.

    `rows` are articles already loaded by the caller. We look up siblings via
    their cluster_id in one batched query, then fold the counts back in.
    """
    cluster_ids = {r["cluster_id"] for r in rows if r.get("cluster_id")}
    if not cluster_ids:
        for r in rows:
            r["n_sources"] = 1
            r["other_sources"] = []
        return rows

    placeholders = ",".join("?" * len(cluster_ids))
    with db.get_conn() as conn:
        sib = conn.execute(
            f"SELECT id, cluster_id, feed_name FROM articles "
            f"WHERE cluster_id IN ({placeholders})",
            list(cluster_ids),
        ).fetchall()

    by_cluster: dict[str, list[dict]] = {}
    for s in sib:
        by_cluster.setdefault(s["cluster_id"], []).append(dict(s))

    for r in rows:
        cid = r.get("cluster_id")
        siblings = by_cluster.get(cid, [])
        r["n_sources"] = max(len(siblings), 1)
        r["other_sources"] = sorted({
            s["feed_name"] for s in siblings if s["id"] != r["id"]
        })
    return rows
