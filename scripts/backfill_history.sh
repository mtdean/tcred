#!/bin/bash
# situation-monitor/scripts/backfill_history.sh
# Optional deep-history pull for a fresh install.
#
# The server's startup fetch already fills FRED, market, CFPB, Manheim, HHDC,
# etc. with full history. These three datasets only pull a recent window on
# their scheduled runs, so a new install starts them nearly empty:
#
#   ABS 424B5 prospectuses   (abs_new_issues)  — default 1 year
#   ABS FWP pricing sheets   (abs_pricing)     — default 3 years
#   BDC holdings             (bdc_holdings)    — every SEC ZIP since 2022 Q4
#
# Each step is idempotent and safe to re-run or interrupt. Expect 30–90
# minutes total — EDGAR rate limits make this deliberately slow. Runs fine
# while the server is up.
#
# Usage:
#   scripts/backfill_history.sh            # all three
#   scripts/backfill_history.sh abs bdc    # pick: abs | pricing | bdc

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PY="$PROJECT_ROOT/backend/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "ERROR: run scripts/setup.sh first." >&2
    exit 1
fi

steps=("$@")
[ ${#steps[@]} -eq 0 ] && steps=(abs pricing bdc)

cd "$PROJECT_ROOT/backend"
for s in "${steps[@]}"; do
    case "$s" in
        abs)     echo "==> ABS 424B5 prospectuses (365d)";  "$PY" ../scripts/backfill_abs_parser.py --days 365 ;;
        pricing) echo "==> ABS FWP pricing (1095d)";        "$PY" ../scripts/backfill_abs_pricing.py --days 1095 ;;
        bdc)     echo "==> BDC holdings (all ZIPs)";        "$PY" ../scripts/backfill_bdc.py ;;
        *) echo "Unknown step '$s' (abs | pricing | bdc)" >&2; exit 2 ;;
    esac
done
echo "Backfill complete."
