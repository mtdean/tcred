"""
backend/data/regulatory.py — Regulatory Flow Monitor (Phase 7).

Foundation stub. The regulatory-flow worktree implements:
  - fetch_regulatory_actions(days_back):  Federal Register API + agency RSS,
                                           token-free, callable from scheduler
  - score_regulatory_actions(limit):      Claude relevance scoring — MANUAL only,
                                           triggered via POST /regulatory/score
  - get_regulatory_actions(...):          filtered list query for the UI

Note: scoring is intentionally separate from fetch so the scheduler can pull data
continuously without spending Claude tokens. Tokens spend only when the user hits
the SCORE button in the Regulatory tab.
"""

from typing import Optional


def fetch_regulatory_actions(days_back: int = 7) -> int:
    return 0


def score_regulatory_actions(limit: int = 100) -> int:
    return 0


def get_regulatory_actions(
    agency: Optional[str] = None,
    action_type: Optional[str] = None,
    min_score: int = 1,
    days_back: int = 90,
    limit: int = 100,
) -> list[dict]:
    return []
