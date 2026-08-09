# Trading Intelligence Backend

FastAPI backend for the pharma/life-sciences trading intelligence agent: it
ingests market news (SEC EDGAR filings and Finnhub company news) and stock
prices (Alpha Vantage), scores each story for financial sentiment and business
event type, stores everything in PostgreSQL, evaluates user alerts, streams
real-time updates over WebSocket, and backtests how news historically moved
prices.

## Quick start

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

cp .env.example .env          # then edit: DATABASE_URL, SEC_USER_AGENT

.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive API reference.

No PostgreSQL yet? Point `DATABASE_URL` at SQLite to try it out:

```
DATABASE_URL=sqlite+aiosqlite:///./dev.db
```

On startup the app creates any missing tables and seeds a ~45-symbol
pharma/biotech/CDMO/AI watchlist. Add `backend/data/tickers.csv`
(`ticker,company_name,sector,exchange`) to replace the seed list with your own
universe — no code change needed.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + database probe |
| GET | `/news` | Scored news feed; filter by `ticker`, `sentiment`, `event_type`, `source`, `min_score`, `since_days` |
| GET | `/stocks` | Tracked universe, filterable by `sector` |
| GET | `/stocks/{ticker}` | Detail: latest price + recent scored news |
| POST | `/alerts` | Create an alert (`positive_news`, `negative_news`, `sentiment_spike`, `event_type`, `price_change`) |
| GET | `/alerts` | List alerts; `/alerts/history` shows firings |
| DELETE | `/alerts/{id}` | Deactivate (history is kept) |
| GET | `/backtest?ticker=MRNA&days=90` | Price impact by event type + signal accuracy |
| GET | `/jobs/status` | Scheduler state and next run times |
| POST | `/admin/seed` | Re-run universe seeding |
| POST | `/admin/ingest/sec` | Trigger an SEC pull now (optional `?ticker=MRNA&ticker=PFE`) |
| POST | `/admin/ingest/finnhub` | Trigger a Finnhub news pull now |
| POST | `/admin/ingest/prices` | Trigger an Alpha Vantage quote refresh now |
| POST | `/admin/backfill/prices` | Load daily price history for one ticker (`?ticker=MRNA&outputsize=full`) |
| WS | `/ws/tickers/{ticker}` | Real-time snapshot, price pushes, and alert pushes |

### WebSocket protocol

On connect the server sends a `snapshot` (latest price + 5 recent headlines).
After that:

```jsonc
// client -> server
{"action": "subscribe",   "tickers": ["MRNA", "BNTX"]}
{"action": "unsubscribe", "tickers": ["BNTX"]}
{"action": "ping"}

// server -> client
{"type": "price_update", "ticker": "MRNA", "price": 145.2, "change": 2.5, "timestamp": "..."}
{"type": "alert", "headline": "...", "sentiment": "positive", "event_type": "fda_approval", ...}
```

## Sentiment

Two interchangeable backends (`SENTIMENT_BACKEND`):

- **`lexicon`** (default) — pharma-tuned keyword scorer with negation handling.
  Zero downloads, so a fresh clone works offline and in CI.
- **`finbert`** — ProsusAI/finbert. `pip install -r requirements-ml.txt` first.

Both also classify each headline into a business event taxonomy
(`fda_approval`, `clinical_trial`, `revenue`, `merger_acquisition`, `recall`,
`partnership`, `litigation`, `exec_change`, `facility`, `analyst_rating`,
`capital_raise`, `other`) — alerts and backtests key off these.

## Data sources

| Source | What it provides | Requirement |
|--------|------------------|-------------|
| SEC EDGAR | 8-K/10-K/10-Q/6-K filings via the `data.sec.gov` JSON APIs | A descriptive `SEC_USER_AGENT` with a contact address (anonymous requests are rejected) |
| Finnhub | Company news headlines + summaries | `FINNHUB_API_KEY` (free tier: ~60 calls/min) |
| Alpha Vantage | Latest quotes (`GLOBAL_QUOTE`) + daily history (`TIME_SERIES_DAILY`) | `ALPHA_VANTAGE_API_KEY` (free tier: 5 calls/min) |

Missing keys degrade gracefully: the source logs a skip and everything else
keeps running. Rate limits are respected with inter-call delays, and both APIs'
throttle responses stop the current batch cleanly instead of erroring.

After adding an Alpha Vantage key, backfill history for your main tickers so
backtests have data on day one:

```bash
curl -X POST "http://localhost:8000/admin/backfill/prices?ticker=MRNA&outputsize=full"
```

## Background jobs (APScheduler)

| Job | Default cadence | What it does |
|-----|-----------------|--------------|
| SEC EDGAR ingestion | every 30 min | Rotates through the watchlist in batches, pulls recent filings, scores and stores them |
| Finnhub news ingestion* | every 5 min | Rotates 50-ticker batches through `/company-news`, deduped on URL |
| Alpha Vantage quote refresh* | every 60 s | Refreshes latest quotes; tickers with live WebSocket subscribers jump the queue |
| WebSocket price push | every 10 s | Pushes the latest close + % change to subscribed clients |
| Retention cleanup | weekly | Deletes news/prices older than `DATA_RETENTION_DAYS` |

\* Only registered when the corresponding API key is configured, so
`/jobs/status` reflects what is actually running.

## Growing the ticker universe

`scripts/build_universe.py` generates `data/tickers.csv` from Finnhub's US
symbol directory, keyword-filtered to pharma/life-sciences names:

```bash
FINNHUB_API_KEY=... .venv/bin/python scripts/build_universe.py --dry-run   # preview
FINNHUB_API_KEY=... .venv/bin/python scripts/build_universe.py --limit 1000
# then restart the API or call POST /admin/seed
```

Review the CSV before committing — keyword matching is deliberately broad.

## Database

`app/models.py` is the source of truth; tables are auto-created on startup.
`db/schema.sql` is the equivalent hand-written PostgreSQL DDL for provisioning
via the Supabase SQL editor or a locked-down Cloud SQL instance.

Tables: `stocks`, `news_articles` (deduped on `(url, source)`),
`sentiment_scores`, `stock_prices`, `user_alerts`, `alert_history`.

### Connecting to Supabase

1. In the Supabase dashboard, click **Connect** (top bar) and pick the
   **Session pooler** connection string. It looks like:

   ```
   postgresql://postgres.abcdefghijkl:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```

   Prefer the session pooler (port **5432**) over the direct connection — the
   direct host is IPv6-only on many networks — and over the transaction pooler
   (port 6543), which this long-running API doesn't need.

2. Swap the scheme for the async driver and put it in `.env`:

   ```
   DATABASE_URL=postgresql+asyncpg://postgres.abcdefghijkl:YOUR-PASSWORD@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```

   If the password contains special characters (`@ : / # ?`), URL-encode them
   (`@` → `%40`, etc.).

3. Start the API. With `CREATE_TABLES_ON_STARTUP=true` (the default) it
   creates all tables and seeds the watchlist on first boot — no manual SQL
   needed. Alternatively, paste `db/schema.sql` into the Supabase SQL editor
   first and set `CREATE_TABLES_ON_STARTUP=false`.

4. Verify: `curl localhost:8000/health` should report `"database": "ok"`, and
   the tables appear in Supabase's Table Editor.

If you do use the transaction pooler (port 6543), the app automatically
disables asyncpg's prepared-statement cache, which otherwise breaks on pooled
connections; `DB_STATEMENT_CACHE_SIZE` overrides that if needed.

**Security note:** Supabase auto-exposes every `public`-schema table through
its REST API (`/rest/v1/...`), keyed by the project's publishable key. The app
enables Row Level Security on all of its tables at startup (and `schema.sql`
does the same), which blocks that path — with no policies defined, the
publishable key can neither read nor write anything. The backend itself is
unaffected because it connects as the table owner over a direct Postgres
connection. Ignore the Supabase dashboard's Next.js/`supabase-js` quickstart:
clients talk to this API, not to Supabase directly.

## Tests

```bash
.venv/bin/python -m pytest
```

84 tests cover sentiment scoring, event classification, ingestion/dedup, alert
matching, the REST API, the WebSocket hub, backtesting, scheduler batch
rotation, and all three integrations (SEC, Finnhub, Alpha Vantage) with mocked
transports — no network needed.

## Roadmap (from the build plan)

- **Week 2 (done)** — Finnhub news, Alpha Vantage quotes + history backfill,
  universe tooling for ~1000 symbols.
- **Week 3** — React dashboard, Electron desktop packaging, mobile-responsive
  PWA.
- **Week 4** — Slack/email/push notification channels (the dispatch seam is
  `app/services/alerts.py::_dispatch`), portfolio simulator, Redis price cache
  if scale demands it, GCP Cloud Run + Cloud SQL deployment.
