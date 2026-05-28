#!/usr/bin/env python3
"""Build a static API snapshot for the gh-pages branch.

Hits the running backend at http://localhost:8000/api and writes JSON files
to frontend/public/api-snapshot/. The filename scheme mirrors snapshotKey() in
frontend/src/lib/api.ts so the static-mode adapter can find the right file.

Re-runnable; existing files are overwritten. Requires the backend to be up.

Usage:
    python scripts/snapshot_api.py [--base http://localhost:8000/api]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Params that the static-mode adapter strips when computing the lookup key.
# We snapshot each endpoint once at its widest setting and let the client
# slice/clip — a request for days_back=180 happily reads from the days_back=1095 file.
IGNORED_KEY_PARAMS = {"limit", "offset", "days_back", "hours_back"}


def snapshot_key(path: str, params: dict[str, Any] | None = None) -> str:
    """Mirror of snapshotKey() in frontend/src/lib/api.ts."""
    clean = path.strip("/").replace("/", "__")
    if not params:
        return clean + ".json"
    kept = sorted(
        (k, str(v))
        for k, v in params.items()
        if k not in IGNORED_KEY_PARAMS and v not in (None, "")
    )
    if not kept:
        return clean + ".json"
    return clean + "__" + "__".join(f"{k}-{v}" for k, v in kept) + ".json"


class Snapshotter:
    def __init__(self, base: str, out_dir: Path) -> None:
        self.base = base.rstrip("/")
        self.out_dir = out_dir
        self.written = 0
        self.failed: list[str] = []
        out_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.base + path
        if params:
            qs = {k: v for k, v in params.items() if v not in (None, "")}
            if qs:
                url += "?" + urllib.parse.urlencode(qs)
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.loads(r.read())

    def snap(self, path: str, params: dict[str, Any] | None = None) -> Any | None:
        try:
            data = self.fetch(path, params)
        except Exception as e:
            label = f"{path} {params}" if params else path
            print(f"  ! {label}: {e}", file=sys.stderr)
            self.failed.append(label)
            return None
        fname = snapshot_key(path, params)
        out = self.out_dir / fname
        out.write_text(json.dumps(data, separators=(",", ":")))
        self.written += 1
        return data


def run(base: str, out_dir: Path) -> int:
    s = Snapshotter(base, out_dir)
    print(f"writing snapshots to {out_dir} (source: {base})")

    # ── Fixed endpoints ────────────────────────────────────
    s.snap("/status")
    s.snap("/articles/feed-health")
    s.snap("/digests", {"limit": 60})
    s.snap("/market/snapshot")
    s.snap("/fred/latest")
    s.snap("/fred/forward-curve")
    s.snap("/fred/sofr", {"limit": 300})
    facets = s.snap("/edgar/facets") or {}
    s.snap("/abs/spread-momentum/deltas")
    s.snap("/abs/deal-summary", {"days_back": 90})
    s.snap("/bdc/watch-list")
    s.snap("/bdc/nonaccrual-trend")
    s.snap("/bdc/summary")
    s.snap("/bdc/nonaccruals", {"limit": 100})
    s.snap("/h8/metrics")
    s.snap("/h8/credit-impulse")
    s.snap("/clo/spread-proxy")
    s.snap("/clo/filings", {"limit": 50})
    s.snap("/kbra/presales")

    # ── FRED history per series ────────────────────────────
    fred_latest = s.fetch("/fred/latest")
    series_ids = sorted({r["series_id"] for r in fred_latest})
    series_ids = sorted(set(series_ids) | {"USREC"})  # recession shading
    print(f"  + FRED history × {len(series_ids)} series")
    for sid in series_ids:
        s.snap(f"/fred/history/{urllib.parse.quote(sid, safe='')}", {"limit": 1200})

    # ── Market history per ticker ──────────────────────────
    market = s.fetch("/market/snapshot") or []
    print(f"  + market history × {len(market)} tickers")
    for r in market:
        ticker = r["ticker"]
        s.snap(
            f"/market/history/{urllib.parse.quote(ticker, safe='')}",
            {"limit": 252},
        )

    # ── Articles ───────────────────────────────────────────
    # ArticleFeed default: min_score with no category. Cover all selectable scores.
    for ms in (1, 3, 4, 5):
        s.snap("/articles", {"min_score": ms, "limit": 50})
    # TopArticlesPanel queries per category at the homepage default (min_score=4, limit=5).
    for cat in ("macro", "credit", "abs", "fintech", "data"):
        s.snap("/articles", {"category": cat, "min_score": 4, "limit": 5})

    # ── Edgar filings ──────────────────────────────────────
    s.snap("/edgar/filings", {"limit": 12})  # RecentFilingsStrip
    s.snap("/edgar/filings", {"limit": 50})  # EdgarFeed default
    for ft in facets.get("form_types", []):
        s.snap("/edgar/filings", {"form_type": ft, "limit": 50})
    for ac in facets.get("asset_classes", []):
        s.snap("/edgar/filings", {"asset_class": ac, "limit": 50})

    # ── ABS pricing (FWP feed) ─────────────────────────────
    for seg in ("", "subprime_auto", "prime_auto", "equipment", "credit_card"):
        s.snap("/abs/pricing", {"segment": seg or None})

    # ── ABS new-issues + spread-series ─────────────────────
    abs_classes = (
        "prime_auto_loan", "subprime_auto_loan", "auto_lease", "credit_card",
        "equipment", "student_loan", "consumer_loan", "solar",
    )
    buckets = ("all", "AAA", "AA", "A", "BBB", "BB_and_below")
    for ac in abs_classes:
        s.snap("/abs/new-issues", {
            "asset_class": ac, "days_back": 1095, "limit": 100,
            "min_confidence": "low",
        })
        for rb in buckets:
            s.snap("/abs/spread-series", {
                "asset_class": ac, "rating_bucket": rb, "days_back": 1095,
            })

    # ── Regulatory actions ─────────────────────────────────
    # The widget supports many filter combinations; cover the defaults plus
    # single-axis variations. Multi-filter combos fall back to the no-filter
    # default in the adapter when no exact match exists.
    for ms in (1, 3, 4, 5):
        s.snap("/regulatory/actions", {"min_score": ms, "days_back": 180, "limit": 200})
    for ag in ("CFPB", "OCC", "FDIC", "Fed", "SEC"):
        for ms in (1, 3, 4, 5):
            s.snap("/regulatory/actions", {
                "agency": ag, "min_score": ms, "days_back": 180, "limit": 200,
            })
    for at in ("RULE", "PROPOSED_RULE", "NOTICE", "PRESS_RELEASE"):
        s.snap("/regulatory/actions", {
            "action_type": at, "min_score": 4, "days_back": 180, "limit": 200,
        })

    # ── KBRA presales ──────────────────────────────────────
    for ac in (
        "prime_auto_loan", "subprime_auto_loan", "auto_lease", "credit_card",
        "equipment", "consumer_loan", "student_loan",
    ):
        s.snap("/kbra/presales", {"asset_class": ac})

    print(f"\ndone — wrote {s.written} files")
    if s.failed:
        print(f"  ({len(s.failed)} failed; see stderr)")
    return 0 if not s.failed else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="http://localhost:8000/api")
    p.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "frontend" / "public" / "api-snapshot",
    )
    args = p.parse_args()
    return run(args.base, args.out)


if __name__ == "__main__":
    sys.exit(main())
