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

## Build phases

This follows the accelerated 4-week plan:

1. **Week 1 (this phase)** — Backend MVP: database schema, FastAPI server,
   sentiment + event classification, SEC EDGAR ingestion, alert engine,
   real-time WebSocket, backtesting, scheduler. ✅
2. **Week 2** — More data sources (Finnhub news, Alpha Vantage prices), the
   full ~1000-symbol universe, Redis caching.
3. **Week 3** — React dashboard, Electron desktop app, mobile-responsive PWA.
4. **Week 4** — Slack/email/push alert channels, portfolio simulator,
   GCP deployment (Cloud Run + Cloud SQL).
