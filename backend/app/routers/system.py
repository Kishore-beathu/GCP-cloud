"""Health, scheduler status, and manual ingestion triggers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, get_session_factory
from app.integrations.alpha_vantage import backfill_daily, update_quotes
from app.integrations.finnhub import ingest_finnhub_news, update_finnhub_quotes
from app.integrations.finnhub_stream import finnhub_stream
from app.integrations.clinical import ingest_clinical_and_regulatory
from app.integrations.edgar_firehose import ingest_recent_filings
from app.integrations.fda import ingest_fda
from app.integrations.halts import ingest_halts
from app.integrations.newswire import ingest_newswires
from app.integrations.sec import ingest_sec_filings
from app.integrations.yahoo_news import ingest_yahoo_news
from app.integrations.yahoo import count_unpriced, update_yahoo_prices
from app.schemas import HealthResponse
from app.security import require_auth
from app.services.rescore import (
    audit_attribution,
    backfill_filing_text,
    repair_article_links,
    rescore_articles,
    stale_count,
)
from app.services.tickers import seed_stocks, tickers_in_group

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@lru_cache(maxsize=1)
def _build() -> str | None:
    """The commit this process was started from, best effort.

    Read once and cached: it cannot change while the process lives, which is
    the entire point of reporting it. Returns None outside a git checkout — a
    container image or an installed package — where the question does not
    arise.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


@router.get("/health", response_model=HealthResponse, summary="Liveness and dependency check")
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    settings = get_settings()
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:
        logger.error("Health check database probe failed: %s", exc)
        database = "unavailable"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        timestamp=datetime.now(timezone.utc),
        environment=settings.environment,
        database=database,
        sentiment_backend=settings.sentiment_backend,
        build=_build(),
    )


@router.get("/jobs/status", summary="Scheduled job and live-stream status", dependencies=[Depends(require_auth)])
async def jobs_status() -> dict:
    from app.scheduler import job_status

    return {**job_status(), "price_stream": finnhub_stream.status()}


@router.post("/admin/seed", summary="Seed the stock universe", dependencies=[Depends(require_auth)])
async def seed(db: AsyncSession = Depends(get_db)) -> dict:
    report = await seed_stocks(db)
    return {"stocks_added": report["added"], **report}


async def _resolve_targets(
    db: AsyncSession, tickers: list[str] | None, group: str | None
) -> list[str] | None:
    """Turn an optional group into a symbol list, keeping None = everything.

    An unknown group is a 422 rather than a silent full-universe run: aiming an
    ingest at "data_storge" and getting all 163 symbols back looks like success.
    """
    if not group:
        return tickers
    try:
        members = await tickers_in_group(db, group)
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if tickers:
        wanted = {ticker.upper() for ticker in tickers}
        return [symbol for symbol in members if symbol.upper() in wanted]
    return members


async def _run_sec_ingest(tickers: list[str] | None) -> None:
    """Run one SEC ingest on its own session, off the request lifecycle."""
    async with get_session_factory()() as session:
        try:
            await ingest_sec_filings(session, tickers)
        except Exception:
            logger.exception("Manual SEC ingest failed")


@router.post("/admin/ingest/sec", status_code=202, summary="Trigger SEC ingestion", dependencies=[Depends(require_auth)])
async def trigger_sec_ingest(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(
        default=None, description="Repeat to limit the run to specific symbols"
    ),
    group: str | None = Query(
        default=None, description="Industry group, e.g. data_storage or ai"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Kick off an SEC EDGAR pull. Returns immediately; the work runs in the background."""
    targets = await _resolve_targets(db, ticker, group)
    background.add_task(_run_sec_ingest, targets)
    return {"status": "accepted", "symbols": len(targets) if targets else "all active"}


async def _run_finnhub_ingest(tickers: list[str] | None) -> None:
    async with get_session_factory()() as session:
        try:
            await ingest_finnhub_news(session, tickers)
        except Exception:
            logger.exception("Manual Finnhub ingest failed")


@router.post("/admin/ingest/finnhub", status_code=202, summary="Trigger Finnhub news ingestion", dependencies=[Depends(require_auth)])
async def trigger_finnhub_ingest(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(
        default=None, description="Repeat to limit the run to specific symbols"
    ),
    group: str | None = Query(
        default=None, description="Industry group, e.g. data_storage or ai"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Kick off a Finnhub news pull. No-op (with a log line) when the key is missing."""
    targets = await _resolve_targets(db, ticker, group)
    background.add_task(_run_finnhub_ingest, targets)
    return {"status": "accepted", "symbols": len(targets) if targets else "all active"}


async def _run_quote_update(tickers: list[str] | None) -> None:
    async with get_session_factory()() as session:
        try:
            await update_quotes(session, tickers)
        except Exception:
            logger.exception("Manual quote update failed")


@router.post("/admin/ingest/prices", status_code=202, summary="Trigger a quote refresh", dependencies=[Depends(require_auth)])
async def trigger_quote_update(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(
        default=None, description="Repeat to limit the run to specific symbols"
    ),
) -> dict:
    """Refresh latest quotes from Alpha Vantage. No-op when the key is missing."""
    background.add_task(_run_quote_update, ticker)
    return {"status": "accepted", "tickers": ticker or "all active"}


@router.post("/admin/backfill/prices", summary="Backfill daily price history for one ticker", dependencies=[Depends(require_auth)])
async def trigger_price_backfill(
    ticker: str = Query(description="Symbol to backfill, e.g. MRNA"),
    outputsize: str = Query(
        default="compact",
        pattern="^(compact|full)$",
        description="compact = ~100 days, full = 20+ years",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Load daily history synchronously (one API call) so backtests have data.

    Runs inline rather than in the background because the caller usually wants
    to know the row counts — and whether the API key is configured — right away.
    """
    try:
        result = await backfill_daily(db, ticker, outputsize)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ticker": ticker.upper(), **result}


async def _run_finnhub_quotes(tickers: list[str] | None) -> None:
    async with get_session_factory()() as session:
        try:
            await update_finnhub_quotes(session, tickers)
        except Exception:
            logger.exception("Manual Finnhub quote refresh failed")


@router.post(
    "/admin/ingest/quotes",
    status_code=202,
    summary="Refresh prices from Finnhub for the whole universe",
    dependencies=[Depends(require_auth)],
)
async def trigger_finnhub_quotes(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(default=None, description="Limit to these symbols"),
    group: str | None = Query(
        default=None, description="Industry group, e.g. data_storage or ai"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Populate the watchlist with prices using the Finnhub key.

    Separate from `/admin/ingest/prices`, which uses Alpha Vantage and is
    limited to roughly 25 calls a day on a free plan.
    """
    targets = await _resolve_targets(db, ticker, group)
    background.add_task(_run_finnhub_quotes, targets)
    return {"status": "accepted", "symbols": len(targets) if targets else "all active"}


@router.post(
    "/admin/ingest/yahoo",
    summary="Load prices and history for symbols no keyed vendor covers",
    dependencies=[Depends(require_auth)],
)
async def trigger_yahoo_prices(
    ticker: list[str] | None = Query(default=None, description="Limit to these symbols"),
    group: str | None = Query(
        default=None, description="Industry group, e.g. data_storage or ai"
    ),
    range_: str = Query(
        default="3mo",
        alias="range",
        pattern="^(1mo|3mo|6mo|1y|2y|5y|10y|max)$",
        description="History window loaded per symbol",
    ),
    only_missing: bool = Query(
        default=True, description="Only symbols with no stored price at all"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Fill in the European and Asia-Pacific listings.

    Runs inline rather than in the background so the caller sees the counts —
    including how many symbols Yahoo had nothing for. With the default
    `only_missing=true` this targets exactly the rows still showing a dash.
    """
    targets = await _resolve_targets(db, ticker, group)
    remaining_before = await count_unpriced(db)
    result = await update_yahoo_prices(db, targets, range_, only_missing)
    return {
        **result,
        "symbols_without_prices_before": remaining_before,
        "symbols_without_prices_after": await count_unpriced(db),
    }


@router.post(
    "/admin/ingest/intraday",
    summary="Record the current session's 5-minute bars",
    dependencies=[Depends(require_auth)],
)
async def trigger_intraday_recording(
    ticker: list[str] | None = Query(default=None, description="Limit to these symbols"),
    group: str | None = Query(default=None, description="Industry group, e.g. ai"),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Symbols per run. One vendor request each, so this is the cost.",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Store intraday bars so the setups can eventually be measured.

    The setups scanner reports conditions that are true right now and cannot
    say how often acting on them paid, because nothing kept the bars. This
    accumulates them. Until enough sessions have been recorded there is
    nothing to measure — see GET /admin/intraday/coverage for progress.
    """
    from app.services.intraday_store import record_intraday

    report = await record_intraday(db, ticker, group, limit)
    return report.as_dict()


@router.get(
    "/admin/intraday/coverage",
    summary="How much intraday history has accumulated",
    dependencies=[Depends(require_auth)],
)
async def intraday_coverage(db: AsyncSession = Depends(get_db)) -> dict:
    """Progress toward being able to measure a setup's hit rate."""
    from app.services.intraday_store import coverage

    return await coverage(db)


# One handler per source, so a caller can name exactly what to pull.
_SOURCE_RUNNERS = {
    "edgar": ingest_recent_filings,
    "fda": ingest_fda,
    "newswire": ingest_newswires,
    "halts": ingest_halts,
    "clinical": ingest_clinical_and_regulatory,
}


@router.post(
    "/admin/ingest/source/{name}",
    summary="Run one news source now and report what it stored",
    dependencies=[Depends(require_auth)],
)
async def trigger_source(name: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Pull from a single source inline, so the counts come back to the caller.

    Inline rather than backgrounded because the reason to call this by hand is
    to find out whether a source works — and a 202 answers a different
    question than the one being asked.
    """
    runner = _SOURCE_RUNNERS.get(name.strip().lower())
    if runner is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown source {name!r}. Available: {', '.join(sorted(_SOURCE_RUNNERS))}",
        )
    report = await runner(db)
    return {"source": name, **report.as_dict()}


@router.post(
    "/admin/ingest/yahoo-news",
    status_code=202,
    summary="Pull per-symbol headlines from Yahoo",
    dependencies=[Depends(require_auth)],
)
async def trigger_yahoo_news(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(default=None, description="Limit to these symbols"),
    group: str | None = Query(default=None, description="Industry group"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The one free news source covering non-US listings."""
    targets = await _resolve_targets(db, ticker, group)
    background.add_task(_run_yahoo_news, targets)
    return {"status": "accepted", "symbols": len(targets) if targets else "all active"}


async def _run_yahoo_news(tickers: list[str] | None) -> None:
    async with get_session_factory()() as session:
        try:
            await ingest_yahoo_news(session, tickers)
        except Exception:
            logger.exception("Manual Yahoo news ingest failed")


@router.get(
    "/admin/diagnose/sources",
    summary="Probe every upstream data source and report what it said",
    dependencies=[Depends(require_auth)],
)
async def diagnose_sources() -> dict:
    """Make one live call per vendor so an empty dashboard has an explanation.

    Ingestion degrades quietly by design, which hides *which* source failed.
    This reports each vendor's own wording. No key is echoed back.
    """
    from app.services.diagnostics import probe_sources

    return await probe_sources(get_settings())


@router.get(
    "/admin/diagnose/news",
    summary="Read every feed source once and report entries seen and matched",
    dependencies=[Depends(require_auth)],
)
async def diagnose_news(db: AsyncSession = Depends(get_db)) -> dict:
    """Separate the three reasons a feed source produces nothing.

    Zero entries means the URL is wrong or the host is refusing us. Entries but
    zero matched means the feed works and nothing in it is about this universe.
    Both look identical from an empty dashboard.
    """
    from app.services.diagnostics import probe_news_sources

    return await probe_news_sources(get_settings(), db)


@router.get(
    "/admin/diagnose/sentiment",
    summary="Is the sentiment pillar carrying information, or near-constant?",
    dependencies=[Depends(require_auth)],
)
async def diagnose_sentiment(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Distinguish "news does not predict" from "news is not being measured".

    Both produce a sentiment pillar that separates nothing in validation, and
    only one of them is a reason to change the weights.
    """
    from app.services.diagnostics import probe_sentiment_distribution

    return await probe_sentiment_distribution(db, days)


@router.post(
    "/admin/news/repair-links",
    summary="Find (and optionally drop) articles whose stored URL is not a URL",
    dependencies=[Depends(require_auth)],
)
async def repair_links(
    apply: bool = Query(
        default=False,
        description="False reports what would be deleted; True deletes it",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Count rows the old feed parser wrote with a guid in place of a link.

    Dry run by default, and the dry run is usually the point: the dashboard
    already renders an unusable link as plain text, so the count measures how
    much stored news predates the parser fix rather than naming work to do.
    Deleting throws away sentiment the scoring pillar reads, for stories the
    feed will not serve again.
    """
    report = await repair_article_links(db, apply=apply)
    return report.as_dict()


@router.post(
    "/admin/news/audit-attribution",
    summary="Find (and optionally drop) news filed under the wrong company",
    dependencies=[Depends(require_auth)],
)
async def audit_news_attribution(
    apply: bool = Query(
        default=False,
        description="False reports what would be deleted; True deletes it",
    ),
    source: list[str] | None = Query(
        default=None,
        description=(
            "Which per-symbol sources to audit. Defaults to yahoo_news; pass "
            "finnhub to measure that feed before deciding whether to prune it."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Count stored Yahoo articles that never name the symbol they are under.

    The per-symbol feed carries general market commentary, and the ingest used
    to treat the request URL as proof of attribution. Rows written before that
    was fixed are still scored, and sentiment is a pillar of the ranked score —
    so another company's good news is currently lifting some symbol's rank.

    Deleting is the right fix here rather than a last resort: this removes a
    score that is wrong about which company it describes, not merely one whose
    hyperlink is broken. Dry run by default all the same.
    """
    report = await audit_attribution(
        db, apply=apply, sources=tuple(source) if source else ("yahoo_news",)
    )
    return report.as_dict()


@router.post(
    "/admin/news/backfill-filing-text",
    summary="Fetch the narrative for 8-Ks already stored without one",
    dependencies=[Depends(require_auth)],
)
async def backfill_filings(
    limit: int = Query(default=200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Apply filing-text reading to filings already in the database.

    Ingestion dedupes on the article URL, so a filing already stored is never
    revisited — which would have left this applying only to filings arriving
    from now on, and never to the corpus the sentiment pillar and the backtest
    actually read.

    Each updated article is rescored in the same pass. A body that changes
    without its score changing is worse than one left alone: it reads as
    evidence the new text carried no sentiment, when nothing looked.
    """
    report = await backfill_filing_text(db, limit=limit)
    return report.as_dict()


@router.get(
    "/admin/diagnose/corporate-actions",
    summary="Find price discontinuities that look like unadjusted splits",
    dependencies=[Depends(require_auth)],
)
async def diagnose_corporate_actions(
    days: int = Query(default=400, ge=30, le=3650),
    ticker: list[str] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """A split nothing adjusted for looks like a 50% crash to every factor.

    Momentum collapses, volatility spikes, the 52-week range position hits its
    floor, and the backtester reads the period as a catastrophe — which then
    feeds the pillar weights. One unadjusted split inside a validation window
    quietly moves the weights of the whole score.

    Detection only. Adjusting needs the ratio, and inferring a ratio from the
    price move is how a genuine crash gets "corrected" into a split that never
    happened, destroying real data to fix imagined data.
    """
    from app.services import corporate_actions

    report = await corporate_actions.detect(db, days=days, tickers=ticker)
    return report.as_dict()


@router.get(
    "/admin/sentiment/status",
    summary="How many stored scores are stale",
    dependencies=[Depends(require_auth)],
)
async def sentiment_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Compare stored scores against the current model version."""
    return await stale_count(db)


@router.post(
    "/admin/sentiment/rescore",
    summary="Re-score stored articles with the current model",
    dependencies=[Depends(require_auth)],
)
async def rescore(
    limit: int = Query(default=1000, ge=1, le=20000),
    only_stale: bool = Query(
        default=True, description="False re-scores everything, not just older versions"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Apply lexicon improvements to news already in the database.

    Runs inline so the caller sees the counts. Alerts are deliberately not
    re-fired: they already fired, or did not, when the news arrived.
    """
    report = await rescore_articles(db, limit=limit, only_stale=only_stale)
    return report.as_dict()
