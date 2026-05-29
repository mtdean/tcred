"""
scripts/backfill_abs_pricing.py — historical pull for the FWP pricing parser.

Populates the abs_pricing table with per-tranche spreads parsed from FWP
pricing term sheets. Idempotent — already-parsed accession numbers are skipped,
so re-running with a wider window only adds new deals.

Usage:
    cd /Users/td/situation-monitor/backend
    source .venv/bin/activate
    python ../scripts/backfill_abs_pricing.py --days 1095

Walks the window in batches so an EDGAR rate-limit blip in one batch loses
30 days of discovery, not the whole run.
"""

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from data.abs_pricing import fetch_abs_pricing  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=1095,
        help="How many days back to backfill (default: 1095 = 3 years)",
    )
    parser.add_argument(
        "--batch-days", type=int, default=30,
        help="Step size when walking the window (default: 30)",
    )
    args = parser.parse_args()

    print(f"Backfilling ABS FWP pricing for last {args.days} days "
          f"(batch={args.batch_days}d)...")
    total = 0
    for end in range(args.batch_days, args.days + args.batch_days, args.batch_days):
        window = min(end, args.days)
        print(f"  Window: last {window} days...")
        try:
            n = fetch_abs_pricing(days_back=window)
        except Exception as e:
            print(f"    batch error (continuing): {e}")
            continue
        total += n
        print(f"    +{n} tranches (cumulative: {total})")

    print(f"\nBackfill complete. {total} tranches stored.")


if __name__ == "__main__":
    main()
