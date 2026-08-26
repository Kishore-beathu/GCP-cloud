# GCP-cloud — Pharma Trading Intelligence Agent

An AI-powered trading intelligence platform for pharma, life-sciences, and AI
equities. It monitors news sources in real time, scores each story for
financial sentiment and business event type (FDA approvals, trial results,
M&A, recalls, …), fires configurable alerts, streams live updates to clients,
and backtests how news historically moved prices.

## Running it locally

```powershell
.\scripts\start.cmd
```

Use the `.cmd`, not the `.ps1` directly. PowerShell's default execution policy
on Windows desktop refuses to run `.ps1` files at all, and it reports that as a
`SecurityError` that reads like a broken script rather than a machine-wide
setting. The wrapper runs the same script with a bypass scoped to that single
process, so nothing about the system changes.

Starts the backend and the dashboard together and prints the URLs. It picks a
port that will actually bind rather than assuming 8000 is free, and tells the
frontend which one it chose, because the two ways that port gets lost look
nothing alike from the browser: a half-dead process still owns the listening
socket, so connections are accepted and never answered — which reads as a hang
rather than a refusal — while a Windows reserved range (Hyper-V, WSL, Docker)
leaves the port unbindable and unowned, so there is nothing to kill.

It also waits for the API to answer before reporting success. Startup seeds the
universe and starts the scheduler, so "the process is running" and "the API is
serving" are several seconds apart, and that gap is what a frontend stuck on
"Connecting…" is usually showing.

`.\scripts\stop.cmd` stops both. `start.cmd` runs it first, so a launch that
failed partway cannot leave a server behind holding a port — matched on the
repository path, so unrelated Python and Node work is untouched.

To run the two halves by hand instead, see [`backend/README.md`](backend/README.md)
and [`frontend/README.md`](frontend/README.md).

## Repository layout

| Path | Contents |
|------|----------|
| [`backend/`](backend/) | FastAPI + PostgreSQL backend: ingestion, sentiment, alerts, WebSocket streaming, backtesting. **Start here** — see [`backend/README.md`](backend/README.md). |
| [`frontend/`](frontend/) | React + TypeScript dashboard: live watchlist, ticker tape, news feed, chart, alerts, backtest view. See [`frontend/README.md`](frontend/README.md). |
| [`desktop/`](desktop/) | Electron shell that packages the dashboard as a Windows desktop app. |
| [`docs/`](docs/) | [Deployment guide](docs/DEPLOYMENT.md) for Cloud Run + Cloud SQL, and an honest [comparison](docs/COMPARISON.md) with commercial platforms. |

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
