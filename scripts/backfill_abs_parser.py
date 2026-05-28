"""
scripts/backfill_abs_parser.py — initial historical pull for the 424B5 parser.

Populates the abs_new_issues table with prospectus-supplement filings from the
last N days. Idempotent — already-parsed accession numbers are skipped, so
re-running widens the window without producing duplicates.

Usage:
    cd /Users/td/situation-monitor/backend
    source .venv/bin/activate
    python ../scripts/backfill_abs_parser.py --days 365

The pull walks the window in batches so EDGAR rate-limiting failures degrade
gracefully — a single bad batch loses 30 days of discovery, not the whole run.
"""

import argparse
import logging
import os
import sys

# Run as `python scripts/backfill_abs_parser.py` from the repo root or from
# anywhere — we resolve the backend on sys.path either way.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from data.abs_parser import fetch_and_parse_abs_424b5  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=365,
        help="How many days back to backfill (default: 365)",
    )
    parser.add_argument(
        "--batch-days", type=int, default=30,
        help="Step size when walking the window (default: 30)",
    )
    args = parser.parse_args()

    print(f"Backfilling 424B5 ABS filings for last {args.days} days "
          f"(batch={args.batch_days}d)...")
    total = 0
    for end in range(args.batch_days, args.days + args.batch_days, args.batch_days):
        window = min(end, args.days)
        print(f"  Window: last {window} days...")
        try:
            n = fetch_and_parse_abs_424b5(days_back=window)
        except Exception as e:
            print(f"    batch error (continuing): {e}")
            continue
        total += n
        print(f"    +{n} tranches (cumulative: {total})")

    print(f"\nBackfill complete. {total} tranches stored.")


if __name__ == "__main__":
    main()
