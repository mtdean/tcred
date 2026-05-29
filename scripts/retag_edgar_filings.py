"""
scripts/retag_edgar_filings.py — re-derive asset_class + issuance_type for
existing edgar_filings rows using the company-name classifiers in
data/edgar.py.

Previously the FTS keyword that surfaced a filing was stored as the
asset_class verbatim. That mistagged CMBS deals as 'royalty' (because their
docs mention the word) and other false positives. The new classifier reads
the deal trust's name, which is far more reliable.

issuance_type is a brand-new column — filled here for the first time.

Idempotent: UPDATE re-runs leave each row at its current classification.

Usage:
    cd /Users/td/situation-monitor/backend
    source .venv/bin/activate
    python ../scripts/retag_edgar_filings.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from cache.db import get_conn, init_db  # noqa: E402
from data.edgar import _classify_asset_class, _classify_issuance_type  # noqa: E402


def main() -> None:
    init_db()  # ensures issuance_type column exists

    with get_conn() as conn:
        rows = [
            dict(r) for r in conn.execute(
                "SELECT accession_no, company_name, form_type, asset_class "
                "  FROM edgar_filings"
            ).fetchall()
        ]

    changed_class = changed_issuance = 0
    with get_conn() as conn:
        for r in rows:
            # Fallback to the existing asset_class so anything we can't
            # name-derive keeps its prior classification rather than being
            # blanked out.
            new_class = _classify_asset_class(r["company_name"], r["asset_class"] or "other")
            new_issuance = _classify_issuance_type(r["company_name"], r["form_type"] or "")
            sets: list[str] = []
            params: dict = {"acc": r["accession_no"]}
            if new_class != r["asset_class"]:
                sets.append("asset_class = :asset_class")
                params["asset_class"] = new_class
                changed_class += 1
            sets.append("issuance_type = :issuance_type")
            params["issuance_type"] = new_issuance
            if new_issuance != (r.get("issuance_type") if "issuance_type" in r else None):
                changed_issuance += 1
            conn.execute(
                f"UPDATE edgar_filings SET {', '.join(sets)} "
                f"WHERE accession_no = :acc",
                params,
            )

    print(f"Rows scanned: {len(rows)}")
    print(f"  asset_class changed: {changed_class}")
    print(f"  issuance_type filled/changed: {changed_issuance}")

    # New distributions
    print("\nNew asset_class distribution:")
    with get_conn() as conn:
        for row in conn.execute(
            "SELECT asset_class, COUNT(*) AS n FROM edgar_filings "
            "GROUP BY asset_class ORDER BY n DESC"
        ):
            print(f"  {row['asset_class']!r:28s} {row['n']}")
        print("\nIssuance type distribution:")
        for row in conn.execute(
            "SELECT issuance_type, COUNT(*) AS n FROM edgar_filings "
            "GROUP BY issuance_type ORDER BY n DESC"
        ):
            print(f"  {row['issuance_type']!r:10s} {row['n']}")


if __name__ == "__main__":
    main()
