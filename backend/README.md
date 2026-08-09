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
| GET | `/stocks` | Search the universe: `q`, `sector`, `region`, `country`, `mic`, `currency` |
| GET | `/stocks/markets` | Venues tracked, per-region counts, and which are open now |
| GET | `/stocks/{ticker}` | Detail: latest price + recent scored news |
| POST | `/alerts` | Create an alert (`positive_news`, `negative_news`, `sentiment_spike`, `event_type`, `price_change`) |
| GET | `/alerts` | List alerts; `/alerts/history` shows firings |
| DELETE | `/alerts/{id}` | Deactivate (history is kept) |
| GET | `/backtest?ticker=MRNA&days=90` | Price impact by event type + signal accuracy |
| GET | `/portfolios` | List paper-trading portfolios |
| POST | `/portfolios` | Create one (`{"name": "...", "starting_cash": 100000}`) |
| GET | `/portfolios/{id}` | Positions and valuation at latest prices |
| POST | `/portfolios/{id}/trades` | Record a buy/sell (price optional — defaults to latest close) |
| GET | `/portfolios/{id}/trades` | Trade log, newest first |
| POST | `/portfolios/{id}/simulate` | Replay the sentiment strategy over stored history |
| GET | `/jobs/status` | Scheduler state and next run times |
| POST | `/admin/seed` | Re-run universe seeding |
| POST | `/admin/ingest/sec` | Trigger an SEC pull now (optional `?ticker=MRNA&ticker=PFE`) |
| POST | `/admin/ingest/finnhub` | Trigger a Finnhub news pull now |
| POST | `/admin/ingest/prices` | Trigger an Alpha Vantage quote refresh now |
| POST | `/admin/backfill/prices` | Load daily price history for one ticker (`?ticker=MRNA&outputsize=full`) |
| GET | `/admin/sentiment/status` | How many stored scores came from an older model version |
| POST | `/admin/sentiment/rescore` | Re-score stored news with the current lexicon |
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

The lexicon matches on **word boundaries**, which matters more than it sounds:
plain substring search made "sub*miss*ion" and "com*miss*ion" fire the negative
term "miss", so *"Regulatory submission accepted for review"* — a real catalyst
— scored maximally negative, and *"European Commission approves…"* was dragged
toward neutral. Negators are word-bounded too ("a*not*her", "*not*able" used to
invert good news) and do not reach across a clause boundary, so in *"did not
meet the endpoint, but the label expansion was approved"* the negator stays
with the first clause.

`LexiconAnalyzer.explain()` returns every term that fired, its weight and
whether it was negated — use it when tuning the vocabulary against real
headlines. `tests/corpus.py` holds the labelled headlines the accuracy tests
run against; add a case whenever a real headline scores wrongly, then extend
the lexicon until it passes.

Both backends also classify each headline into a business event taxonomy
(`fda_approval`, `clinical_trial`, `revenue`, `merger_acquisition`, `recall`,
`partnership`, `litigation`, `exec_change`, `facility`, `analyst_rating`,
`capital_raise`, `other`) — alerts and backtests key off these.

## Markets and regions

The universe spans **North America, Europe and Asia-Pacific**. A listing's
venue, country, currency and trading session are resolved from the vendor
symbol suffix (`app/services/markets.py`) and stored on the row, so they are
filterable without re-parsing symbols per query:

```bash
curl "localhost:8000/stocks?region=europe&sector=biotech"
curl "localhost:8000/stocks?country=JP"
curl "localhost:8000/stocks?mic=XLON"
curl "localhost:8000/stocks?q=novo"        # finds NVO (ADR) and NOVO-B.CO (home line)
curl "localhost:8000/stocks/markets"       # per-region counts, sessions, open now
```

| Region | Example symbols |
|--------|-----------------|
| `north_america` | `PFE`, `SHOP.TO`, `WALMEX.MX` |
| `europe` | `AZN.L`, `SAN.PA`, `ROG.SW`, `NOVO-B.CO`, `BAYN.DE` |
| `asia_pacific` | `4502.T`, `2269.HK`, `207940.KS`, `SUNPHARMA.NS`, `CSL.AX` |

Two regional details are encoded deliberately, because both cause silent errors:

- **London quotes in pence.** Its currency code is `GBp`, and
  `markets.normalise_price()` divides by 100. Without it a London price reads
  100x too high next to a US cross-listing.
- **Sessions do not overlap.** Tokyo closes before New York opens, so a quiet
  price stream at 09:00 UTC is a closed market rather than a broken feed.
  `GET /stocks/markets` reports which venues are trading right now.

**SEC EDGAR only covers US registrants.** European and Asian companies without
a US listing file with their home regulator, so for those names the platform
relies on Finnhub news alone. The seed universe therefore carries both lines
for the big names — `NVO` and `NOVO-B.CO`, `AZN` and `AZN.L` — because the ADR
brings SEC filings and US-hours liquidity while the home line brings the
domestic session and local currency.

Growing the universe across regions:

```bash
FINNHUB_API_KEY=... .venv/bin/python scripts/build_universe.py \
    --exchange US,L,PA,AS,DE,SW,CO,ST,MI,MC,BR,TO,T,HK,SS,KS,NS,AX --limit 1000
```

## Data sources

| Source | What it provides | Requirement |
|--------|------------------|-------------|
| SEC EDGAR | 8-K/10-K/10-Q/6-K filings via the `data.sec.gov` JSON APIs, with item codes expanded to their official titles | A descriptive `SEC_USER_AGENT` with a contact address (anonymous requests are rejected) |
| Finnhub | Company news headlines + summaries | `FINNHUB_API_KEY` |
| Finnhub WebSocket | **Live trade ticks** — the only real-time source | `FINNHUB_API_KEY` (plans cap concurrent symbols) |
| Alpha Vantage | Latest quotes (`GLOBAL_QUOTE`) + daily history (`TIME_SERIES_DAILY`) | `ALPHA_VANTAGE_API_KEY` |

Missing keys degrade gracefully: the source logs a skip and everything else
keeps running. Rate limits are respected with inter-call delays, and both APIs'
throttle responses stop the current batch cleanly instead of erroring.

**Check your plans' actual quotas before trusting the defaults.** Alpha
Vantage in particular has run free tiers as tight as a few dozen requests per
*day*, which the default 60-second quote job would exhaust in minutes. If that
is your plan, raise `ALPHA_VANTAGE_INTERVAL_SECONDS` (3600 or more) or leave
quotes to on-demand backfills — no code change needed.

After adding an Alpha Vantage key, backfill history for your main tickers so
backtests have data on day one:

```bash
curl -X POST "http://localhost:8000/admin/backfill/prices?ticker=MRNA&outputsize=full"
```

## Re-scoring stored news

Sentiment is stored, not recomputed on read, so improving the lexicon does
nothing for news already in the database. Each `sentiment_scores` row records
the `model_version` that produced it, which makes the stale rows identifiable:

```bash
curl -H "Authorization: Bearer $TOKEN" localhost:8000/admin/sentiment/status
# {"current_model": "lexicon-v2", "by_model_version": {"lexicon-v1": 412}, "stale": 412}

curl -X POST -H "Authorization: Bearer $TOKEN" \
  "localhost:8000/admin/sentiment/rescore?limit=5000"
# {"examined": 412, "updated": 137, "unchanged": 275, "sentiment_flipped": 41}
```

Re-scoring deliberately does **not** re-fire alerts — they already fired, or
did not, when the news arrived, and replaying months of history into a Slack
channel would be worse than useless.

## Real-time prices

Alpha Vantage is REST-only, so its prices are polled. **Finnhub's WebSocket is
the only true streaming source**, and the backend keeps one connection to it
(`app/integrations/finnhub_stream.py`) feeding trade ticks straight into the
browser WebSocket. It starts automatically when `FINNHUB_API_KEY` is set.

Four behaviours make it safe to leave running:

- **Coalescing** — a liquid symbol can print many trades a second, so ticks
  update an in-memory map and flush at most one message per symbol per second.
- **Demand-driven subscriptions** — only tickers someone is actually watching
  are subscribed upstream, re-checked every few seconds and capped at
  `FINNHUB_STREAM_MAX_SYMBOLS` (plans limit concurrent symbols). Closing the
  last browser tab on a ticker releases its slot.
- **No double-pushing** — symbols carried by the stream are skipped by the
  polling job, so the UI never flickers between a live trade price and an older
  stored close.
- **Self-healing** — disconnects reconnect with exponential backoff, and a
  stream outage silently falls back to polled prices.

`GET /jobs/status` reports it under `price_stream` (connected, subscribed
symbols, live price count). Set `FINNHUB_STREAM_ENABLED=false` to turn it off
and rely on polling alone.

## Background jobs (APScheduler)

| Job | Default cadence | What it does |
|-----|-----------------|--------------|
| SEC EDGAR ingestion | every 30 min | Rotates through the watchlist in batches, pulls recent filings, scores and stores them |
| Finnhub news ingestion* | every 5 min | Rotates 50-ticker batches through `/company-news`, deduped on URL |
| Alpha Vantage quote refresh* | every 60 s | Refreshes latest quotes; tickers with live WebSocket subscribers jump the queue |
| WebSocket price push | every 10 s | Pushes the latest stored close to subscribed clients — **skipping symbols the live stream already covers** |
| Retention cleanup | weekly | Deletes news/prices older than `DATA_RETENTION_DAYS` |

\* Only registered when the corresponding API key is configured, so
`/jobs/status` reflects what is actually running.

## Alert notification channels

Alerts always deliver in-app over the WebSocket. Two external channels are
available, each activating only once configured:

| Channel | Setting | Notes |
|---------|---------|-------|
| `slack` | `SLACK_WEBHOOK_URL` | Slack Incoming Webhook; posts a formatted block message with a link button |
| `email` | `SMTP_HOST`, `EMAIL_TO`, … | Plain SMTP. Gmail requires an App Password, not your account password |

Name them when creating an alert:

```bash
curl -X POST http://localhost:8000/alerts -H 'Content-Type: application/json' -d '{
  "ticker": "MRNA",
  "alert_type": "event_type",
  "condition": {"event_type": "fda_approval", "email_to": "desk@example.com"},
  "channels": ["in_app", "slack", "email"]
}'
```

`condition.email_to` overrides the default recipients for that one alert.

Delivery is best-effort and deliberately isolated: notifications are sent
*after* the firing is committed to `alert_history`, so a broken webhook or a
dead SMTP server can never lose a record or abort an ingestion run. Failures
are logged, not raised.

## Portfolio simulator

Paper-trade against stored prices, or replay the sentiment signal to see how it
would have performed:

```bash
# Create a portfolio
curl -X POST http://localhost:8000/portfolios -H 'Content-Type: application/json' \
  -d '{"name": "Paper", "starting_cash": 100000}'

# Trade manually (omit "price" to fill at the latest stored close)
curl -X POST http://localhost:8000/portfolios/1/trades -H 'Content-Type: application/json' \
  -d '{"ticker": "MRNA", "side": "buy", "quantity": 100}'

# Replay the strategy: buy each positive story, hold 5 days, exit early on bad news
curl -X POST http://localhost:8000/portfolios/1/simulate -H 'Content-Type: application/json' \
  -d '{"days": 180, "hold_days": 5, "position_size_pct": 10}'
```

**Multi-currency holdings are not FX-converted.** A portfolio can hold JPY,
EUR and USD lines at once, and adding those numbers together produces a figure
that means nothing. The valuation reports `positions_by_currency` and sets
`mixed_currency: true` rather than presenting a misleading total; converting
properly needs an FX rate source the platform does not have yet.

The trade log is the source of truth — positions and average cost are derived
by replaying it, so they cannot drift. Holdings with no price history are
valued at cost rather than dropped, so totals stay meaningful before a
backfill. Simulated trades accumulate in the portfolio, so use a fresh one per
run.

The simulator is a teaching tool for the signal, not a production backtester:
fills are close-to-close with no slippage, commissions, or shorting.

## Growing the ticker universe

`scripts/build_universe.py` generates `data/tickers.csv` from Finnhub's US
symbol directory, keyword-filtered to pharma/life-sciences names:

```bash
FINNHUB_API_KEY=... .venv/bin/python scripts/build_universe.py --dry-run   # preview
FINNHUB_API_KEY=... .venv/bin/python scripts/build_universe.py --limit 1000
# then restart the API or call POST /admin/seed
```

Review the CSV before committing — keyword matching is deliberately broad.

## Authentication

The API is unauthenticated by default, which suits local development. Setting
`AUTH_PASSWORD` turns enforcement on — there is no separate flag to forget:

```
AUTH_PASSWORD=choose-a-strong-password
SECRET_KEY=<python -c "from app.security import generate_secret_key; print(generate_secret_key())">
```

With those set, everything except `GET /health` and `POST /auth/login` needs a
bearer token:

```bash
TOKEN=$(curl -sX POST localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"..."}' | python -c 'import json,sys;print(json.load(sys.stdin)["token"])')

curl -H "Authorization: Bearer $TOKEN" localhost:8000/stocks
```

Tokens are HMAC-signed (stdlib only, no new dependencies), expire after
`AUTH_TOKEN_TTL_SECONDS`, and the dashboard handles sign-in and renewal for
you. The WebSocket takes the token as a `?token=` query parameter, because
browsers cannot set headers on a WebSocket handshake.

**A production deployment refuses to start without this.** `ENVIRONMENT=production`
plus a missing `AUTH_PASSWORD` or `SECRET_KEY` raises at startup rather than
quietly exposing your data and your API quota — `/admin/ingest/*` is exactly
the endpoint a stranger would use to burn your Finnhub allowance.

Failed sign-ins are throttled per client (`LOGIN_MAX_ATTEMPTS` per
`LOGIN_WINDOW_SECONDS`).

## Database schema changes

`app/models.py` is the source of truth. Two ways to get it into a database:

- **Local development** — `CREATE_TABLES_ON_STARTUP=true` (the default) creates
  any missing tables at boot. It never `ALTER`s an existing table, so it cannot
  apply a change to a column that already exists.
- **Anything you care about** — Alembic migrations:

  ```bash
  alembic upgrade head                          # apply pending migrations
  alembic revision --autogenerate -m "add x"    # after changing a model
  alembic downgrade -1                          # step back
  ```

  Alembic reads `DATABASE_URL` from the app's settings, so there is no second
  config to keep in sync and no credentials in `alembic.ini`.

**Adopting migrations on a database that already has tables** (for example a
Supabase project created by `create_all`) — tell Alembic the baseline is
already applied, once:

```bash
alembic stamp head
```

Then set `CREATE_TABLES_ON_STARTUP=false` and use `alembic upgrade head` from
then on. CI fails the build if a model changes without a matching revision.

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

274 tests cover sentiment scoring, event classification, ingestion/dedup, alert
matching and delivery, the REST API, the WebSocket hub, backtesting, scheduler
batch rotation, portfolio maths and simulation, and every data integration
(SEC, Finnhub REST, Alpha Vantage) against mocked transports. The live price
stream is covered end to end against a real WebSocket server on localhost.
Authentication is covered by token-forgery attempts and a parametrised sweep
asserting every protected route rejects anonymous callers. Sentiment accuracy is
measured against a labelled corpus of realistic headlines rather than asserted.
No network needed.

## Roadmap (from the build plan)

- **Week 2 (done)** — Finnhub news, Alpha Vantage quotes + history backfill,
  universe tooling for ~1000 symbols.
- **Week 3 (done)** — React dashboard, Electron desktop packaging,
  mobile-responsive PWA.
- **Week 4 (done)** — Slack and email notification channels, portfolio
  simulator, Docker image and GCP Cloud Run deployment (see
  [`../docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md)).

Possible next steps: Alembic migrations before the first destructive schema
change, Redis pub/sub if the WebSocket hub ever needs to span more than one
instance, and mobile push (APNs/FCM) which would slot in beside Slack and email
in `app/services/notifications.py`.
