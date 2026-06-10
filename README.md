# `<TCRED>`

> A self-hosted, Bloomberg-terminal–styled intelligence dashboard for **macro, credit, and**  
> **structured finance**. One local server, reachable from any device on your home network.

![TCRED dashboard — Home](docs/screenshots/home.png)

---

## Quickstart

```bash
# 1. Keys (see Requirements below)
cp .env.example .env && $EDITOR .env       # add FRED + Anthropic keys + EDGAR user-agent

# 2. Backend
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Frontend (build the static app the backend serves)
cd ../frontend && npm install && npm run build

# 4. Run (serves API + app on http://localhost:8000)
cd ../backend && python main.py
```

Then open **http://localhost:8000** on this Mac, or `http://<your-lan-ip>:8000` from
an iPad on the same Wi-Fi (`ipconfig getifaddr en0` to find the IP). For an always-on
service, see [Running as a service](#running-as-a-service).

> **You provide three credentials** (all free): an **Anthropic API key**, a **FRED API key**,
> and an **EDGAR User-Agent** string. The app runs without them, but news scoring, the AI
> digest, the analyst briefing, and macro data stay empty until they're set. See [Requirements](#requirements).

---

## Requirements


| Tool    | Version | Notes                                          |
| ------- | ------- | ---------------------------------------------- |
| Python  | 3.11+   | built & run on 3.14                            |
| Node.js | 18+     | built on 26; only needed to build the frontend |
| OS      | macOS   | uses `launchd` for the auto-start service      |


### Credentials (`.env`)


| Variable            | What it is                                                                                                | Where to get it                                                                                                        |
| ------------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `ANTHROPIC_API_KEY` | Claude — powers article relevance scoring (1–5), the AI digest, and the **Analyst** briefing + chat (Opus 4.7) | [https://console.anthropic.com](https://console.anthropic.com)                                                         |
| `FRED_API_KEY`      | St. Louis Fed economic data (macro, rates, spreads, credit series)                                        | [https://fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html) (free, instant) |
| `EDGAR_USER_AGENT`  | **Not an API key** — SEC requires a `User-Agent` identifying you, in the form `AppName/0.1 you@email.com` | self-assigned per [https://www.sec.gov/os/webmaster-faq#developers](https://www.sec.gov/os/webmaster-faq#developers)   |


Market data (yfinance) and EDGAR filings need no credentials beyond the User-Agent.

---

## Installation

```bash
# Backend (Python)
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # all deps pinned to exact versions

# Frontend (build static assets the backend will serve)
cd ../frontend
npm install
npm run build                            # → frontend/dist/
```

There is no PyPI package to install — clone/copy the repo and run from source.

---

## Configuration

### Environment (`.env`)

```ini
FRED_API_KEY=...
ANTHROPIC_API_KEY=...
EDGAR_USER_AGENT=<TCRED>/0.1 you@email.com
```

### Feeds — `config/feeds.yaml`

Two lists, `news_feeds` (RSS) and `newsletter_feeds`:

```yaml
- name: "WSJ Markets"
  url: "https://..."        # "NEEDS_URL" + needs_url: true if unknown
  category: macro           # macro | credit | fintech | structured_finance | data_science
  platform: rss             # rss | substack | beehiiv | custom
  health_check: true
```

beehiiv feeds use `https://rss.beehiiv.com/feeds/<id>.xml` (find the id in page source).

### Data sources — `config/data_sources.yaml`

- `fred_series` — FRED series (id, label, category, frequency)
- `market_tickers` — yfinance tickers
- `edgar` — form types + asset-class keywords
- `refresh_intervals` — job cadences. `**news_feeds_minutes: 0**` disables automatic
(token-spending) news classification — see [Token usage](#token-usage).

---

## Usage

The FastAPI server hosts **both the REST API and the built React app on one port (`:8000`)**.
The frontend calls the API at the relative path `/api`, so it works from any device with no
CORS or `localhost` issues.

### Running as a service

A `launchd` agent starts the server on login and restarts it on crash:

```bash
cp scripts/com.situationmonitor.server.plist ~/Library/LaunchAgents/
launchctl load   ~/Library/LaunchAgents/com.situationmonitor.server.plist   # start
launchctl unload ~/Library/LaunchAgents/com.situationmonitor.server.plist   # stop
```

### Running manually

```bash
./scripts/run.sh        # or: cd backend && source .venv/bin/activate && python main.py
```

The port opens in ~1s; market/FRED/EDGAR data loads in the background just after.

### Accessing from an iPad

On the same Wi-Fi, open `http://<lan-ip>:8000` (`ipconfig getifaddr en0`). Add to Home
Screen for an app-like launcher. A DHCP reservation or your Mac's `.local` name gives a
stable URL.

### Token usage

The **only** Claude-spending actions are **manual**:

- **REFRESH** (top bar, route-aware) — refreshes the data behind the current page; on
  **/news** it fetches feeds + classifies the backlog (Claude). On token-free pages
  (Markets / Macro / ABS / Regulatory / …) it just re-pulls the underlying data.
- **GENERATE** (digest) — produce + archive a Home-page digest.
- **GENERATE** (analyst briefing) — produce a monthly macro/credit briefing with
  **Opus 4.7** + adaptive thinking. Subsequent **Analyst chat** turns are tool-use
  enabled (FRED history, ABS spread series, EDGAR lookups…) and cached so each new
  message only pays for incremental tokens.
- **SCORE** (Regulatory) — manual Claude scoring of Federal Register / agency items.
- **PARSE** (KBRA presale PDF) — Claude extracts collateral + tranche stats.

Market / FRED / EDGAR / BDC / Regulatory / SIFMA / feed-health / article-dedup /
nightly DB backup all refresh automatically and cost nothing. The top bar shows
"NEWS: time ago" so you know how stale the feed is. Set `news_feeds_minutes` to a
positive number to re-enable automatic classification.

### Digest archive

Digests persist in SQLite keyed by **(US/Eastern date, session)** where session is **AM**
(before noon ET) or **PM** (noon+). Browse past entries from the panel's date dropdown to
track how the dominant narrative shifts across the cycle.

### Analyst briefings

A separate, longer-form artifact from the Home digest. The **Analyst** page generates
a structured monthly macro/credit/structured-finance briefing using **Opus 4.7** with
`effort=high` thinking, persists it to the `briefings` table, and exposes a tool-use
chat against the saved briefing (`effort=xhigh`). The system prompt, tool list, and
briefing body are sent with `cache_control` so each subsequent chat turn only pays for
the new user message.

### Watchlists

Saved searches that span **news + EDGAR filings + regulatory actions** with a single
keyword/regex plus optional per-source filters (news categories, EDGAR asset classes
and form types, regulatory agencies). Each list tracks a "last viewed" timestamp so
the sidebar can flag new matches since you last looked.

### SIFMA drop folder

SIFMA's Excel export is gated behind a HubSpot form, so there's no auto-download. The
workflow is: download the latest *US ABS Issuance* xlsx from sifma.org once a month
and drop it in `backend/cache/sifma_drops/`. The scheduled `sifma` job (6h) parses
any new file into the 8 SIFMA series, then moves it to `sifma_drops/parsed/`. The
ABS page renders a stacked-area chart of monthly issuance by asset class.

### Nightly DB backups

A scheduled `backup` job snapshots `backend/cache/monitor.db` via SQLite's online
backup API into `backend/cache/backups/monitor-YYYY-MM-DD.db` with rolling retention
(safe to run while the server is live). `GET /api/backups` lists snapshots;
`POST /api/backups/run` triggers one on demand.

---

## Screenshots

**News** — scored feed with per-source chips, score/category/source filters, read state:
![News](docs/screenshots/news.png)

**Markets** — snapshot table with sparklines + spread charts with 3-yr percentiles:
![Markets](docs/screenshots/markets.png)

**Macro** — FRED "Favorite Dashboard" views alongside the credit panels:
![Macro](docs/screenshots/macro.png)

**ABS / EDGAR** — filterable SEC filings monitor:
![ABS/EDGAR](docs/screenshots/abs-edgar.png)


| Digest archive (AM/PM, US/Eastern) | Feed health |
| ---------------------------------- | ----------- |
| ![Digest archive](docs/screenshots/digest-archive.png) | ![Feed health](docs/screenshots/feed-health.png) |


---

## Features

- **Home** — AI digest (archived AM/PM), HY OAS, Treasury forward curve, SOFR rates
(1M/3M + computed 1Y), top scored stories, recent ABS filings.
- **News** — filterable scored feed with colored chip filters (category × source);
per-publication source chips (🗞 RSS / ✉ newsletter); **semantic dedup with a "+N sources"
badge** so the same story from multiple outlets collapses into one row; read-state
persistence; feed-health modal.
- **Markets** — snapshot table (price, 1D/5D/30D %, 90-day sparkline) grouped by category;
HY/IG/loan spread charts with **regime percentile chips** (where does the current value
sit vs. its 5-year distribution?); 21 consumer-credit equity proxies.
- **Macro** — a layered predictive stack on top of the raw series: a **recession-risk
ensemble** (blended yield-curve + EBP + near-term-forward-spread probits + meta-logit
stacked gauge), a **Consumer Financial Stress Index**, OFR & BIS systemic-risk gauges,
NY Fed delinquency-transition *flows*, credit impulse, plus the underlying activity /
rates / conditions / inflation panels. High-frequency adds: **weekly jobless claims**,
**Fed liquidity plumbing** (reserves / ON RRP / TGA), **Manheim used-vehicle values**
(auto-ABS recovery driver), and **CFPB complaint volume by product** (consumer-stress
lead). Each series carries a **freshness chip** (fresh / stale / dead) so you know when
a source has gone quiet. See [`docs/INDICATORS.md`](docs/INDICATORS.md) for the
research, formulas, and sources.
- **ABS / EDGAR** — paginated, filterable SEC filings monitor; a **new-issue spread
tracker** that parses ABS pricing term sheets (per-tranche spreads by prime / subprime /
equipment segment) including 424B5 column-mapping + WAL extraction; spread-momentum
deltas across deals; a **Trusts sub-tab** with monthly card master-trust performance
(delinquency / charge-off / payment rate) parsed from 10-D distribution reports —
consumer credit ~2 quarters ahead of the quarterly FRED series; **TRACE secondary
trading volume** (ABS / CLO / CMBS, daily); and a **stacked-area SIFMA issuance
panel** driven by the drop folder (see [Usage › SIFMA drop folder](#sifma-drop-folder)).
- **Deals** — issuer / deal pivot view. Pick a name and see every angle on it: ABS
new-issues with tranche aggregates, pricing history, EDGAR filings, KBRA presales, and
recent scored news — all in one place.
- **Private Credit** — BDC portfolio monitor with non-accrual trend, NAV-weighted
aggregates, summary tables and holdings (parsed from SEC SOI.tsv); KBRA presale parser
(manual Claude extract); CLO stress monitor (JBBB / JAAA spread proxy + EDGAR filings);
Fed **H.8 bank credit** table + credit impulse chart.
- **Regulatory** — Federal Register + agency RSS feed monitor (CFPB, OCC, FDIC, Fed,
SEC, FHFA, …) with manual Claude scoring on demand.
- **Analyst** — monthly Opus 4.7 macro/credit briefing (with adaptive thinking) +
tool-use chat against the saved briefing; markdown-rendered output.
- **Watchlists** — saved keyword/regex searches across news + EDGAR + regulatory
actions, with optional category / asset-class / form-type / agency filters and a "new
matches since last viewed" indicator.

---

## Architecture

```
Browser (Mac / iPad) ──http://<lan-ip>:8000──▶ FastAPI (uvicorn)
                                                 ├── /api/*   REST API
                                                 └── /        built React app + SPA fallback
                                                      ├── SQLite — articles · article_clusters
                                                      │           metrics · edgar_filings
                                                      │           abs_pricing · abs_new_issues
                                                      │           trust_performance
                                                      │           bdc_* · clo_* · h8_metrics
                                                      │           kbra_presales
                                                      │           regulatory_actions
                                                      │           feed_health · digests
                                                      │           briefings · watchlists
                                                      │           job_runs · meta
                                                      └── APScheduler — every job recorded
                                                           ├── market         15m ┐
                                                           ├── FRED+indicators 6h │
                                                           ├── EDGAR+ABS      4h │
                                                           ├── 10-D trusts   12h │
                                                           ├── TRACE volume  12h │
                                                           ├── HHDC          24h │ token-free,
                                                           ├── BDC           24h │ automatic
                                                           ├── Manheim       24h │
                                                           ├── CFPB          24h │
                                                           ├── Regulatory     6h │
                                                           ├── SIFMA          6h │
                                                           ├── article-dedup 30m │
                                                           ├── DB backup     24h │
                                                           ├── health        12h ┘
                                                           └── news fetch+classify   ┐ manual
                                                               digest / briefing     │ (Claude
                                                               regulatory score      │ spend)
                                                               KBRA parse            ┘
```

**Stack:** FastAPI · uvicorn · APScheduler · SQLite · `fredapi` · `yfinance` · `feedparser`
· `anthropic` · `pandas`/`numpy` · `openpyxl` (NY Fed + SIFMA workbooks) · `lxml` (ABS
term-sheet tables) · `pypdf` (KBRA presales) (backend); React 19 + TypeScript + Vite ·
React Query · React Router · Recharts · Radix UI · `react-markdown` (briefing + chat)
(frontend). Dependencies are pinned to exact versions. The pytest suite covers ~860
tests; every scheduled job is wrapped in an `_instrument` helper that records start /
end / status / duration / rows ingested into `job_runs`.

---

## Project layout

```
backend/
  main.py             FastAPI app: API + serves built frontend (SPA fallback)
  config.py           env + YAML loader
  api/routes.py       all REST endpoints
  cache/
    db.py             SQLite schema, migrations, queries
    monitor.db        the live database
    backups/          nightly online-backup snapshots (rolling retention)
    sifma_drops/      drop folder for SIFMA xlsx; parsed files moved into ./parsed/
  data/               feeds · classifier · digest · fred · market · edgar · scheduler
                      indicators (computed series) · hhdc (NY Fed flows)
                      abs_pricing · abs_parser · abs_reparse (424B5 + FWP)
                      trust_performance (10-D master-trust metrics)
                      manheim (UVVI) · cfpb (complaints) · finra_trace (STAR volume)
                      bdc · clo · h8 · kbra · regulatory · sifma
                      analyst · analyst_tools (briefing + tool-use chat, Opus 4.7)
                      article_dedup (semantic clustering)
                      watchlists · issuers · percentiles · freshness · backups
  tests/              pytest suite (~860 tests); see conftest.py for fixtures
frontend/
  src/
    pages/            Home · News · Markets · Macro · ABS · Deals
                      PrivateCredit · Regulatory · Analyst · Watchlists
    components/       layout · home · news · markets · macro · abs · deals
                      private_credit · regulatory · charts · shared
    lib/ styles/      API client, hooks, design tokens
  .env(.production)   VITE_API_URL (dev: localhost:8000/api · prod: /api)
config/
  feeds.yaml          RSS/newsletter feeds (incl. regulatory agency feeds)
  data_sources.yaml   FRED series, tickers, EDGAR config, ABS pricing, refresh intervals
scripts/
  run.sh                                 manual launcher
  com.situationmonitor.server.plist      launchd auto-start service
  backfill_*.py / reparse_*.py           one-shot maintenance scripts
  publish_gh_pages.sh / snapshot_api.py  static-mode (gh-pages) snapshot pipeline
docs/
  INDICATORS.md       predictive-indicator research: formulas, sources, build status
  ABS_SPREADS.md      ABS new-issue spread methodology
  screenshots/        README images
```

---

## API reference

```
# Status + observability
GET  /api/status                      health, row counts, last_news_refresh, jobs summary
GET  /api/jobs/status                 latest scheduled-job run per job_id
GET  /api/jobs/history                recent job-run history

# News + digest
GET  /api/articles                    scored articles (min_score, category=CSV, source_type, limit, offset)
GET  /api/articles/feed-health        per-feed health
POST /api/articles/{id}/read          mark read
POST /api/articles/refresh            MANUAL: fetch feeds + classify (Claude)
POST /api/digest                      MANUAL: generate + persist digest (Claude)
GET  /api/digests                     digest archive (newest first)

# Markets + FRED
GET  /api/market/snapshot             latest prices + 1D/5D/30D %
POST /api/market/refresh              manual yfinance refresh
GET  /api/market/history/{ticker}     price history
GET  /api/fred/latest                 latest value per series (incl. computed indicators)
GET  /api/fred/history/{series_id}    series history
POST /api/fred/refresh                manual FRED refresh
GET  /api/fred/forward-curve          today / 6mo / 1yr treasury curve
GET  /api/fred/sofr                   SOFR 1M/3M + computed 1Y
POST /api/indicators/refresh          recompute derived indicators (probits, EBP, ensemble…)
POST /api/hhdc/refresh                pull NY Fed delinquency-transition flows

# EDGAR + ABS + SIFMA
GET  /api/edgar/filings               filings (form_type, asset_class, limit, offset)
GET  /api/edgar/facets                distinct form types + asset classes
POST /api/edgar/refresh               manual EDGAR pull
GET  /api/abs/pricing                 ABS new-issue spreads by deal/tranche (segment filter)
GET  /api/abs/spread-momentum         senior/subordinate spread per deal over time
GET  /api/abs/spread-momentum/deltas  period-over-period spread deltas
POST /api/abs/pricing/refresh         discover + parse recent ABS pricing term sheets
GET  /api/abs/new-issues              parsed 424B5 deals (filters: segment, rating_bucket, …)
GET  /api/abs/spread-series           per-deal spread time series (rating_bucket=all by default)
GET  /api/abs/deal-summary            per-deal aggregated tranche stats
POST /api/abs/new-issues/refresh      manual 424B5 / FWP parse run
GET  /api/abs/issuance                SIFMA monthly issuance by asset class
POST /api/abs/issuance/refresh        scan SIFMA drop folder, parse, archive
GET  /api/trust-performance           monthly master-trust metrics from 10-Ds (time series)
GET  /api/trust-performance/latest    latest period per trust, metrics pivoted
POST /api/trust-performance/refresh   discover + parse recent 10-D distribution reports

# Private Credit (BDC / CLO / H.8 / KBRA)
GET  /api/bdc/watch-list              non-accrual watch list
GET  /api/bdc/nonaccrual-trend        non-accrual % over time
GET  /api/bdc/summary                 per-BDC summary table
GET  /api/bdc/latest-per-bdc          most recent filing per BDC (with comparatives)
GET  /api/bdc/aggregate-trend         NAV-weighted aggregate trend
GET  /api/bdc/nonaccruals             holdings flagged on non-accrual
POST /api/bdc/refresh                 pull latest SOI.tsv per BDC
GET  /api/clo/spread-proxy            JBBB / JAAA ETF spread proxy
GET  /api/clo/filings                 CLO-related EDGAR filings
GET  /api/h8/metrics                  Fed H.8 bank credit metrics
GET  /api/h8/credit-impulse           bank credit impulse series
GET  /api/kbra/presales               KBRA presale reports
POST /api/kbra/refresh                manual presale PDF parse (Claude)

# Regulatory
GET  /api/regulatory/actions          Federal Register + agency feed items
POST /api/regulatory/refresh          manual pull
POST /api/regulatory/score            MANUAL: relevance-score with Claude

# Analyst (briefings + tool-use chat)
GET  /api/briefings                   list saved briefings
GET  /api/briefings/latest            latest briefing
GET  /api/briefings/{id}              one briefing
POST /api/briefings/generate          MANUAL: generate (Opus 4.7, effort=high)
POST /api/briefings/{id}/chat         MANUAL: tool-use chat turn (Opus 4.7, effort=xhigh)

# Deals (issuer pivot)
GET  /api/issuers                     list issuer names + activity counts
GET  /api/issuers/summary             one issuer: tranches, pricing, EDGAR, KBRA, news

# Watchlists
GET    /api/watchlists                list saved searches
POST   /api/watchlists                create
GET    /api/watchlists/{id}           one watchlist
PATCH  /api/watchlists/{id}           edit
DELETE /api/watchlists/{id}           delete
GET    /api/watchlists/{id}/results   matches across news + EDGAR + regulatory
POST   /api/watchlists/{id}/viewed    bump last-viewed timestamp

# Operations
GET  /api/percentiles                 historical-percentile context for any series
GET  /api/freshness                   per-series fresh / stale / dead status
GET  /api/backups                     list nightly DB snapshots
POST /api/backups/run                 trigger a snapshot now
```

Computed series (recession-probit ensemble, EBP, NTFS, CFSI, OFR FSI, BIS credit gap,
NY Fed flows, …) are stored alongside FRED data and served through `/api/fred/*`. See
[`docs/INDICATORS.md`](docs/INDICATORS.md).

Interactive docs: `http://localhost:8000/docs`.

---

## Development

```bash
# Rebuild the frontend after UI changes (served fresh, no restart)
cd frontend && npm run build

# Restart after backend changes (launchd)
launchctl unload ~/Library/LaunchAgents/com.situationmonitor.server.plist
launchctl load   ~/Library/LaunchAgents/com.situationmonitor.server.plist

# Logs
tail -f logs/server.out.log logs/server.err.log

# Inspect the DB
sqlite3 backend/cache/monitor.db '.tables'

# Run the test suite
cd backend && source .venv/bin/activate
pip install -r requirements-dev.txt    # first time only
pytest -q                              # ~860 tests
```

`tests/conftest.py` sets `MONITOR_DB_PATH` to a session temp file *before* backend
modules import, so tests never touch the live `cache/monitor.db`. Useful fixtures:
`fresh_db`, `db_conn`, `mocked_responses`, `mocked_aiohttp`, `mock_anthropic`,
`api_client`.

---

## Notes & limitations

- **Single-user, no auth — LAN only.** Do not expose to the public internet.
- Some external feeds block automated fetching (HTTP 403) or expose no RSS; the Feed Health
modal flags dead / needs-url feeds.
- **SIFMA issuance** has no public API — the *US ABS Issuance* xlsx is gated behind a
HubSpot form. Drop the file into `backend/cache/sifma_drops/` once a month; the scheduled
`sifma` job picks it up and archives it to `parsed/`.
- The Analyst briefing + chat uses **Opus 4.7** with high / xhigh thinking and is the
most token-intensive surface in the app — it's always behind an explicit button.
- Display name is `<TCRED>`; behind-the-scenes identifiers (folder, `launchd` label, EDGAR
User-Agent) are functional only.

---

## Roadmap

- **Deals**: schema-agnostic rep-line tables, performance vs. forecast vintages, covenant
trigger gauges, payment waterfall, parquet ingestion pipeline (current view is the
issuer pivot — surfaces every angle on a name but doesn't yet drill into deal cashflows).
- Vintage delinquency curves, threshold alerts (Slack / email), authored notes attached
to chart points.

---

## License

[MIT](LICENSE) © 2026 Tucker Dean