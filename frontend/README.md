# Trading Intelligence Dashboard

React + TypeScript dashboard for the trading agent backend: live watchlist,
real-time ticker tape (WebSocket), scored news feed with sentiment/event
filters, price chart, alert management, alert toasts, and a backtest view.
Fully responsive — the same build serves desktop browsers, mobile, and the
Electron shell in `../desktop`.

## Run it

```bash
cd frontend
npm install
cp .env.example .env      # only needed if the API isn't on localhost:8000
npm run dev               # http://localhost:3000
```

The backend must be running (see `../backend/README.md`). The WebSocket URL is
derived from `VITE_API_URL` automatically.

Production build: `npm run build` → static files in `dist/`, deployable to any
static host (Vercel, GCS bucket, nginx). Set `VITE_API_URL` at build time to
point at the deployed API.

## What's on screen

- **Ticker tape + watchlist** — live prices and % change pushed over one
  WebSocket; click any ticker to focus it. Search filters the watchlist.
- **Price chart** — daily closes (1M/3M/1Y/5Y) from `/stocks/{ticker}/prices`.
  Empty until prices are ingested or backfilled.
- **News feed** — scored articles with sentiment and event badges, filterable
  by sentiment and event type.
- **Alerts** — create/remove alerts, see recent firings; live firings also pop
  up as toasts bottom-right.
- **Backtest** — 180-day price impact by event type and signal accuracy.

## Desktop app (Windows)

The Electron shell lives in `../desktop` and wraps this build:

```bash
cd frontend && npm run build          # build the web app first
cd ../desktop && npm install
npm start                             # run as a desktop window
npm run dist                          # package a Windows installer into release/
```

`npm start` also accepts `APP_URL=http://localhost:3000` to point the window at
the Vite dev server during development.
