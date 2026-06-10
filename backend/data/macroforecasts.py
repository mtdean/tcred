"""
backend/data/macroforecasts.py — macrobot forecast-bundle ingestion.

The macro-research bot (~/macro-research) writes a versioned hand-off bundle on
every `run_synthesis`:

  tcred_metrics.csv    flat rows (series_id, label, category, date, value) —
                       ensemble forecasts + model-disagreement bands, implied
                       forward-curve segments, regime evidence
  tcred_manifest.json  schema version + series catalog

This loader reads the bundle in place (default `~/macro-research/data/forecasts`,
override with MACRO_FORECASTS_DIR) and upserts rows into the shared `metrics`
table under categories macro_forecasts / macro_curve / macro_regime. The bundle
snapshots one value per series per run date, so repeated ingests accumulate
history (INSERT OR REPLACE on (series_id, date) keeps re-ingestion idempotent).

Supported bundle schema: tcred_schema_version 0.2.x — a major-version bump on
the macrobot side is logged and skipped rather than mis-parsed.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from cache import db

logger = logging.getLogger(__name__)

BUNDLE_DIR = Path(
    os.getenv("MACRO_FORECASTS_DIR")
    or (Path.home() / "macro-research" / "data" / "forecasts")
)
SUPPORTED_SCHEMA_MAJOR = "0"

_MACRO_CATEGORIES = ("macro_forecasts", "macro_curve", "macro_regime")


def ingest_macro_forecasts() -> dict:
    """Scan the bundle dir and upsert all metric rows. Returns counts."""
    metrics_path = BUNDLE_DIR / "tcred_metrics.csv"
    manifest_path = BUNDLE_DIR / "tcred_manifest.json"
    if not metrics_path.exists():
        logger.info("macroforecasts: no bundle at %s — skipping", metrics_path)
        return {"records": 0, "skipped": "no bundle"}

    if manifest_path.exists():
        try:
            version = json.loads(manifest_path.read_text()).get(
                "tcred_schema_version", "?")
            if version.split(".")[0] != SUPPORTED_SCHEMA_MAJOR:
                logger.warning(
                    "macroforecasts: bundle schema %s unsupported (want %s.x) — skipping",
                    version, SUPPORTED_SCHEMA_MAJOR,
                )
                return {"records": 0, "skipped": f"schema {version}"}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("macroforecasts: unreadable manifest (%s) — continuing", e)

    fetched_at = datetime.now(timezone.utc).isoformat()
    n = 0
    with metrics_path.open() as fh:
        for row in csv.DictReader(fh):
            try:
                db.upsert_metric({
                    "series_id": row["series_id"],
                    "label": row["label"],
                    "category": row["category"],
                    "date": row["date"],
                    "value": float(row["value"]),
                    "fetched_at": fetched_at,
                })
                n += 1
            except (KeyError, ValueError) as e:
                logger.warning("macroforecasts: bad row %s (%s) — skipped", row, e)
    logger.info("macroforecasts: ingested %d rows from %s", n, metrics_path)
    return {"records": n}


def get_macro_views() -> dict:
    """Serve the structured visualization payload (tcred_views.json) verbatim.

    Read fresh per request — the file is small and macrobot rewrites it on
    every synthesis run, so no caching layer is warranted."""
    views_path = BUNDLE_DIR / "tcred_views.json"
    if not views_path.exists():
        return {"available": False, "reason": f"no bundle at {views_path}"}
    try:
        return {"available": True, **json.loads(views_path.read_text())}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("macroforecasts: unreadable views file (%s)", e)
        return {"available": False, "reason": str(e)}


def get_macro_forecasts() -> dict:
    """Latest value per macro series, grouped by category (panel payload)."""
    out: dict[str, list[dict]] = {c: [] for c in _MACRO_CATEGORIES}
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT m.series_id, m.label, m.category, m.date, m.value
            FROM metrics m
            JOIN (
                SELECT series_id, MAX(date) AS max_date
                FROM metrics
                WHERE category IN (?, ?, ?)
                GROUP BY series_id
            ) latest
              ON m.series_id = latest.series_id AND m.date = latest.max_date
            WHERE m.category IN (?, ?, ?)
            ORDER BY m.series_id
            """,
            (*_MACRO_CATEGORIES, *_MACRO_CATEGORIES),
        ).fetchall()
    for r in rows:
        out[r["category"]].append({
            "series_id": r["series_id"], "label": r["label"],
            "date": r["date"], "value": r["value"],
        })
    return out
