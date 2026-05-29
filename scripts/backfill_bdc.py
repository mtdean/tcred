"""
scripts/backfill_bdc.py — historical BDC SOI bulk-dataset backfill.

Iterates every BDC ZIP published on SEC's BDC data-sets page (quarterly back
to 2022 Q4, monthly from 2025-04 onward) and ingests each into bdc_holdings +
bdc_summary. Idempotent — holdings are INSERT OR IGNORE on a hash key and
summaries are INSERT OR REPLACE per (cik, observation_date).

Usage:
    cd /Users/td/situation-monitor/backend
    source .venv/bin/activate
    python ../scripts/backfill_bdc.py
    python ../scripts/backfill_bdc.py --since-year 2024
"""

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from data.bdc import backfill_bdc_data  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-year", type=int, default=None,
        help="Skip ZIPs older than this year (e.g. 2024). Default: ingest all.",
    )
    args = parser.parse_args()

    print(f"Backfilling BDC SOI dataset"
          f"{f' from {args.since_year} onward' if args.since_year else ' (full history)'}…")
    result = backfill_bdc_data(since_year=args.since_year)

    print(f"\nDone. {result['zips_processed']} ZIPs processed, "
          f"{result['total_holdings']} holdings stored.")
    print("Per-zip breakdown:")
    for r in result["per_zip"]:
        print(f"  {r['zip']:30s}  {r['status']:6s}  +{r['holdings']:6d}")


if __name__ == "__main__":
    main()
