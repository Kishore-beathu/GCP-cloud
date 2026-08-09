# GCP-cloud — Pharma Trading Intelligence Agent

An AI-powered trading intelligence platform for pharma, life-sciences, and AI
equities. It monitors news sources in real time, scores each story for
financial sentiment and business event type (FDA approvals, trial results,
M&A, recalls, …), fires configurable alerts, streams live updates to clients,
and backtests how news historically moved prices.

## Repository layout

| Path | Contents |
|------|----------|
| [`backend/`](backend/) | FastAPI + PostgreSQL backend: ingestion, sentiment, alerts, WebSocket streaming, backtesting. **Start here** — see [`backend/README.md`](backend/README.md). |
| [`frontend/`](frontend/) | React + TypeScript dashboard: live watchlist, ticker tape, news feed, chart, alerts, backtest view. See [`frontend/README.md`](frontend/README.md). |
| [`desktop/`](desktop/) | Electron shell that packages the dashboard as a Windows desktop app. |
| [`docs/`](docs/) | Deployment guide for Cloud Run + Cloud SQL. |

## Build phases

This follows the accelerated 4-week plan:

1. **Week 1** — Backend MVP: database schema, FastAPI server, sentiment +
   event classification, SEC EDGAR ingestion, alert engine, real-time
   WebSocket, backtesting, scheduler. ✅
2. **Week 2** — Data breadth: Finnhub company news, Alpha Vantage quotes and
   daily-history backfill, rate-limit-aware batch rotation, tooling to grow
   the universe toward ~1000 symbols. ✅
3. **Week 3** — React dashboard with live WebSocket prices, news feed, charts,
   alert UI, and backtest view; Electron desktop shell for Windows;
   mobile-responsive layout with a PWA manifest. ✅
4. **Week 4** — Slack and email alert channels, paper-trading portfolio with a
   sentiment-strategy simulator, Docker image and GCP Cloud Run deployment
   (see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)). ✅
5. **Production hardening** — password authentication on every endpoint and the
   WebSocket, Alembic schema migrations, and GitHub Actions CI. ✅
