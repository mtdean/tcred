# Data-source plugins

A plugin is one Python file in this folder that fetches some data and declares a
`SOURCE`. On the next server restart it is:

- **scheduled** every `every_hours`, with each run recorded in `job_runs`
  (`GET /api/jobs/status`) like the built-in jobs
- **run once at startup** (unless `run_on_startup=False`)
- **refreshable on demand**: `POST /api/jobs/run/<id>`
- **listed** at `GET /api/sources`
- **charted** on the Macro tab under *Custom Sources*, if it declares a `panel`
- **queryable** per series at `GET /api/fred/history/<series_id>`

No changes to the scheduler, API routes, or frontend are needed.

## Quick start

```bash
cp plugins/_example_treasury_rates.py plugins/treasury_rates.py
# restart the server (scripts/run.sh, or kickstart the service)
curl localhost:8000/api/sources | python3 -m json.tool
```

Files starting with `_` are ignored, so `_example_treasury_rates.py` is a template
that never loads by itself.

## Anatomy

```python
import requests
from data.sources import Panel, Series, Source, store_series

def fetch() -> int:
    rows = requests.get("https://example.com/api", timeout=60).json()
    # Upsert (date, value) points into the shared metrics table.
    return store_series(
        "MYSRC_WIDGETS",                  # series id: unique across all sources
        "Widgets shipped (monthly)",      # human label
        [(r["date"], r["value"]) for r in rows],
    )

SOURCE = Source(
    id="my_source",                       # lowercase snake_case, 2–40 chars
    label="Widget shipments",
    description="Monthly widget shipments from Example Corp.",
    fetch=fetch,
    every_hours=24,
    panel=Panel(                          # optional — omit for no chart
        title="Widget Shipments",
        subtitle="UNITS / MONTH",
        unit="plain",                     # "plain" or "pct"
        decimals=0,
        frequency="monthly",              # "daily" | "monthly" | "quarterly"
        series=(Series("MYSRC_WIDGETS", "WIDGETS"),),
    ),
)
```

A file can declare several sources with `SOURCES = [Source(...), ...]`.

## Rules

| | |
|---|---|
| **`fetch`** | No arguments. Return the number of rows written (or `None`). A plain function runs in a worker thread; an `async def` runs on the event loop. |
| **Errors** | Just raise. The run is recorded as `error` in `job_runs` with the message, and the next scheduled run tries again. A plugin that fails to *import* is logged and skipped, and the server still starts. |
| **Dates** | ISO `YYYY-MM-DD`. Monthly data uses the 1st of the month; quarterly data uses the first day of the quarter. |
| **Idempotency** | `store_series` replaces an existing `(series_id, date)` point, so re-pulling overlapping windows is safe. For large histories, fetch incrementally (see `backend/data/cfpb.py`). |
| **IDs** | Source ids must be unique and can't reuse a core job id (`feeds`, `market`, `fred`, `edgar`, …). A rejected id is logged at startup. Prefix series ids with your source name to avoid collisions. |
| **Dependencies** | Anything in `backend/requirements.txt` is available (`requests`, `pandas`, `openpyxl`, `lxml`, `bs4`, `pypdf`, …). If you need more, add it there. |
| **Secrets** | Read API keys with `os.getenv("MY_KEY")`. The server loads `.env` at startup, so put them there, never in the plugin file. |
| **Courtesy** | Pick `every_hours` to match how often the data actually changes, and send a `User-Agent` if the publisher asks for one (`from config import settings; settings.EDGAR_USER_AGENT`). |

## Per-install overrides

Without editing the plugin, `config/data_sources.yaml` can turn any source (built-in or
plugin) off, or change how often it runs:

```yaml
sources:
  treasury_rates: {every_hours: 72}
  cfpb: {enabled: false}
```

## Testing a plugin

```bash
cd backend && source .venv/bin/activate
python -c "from data.sources import get_source; print(get_source('my_source').fetch())"
```

Point `MONITOR_DB_PATH=/tmp/scratch.db` at a throwaway database to try it without
touching your real data. Plugins elsewhere on disk can be loaded with
`SOURCE_PLUGINS_DIR=/path/to/dir`.

## Built-in examples

The built-in sources use the same interface: they're plain modules listed in
`BUILTIN_MODULES` in `backend/data/sources.py`. Good references:

- `backend/data/cfpb.py`: incremental fetch with a revision window
- `backend/data/manheim.py`: scrape a page for a re-dated XLSX link, then parse it
- `backend/data/finra_trace.py`: a daily file where history builds up from first ingest
