# Trading Intelligence Backend

FastAPI backend for the pharma/life-sciences trading intelligence agent: it
ingests market news (SEC EDGAR in this phase), scores each item for financial
sentiment and business event type, stores everything in PostgreSQL, evaluates
user alerts, streams real-time updates over WebSocket, and backtests how news
historically moved prices.

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

## Background jobs (APScheduler)

| Job | Default cadence | What it does |
|-----|-----------------|--------------|
| SEC EDGAR ingestion | every 30 min | Rotates through the watchlist in batches, pulls recent 8-K/10-K/10-Q/6-K filings, scores and stores them |
| WebSocket price push | every 10 s | Pushes the latest close + % change to subscribed clients |
| Retention cleanup | weekly | Deletes news/prices older than `DATA_RETENTION_DAYS` |

Note: the SEC requires a descriptive `SEC_USER_AGENT` containing a real contact
address; anonymous requests are rejected. Some corporate networks block
`sec.gov` entirely — ingestion logs a warning and continues in that case.

## Database

`app/models.py` is the source of truth; tables are auto-created on startup.
`db/schema.sql` is the equivalent hand-written PostgreSQL DDL for provisioning
via the Supabase SQL editor or a locked-down Cloud SQL instance.

Tables: `stocks`, `news_articles` (deduped on `(url, source)`),
`sentiment_scores`, `stock_prices`, `user_alerts`, `alert_history`.

## Tests

```bash
.venv/bin/python -m pytest
```

62 tests cover sentiment scoring, event classification, ingestion/dedup, alert
matching, the REST API, the WebSocket hub, backtesting, and SEC parsing (with a
mocked transport — no network needed).

## Roadmap (from the build plan)

- **Week 2** — Finnhub news, Alpha Vantage prices, ticker universe to ~1000
  symbols, Redis price cache.
- **Week 3** — React dashboard, Electron desktop packaging, mobile-responsive
  PWA.
- **Week 4** — Slack/email/push notification channels (the dispatch seam is
  `app/services/alerts.py::_dispatch`), portfolio simulator, GCP Cloud
  Run + Cloud SQL deployment.
