"""
scripts/join_abs_pricing_to_new_issues.py — backfill 424B5 rows from FWP data.

abs_new_issues (424B5 prospectus supplements) has 0% rating coverage and ~22%
WAL coverage. abs_pricing (FWP pricing term sheets) has the same tranches from
the same deals but with ratings + WAL + spread cleanly extracted.

This script matches the two on (cleaned deal name, canonical class label,
filing dates within 30d) and copies the missing fields into abs_new_issues.

abs_pricing.rating is a combined "Moody's/S&P" string like "Aaa/AAA". We split
it so the chart's rating-bucket filter (which checks rating_sp, rating_moodys,
rating_kbra independently) finds a match.

Idempotent: UPDATEs only NULL fields, so re-running is safe.

Usage:
    cd /Users/td/situation-monitor/backend
    source .venv/bin/activate
    python ../scripts/join_abs_pricing_to_new_issues.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from cache.db import get_conn  # noqa: E402
from data.abs_reparse import apply_implied_spread  # noqa: E402


def _clean_name(s: str | None) -> str:
    """Normalize a deal/issuer name for matching across the two tables."""
    if not s:
        return ""
    # Stray newlines appear in some abs_new_issues.issuer_name values.
    s = s.replace("\n", " ").replace("\r", " ")
    # Drop common legal suffixes that one source includes and the other doesn't.
    for suffix in (
        "Owner Trust", "Issuance Trust", "Master Trust",
        "Master Owner Trust", "Master Note Trust",
        "LLC", "L.L.C.", "Inc.", "Inc",
        "Corporation", "Corp.", "Corp",
    ):
        s = re.sub(r"\b" + re.escape(suffix) + r"\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _canonical_class(s: str | None) -> str:
    """'Class A-2 Notes' → 'A2', 'A-2' → 'A2'. Matches the canonicalization in
    data/abs_reparse.py so the join behaves consistently."""
    if not s:
        return ""
    t = re.sub(r"\b(?:class|notes?|certs?|certificates?)\b", "", s, flags=re.IGNORECASE)
    return re.sub(r"[^A-Za-z0-9]", "", t).upper()


def _split_rating(rating: str | None) -> tuple[str | None, str | None]:
    """abs_pricing.rating is a combined string like 'Aaa/AAA' (Moody's/S&P) or
    just 'AAA'. Return (moodys, sp) where each is the bare letter grade or None."""
    if not rating:
        return None, None
    parts = [p.strip() for p in rating.split("/") if p.strip()]
    if len(parts) == 1:
        # Single string — guess agency by the first letter convention.
        p = parts[0]
        if p.startswith("A") and not p.startswith("Aa"):
            # 'AAA' / 'AA+' / 'A-' look like S&P.
            return None, p
        if p.startswith("Aa") or p.startswith("Ba"):
            # 'Aaa' / 'Aa1' / 'Baa1' look like Moody's.
            return p, None
        return None, p
    # Two-part: Moody's first by industry convention.
    return parts[0], parts[1]


def run() -> dict:
    """Match abs_new_issues to abs_pricing and copy missing fields."""
    with get_conn() as conn:
        ni_rows = [
            dict(r) for r in conn.execute(
                "SELECT id, issuer_name, class_name, filing_date, "
                "       wal_years, rating_sp, rating_moodys "
                "  FROM abs_new_issues"
            ).fetchall()
        ]
        p_rows = [
            dict(r) for r in conn.execute(
                "SELECT deal_name, class_name, pricing_date, wal, rating "
                "  FROM abs_pricing"
            ).fetchall()
        ]

    # Index pricing rows by (cleaned deal name, canonical class) for O(1) lookup.
    # If multiple pricing rows match a key, prefer the one whose date is
    # closest to the ni row's filing_date (resolved per ni row below).
    pricing_index: dict[tuple[str, str], list[dict]] = {}
    for p in p_rows:
        key = (_clean_name(p["deal_name"]), _canonical_class(p["class_name"]))
        if not key[0] or not key[1]:
            continue
        pricing_index.setdefault(key, []).append(p)

    updates: list[dict] = []
    for ni in ni_rows:
        key = (_clean_name(ni["issuer_name"]), _canonical_class(ni["class_name"]))
        if not key[0] or not key[1]:
            continue
        candidates = pricing_index.get(key, [])
        if not candidates:
            continue
        # Pick the candidate filed closest in time (within 30 days).
        ni_date = ni["filing_date"] or ""
        def _delta(p):
            try:
                from datetime import datetime
                d1 = datetime.strptime(ni_date[:10], "%Y-%m-%d")
                d2 = datetime.strptime((p["pricing_date"] or "")[:10], "%Y-%m-%d")
                return abs((d2 - d1).days)
            except ValueError:
                return 9999
        candidates_sorted = sorted(candidates, key=_delta)
        best = candidates_sorted[0]
        if _delta(best) > 30:
            continue

        m, s = _split_rating(best.get("rating"))
        change: dict = {"id": ni["id"]}
        if ni.get("wal_years") is None and best.get("wal") is not None:
            change["wal_years"] = best["wal"]
        if ni.get("rating_sp") is None and s:
            change["rating_sp"] = s
        if ni.get("rating_moodys") is None and m:
            change["rating_moodys"] = m
        if len(change) > 1:
            updates.append(change)

    counts = {"matched_rows": 0, "wal_filled": 0, "rating_sp_filled": 0,
              "rating_moodys_filled": 0}
    with get_conn() as conn:
        for u in updates:
            sets = [k for k in u if k != "id"]
            if not sets:
                continue
            set_sql = ", ".join(f"{k} = :{k}" for k in sets)
            conn.execute(
                f"UPDATE abs_new_issues SET {set_sql} WHERE id = :id", u,
            )
            counts["matched_rows"] += 1
            if "wal_years" in u:
                counts["wal_filled"] += 1
            if "rating_sp" in u:
                counts["rating_sp_filled"] += 1
            if "rating_moodys" in u:
                counts["rating_moodys_filled"] += 1

    return counts


def _coverage() -> dict:
    with get_conn() as conn:
        return dict(conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN wal_years IS NOT NULL THEN 1 ELSE 0 END) AS with_wal,
              SUM(CASE WHEN rating_sp IS NOT NULL THEN 1 ELSE 0 END) AS with_sp,
              SUM(CASE WHEN rating_moodys IS NOT NULL THEN 1 ELSE 0 END) AS with_moodys,
              SUM(CASE WHEN spread_to_benchmark IS NOT NULL THEN 1 ELSE 0 END) AS with_spread
            FROM abs_new_issues
            """
        ).fetchone())


def main() -> None:
    print("== BEFORE ==")
    before = _coverage()
    for k, v in before.items():
        print(f"  {k}: {v}")

    counts = run()
    print("\n== JOIN RESULT ==")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    n_implied = apply_implied_spread()
    print(f"\nImplied-spread pass: {n_implied} rows now have spread_to_benchmark")

    print("\n== AFTER ==")
    after = _coverage()
    for k, v in after.items():
        delta = v - (before[k] or 0)
        sign = "+" if delta >= 0 else ""
        print(f"  {k}: {v}  ({sign}{delta})")


if __name__ == "__main__":
    main()
