"""
backend/data/sources.py — pluggable data-source registry.

A data source is one Python module that defines a module-level `SOURCE`
(or `SOURCES` list) of `Source` objects. The registry collects them from:

  1. Built-ins — the `data.*` modules listed in BUILTIN_MODULES.
  2. Plugins   — every `*.py` in the plugins directory (default
                 `<project>/plugins/`, override with SOURCE_PLUGINS_DIR).
                 Files starting with `_` are skipped (templates, helpers).

Every registered source gets, with no other code changes:
  - a scheduled job (`every_hours`), recorded in `job_runs` like core jobs
  - an initial run at server startup (`run_on_startup`)
  - a manual refresh via POST /api/jobs/run/{id} (`manual_refresh`)
  - a listing at GET /api/sources
  - a chart on the Macro tab's "Custom Sources" section, if it declares a
    `panel` (series are read from the shared `metrics` table, which also
    makes them available at /api/fred/history/{series_id})

Per-install overrides live in data_sources.yaml without touching the code:

    sources:
      cfpb: {enabled: false}       # turn a source off
      trace: {every_hours: 24}     # change its cadence

A plugin that fails to import is logged and skipped — one broken file never
takes the server down. See plugins/README.md for the authoring guide.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Literal

from cache.db import upsert_metric
from config import PROJECT_ROOT, load_data_sources

logger = logging.getLogger(__name__)

# Built-in sources declared with SOURCE in their own module. Order is the
# order they're scheduled and listed.
BUILTIN_MODULES = [
    "data.manheim",
    "data.cfpb",
    "data.finra_trace",
    "data.sce",
    "data.sba",
    "data.nfib",
    "data.philly_cards",
    "data.fdic",
    "data.kcfed_sbls",
    "data.edgar_distress",
]

# Core scheduler jobs a source id may not shadow (data/scheduler.py).
RESERVED_IDS = frozenset({
    "feeds", "market", "fred", "edgar", "hhdc", "health", "bdc", "regulatory",
    "backup", "article_dedup", "sifma", "trust_perf", "macro_forecasts",
    "alerts", "daily_brief",
})

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


@dataclass(frozen=True)
class Series:
    """One line on a source's panel chart."""
    series_id: str          # key in the metrics table
    name: str               # legend label


@dataclass(frozen=True)
class Panel:
    """Optional dashboard chart for a source (rendered on the Macro tab)."""
    title: str
    series: tuple[Series, ...]
    subtitle: str = ""
    unit: Literal["plain", "pct"] = "plain"
    decimals: int = 1
    # Range-toggle preset matching the data's frequency.
    frequency: Literal["daily", "monthly", "quarterly"] = "monthly"


@dataclass(frozen=True)
class Source:
    """A pluggable data source.

    `fetch` takes no arguments and returns the number of rows written (or
    None). It may be a plain function (run in a worker thread) or an
    `async def`. Raise on failure — the scheduler records the error in
    job_runs.
    """
    id: str
    label: str
    fetch: Callable[[], int | None]
    every_hours: float = 24
    description: str = ""
    run_on_startup: bool = True
    manual_refresh: bool = True
    panel: Panel | None = None
    enabled: bool = True
    # Filled in by the registry: 'builtin' or the plugin file name.
    origin: str = field(default="", compare=False)


def store_series(
    series_id: str,
    label: str,
    points: Iterable[tuple[str, float | None]],
    category: str = "custom",
) -> int:
    """Upsert (date, value) points into `metrics`. Returns rows written.

    Dates are ISO strings ('YYYY-MM-DD'; monthly data uses the 1st). Writing
    the same (series_id, date) again replaces the value, so fetches can
    safely re-pull overlapping windows.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    n = 0
    for date, value in points:
        upsert_metric(
            {
                "series_id": series_id,
                "label": label,
                "category": category,
                "date": str(date)[:10],
                "value": None if value is None else float(value),
                "fetched_at": fetched_at,
            }
        )
        n += 1
    return n


# ── Discovery ────────────────────────────────────────────────────────────────

def plugins_dir() -> Path:
    return Path(os.getenv("SOURCE_PLUGINS_DIR") or PROJECT_ROOT / "plugins")


def _sources_in(module, origin: str) -> list[Source]:
    found = getattr(module, "SOURCES", None) or []
    if getattr(module, "SOURCE", None) is not None:
        found = [module.SOURCE, *found]
    out = []
    for s in found:
        if not isinstance(s, Source):
            logger.error("Sources: %s defines a non-Source %r — skipped", origin, s)
            continue
        out.append(replace(s, origin=origin))
    return out


def _load_plugin_file(path: Path):
    # Namespaced so a plugin named e.g. `json.py` can't shadow the stdlib.
    name = f"sm_plugins.{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _apply_overrides(s: Source, overrides: dict) -> Source:
    o = overrides.get(s.id) or {}
    changes = {}
    if "enabled" in o:
        changes["enabled"] = bool(o["enabled"])
    if "every_hours" in o:
        changes["every_hours"] = float(o["every_hours"])
    return replace(s, **changes) if changes else s


def discover_sources(
    builtin_modules: list[str] | None = None,
    plugin_dir: Path | None = None,
    overrides: dict | None = None,
) -> list[Source]:
    """Collect, validate, and configure every source. Never raises."""
    if builtin_modules is None:
        builtin_modules = BUILTIN_MODULES
    if plugin_dir is None:
        plugin_dir = plugins_dir()
    if overrides is None:
        overrides = (load_data_sources() or {}).get("sources") or {}

    candidates: list[Source] = []
    for mod_name in builtin_modules:
        try:
            candidates += _sources_in(importlib.import_module(mod_name), "builtin")
        except Exception as e:  # noqa: BLE001
            logger.error("Sources: built-in %s failed to import: %s", mod_name, e)

    if plugin_dir.is_dir():
        for path in sorted(plugin_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                candidates += _sources_in(_load_plugin_file(path), path.name)
            except Exception as e:  # noqa: BLE001
                logger.error("Sources: plugin %s failed to load: %s", path.name, e)

    sources: list[Source] = []
    seen: set[str] = set()
    for s in candidates:
        if not _ID_RE.match(s.id):
            logger.error(
                "Sources: %s id %r must be lowercase snake_case (2-40 chars) — skipped",
                s.origin, s.id,
            )
        elif s.id in RESERVED_IDS:
            logger.error("Sources: %s id %r is a reserved core job id — skipped", s.origin, s.id)
        elif s.id in seen:
            logger.error("Sources: %s id %r is already registered — skipped", s.origin, s.id)
        elif s.every_hours <= 0:
            logger.error("Sources: %s every_hours must be > 0 — skipped", s.id)
        else:
            seen.add(s.id)
            sources.append(_apply_overrides(s, overrides))

    plugins = [s for s in sources if s.origin != "builtin"]
    if plugins:
        logger.info("Sources: loaded plugins %s", ", ".join(s.id for s in plugins))
    return sources


@lru_cache(maxsize=1)
def get_sources() -> tuple[Source, ...]:
    """All registered sources (enabled or not), discovered once per process."""
    return tuple(discover_sources())


def enabled_sources() -> list[Source]:
    return [s for s in get_sources() if s.enabled]


def get_source(source_id: str) -> Source | None:
    return next((s for s in get_sources() if s.id == source_id), None)


def describe_source(s: Source) -> dict:
    """JSON shape served by GET /api/sources."""
    panel = None
    if s.panel is not None:
        panel = {
            "title": s.panel.title,
            "subtitle": s.panel.subtitle,
            "unit": s.panel.unit,
            "decimals": s.panel.decimals,
            "frequency": s.panel.frequency,
            "series": [{"series_id": x.series_id, "name": x.name} for x in s.panel.series],
        }
    return {
        "id": s.id,
        "label": s.label,
        "description": s.description,
        "origin": s.origin,
        "enabled": s.enabled,
        "every_hours": s.every_hours,
        "run_on_startup": s.run_on_startup,
        "manual_refresh": s.manual_refresh,
        "panel": panel,
    }
