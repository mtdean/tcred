"""
scripts/reparse_abs_new_issues.py — re-extract fields on every stored 424B5 row.

The live parser stores rows with many fields NULL (ratings 0%, WAL ~22%, etc.)
because its structured pandas table-finder doesn't handle every prospectus
layout. This script walks each row, fetches the original 424B5 HTML (cached
to disk after the first fetch), calls Claude for a richer extraction, and
UPDATEs the row — only filling fields that were NULL, preserving prior good data.

Then it re-runs the existing _compute_spread on the merged row so newly-
populated WAL turns into a computed spread_to_benchmark. Rows whose spread
was derived from coupon - matching-tenor UST are flagged spread_source='implied';
floating tranches whose spread was already a printed margin are 'parsed'.

Idempotent: re-running only touches rows that still have NULL fields. Cached
HTML on disk means re-runs don't re-download from EDGAR.

Usage:
    cd /Users/td/situation-monitor/backend
    source .venv/bin/activate
    python ../scripts/reparse_abs_new_issues.py            # full run
    python ../scripts/reparse_abs_new_issues.py --limit 5  # smoke test
"""

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from cache.db import get_conn  # noqa: E402
from data.abs_reparse import reparse_all  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def _backfill_existing_spread_source() -> int:
    """All rows in DB that already have spread_to_benchmark got it through the
    live parser's _compute_spread call. Floating → 'parsed' (printed margin);
    fixed → 'implied' (coupon - treasury math). Set spread_source for those
    rows ONE TIME so the column is fully populated even for pre-existing data.
    Returns rows updated.
    """
    with get_conn() as conn:
        n = conn.execute(
            """
            UPDATE abs_new_issues
               SET spread_source = CASE
                       WHEN coupon_type = 'floating' THEN 'parsed'
                       ELSE 'implied'
                   END
             WHERE spread_to_benchmark IS NOT NULL
               AND spread_source IS NULL
            """
        ).rowcount
    return n


def _coverage() -> dict:
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN wal_years IS NOT NULL THEN 1 ELSE 0 END) AS with_wal,
              SUM(CASE WHEN rating_sp IS NOT NULL OR rating_moodys IS NOT NULL
                       OR rating_kbra IS NOT NULL OR rating_fitch IS NOT NULL
                       THEN 1 ELSE 0 END) AS with_any_rating,
              SUM(CASE WHEN spread_to_benchmark IS NOT NULL THEN 1 ELSE 0 END) AS with_spread,
              SUM(CASE WHEN spread_source = 'parsed' THEN 1 ELSE 0 END) AS spread_parsed,
              SUM(CASE WHEN spread_source = 'implied' THEN 1 ELSE 0 END) AS spread_implied
            FROM abs_new_issues
            """
        ).fetchone()
        return dict(r)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap rows processed (for smoke test)",
    )
    args = parser.parse_args()

    print("== coverage BEFORE ==")
    before = _coverage()
    for k, v in before.items():
        print(f"  {k}: {v}")

    pre_n = _backfill_existing_spread_source()
    if pre_n:
        print(f"\nBackfilled spread_source on {pre_n} existing rows")

    print(f"\nRe-parsing rows{f' (limit {args.limit})' if args.limit else ''}…")
    result = reparse_all(limit=args.limit)
    print(
        f"\nScanned: {result['scanned']}, "
        f"updated: {result['updated']}, errors: {result['errors']}"
    )

    print("\n== coverage AFTER ==")
    after = _coverage()
    for k, v in after.items():
        delta = v - (before[k] or 0)
        sign = "+" if delta >= 0 else ""
        print(f"  {k}: {v}  ({sign}{delta})")


if __name__ == "__main__":
    main()
