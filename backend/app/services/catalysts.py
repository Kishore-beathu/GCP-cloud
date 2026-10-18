"""What is scheduled to happen next, per symbol.

Everything else stored here looks backwards: what was filed, what was
published, what the price did. A catalyst is the opposite — known in advance,
not yet resolved — and it is the only kind of information that answers "what
should I be watching tomorrow" rather than "what happened yesterday".

Two sources, because they are the two with a usable date:

* **Earnings dates**, from the vendor's market-wide calendar. Confirmed by the
  company, so the date is firm.
* **Trial primary completion dates**, from ClinicalTrials.gov. These are the
  sponsor's own estimate and they slip constantly, so they are stored as
  estimated and reported as such. A calendar that presented them with the same
  confidence as an earnings date would be lying about both.

**What is deliberately absent: PDUFA dates.** They are the single most
valuable catalyst in this universe and there is no free structured feed for
them. They appear in company press releases, so the news pipeline sees them as
text — but parsing a date out of prose and presenting it as a scheduled event
is how a calendar ends up confidently wrong. Left out rather than guessed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.clinical import CLINICAL_TRIALS_URL
from app.integrations.finnhub import (
    FinnhubRateLimited,
    FinnhubNotCovered,
    FinnhubRejected,
    fetch_earnings_calendar,
)
from app.models import CatalystEvent, Stock
from app.services.matching import build_index, match_tickers

logger = logging.getLogger(__name__)

# How far ahead to look. The spec asked for 1-10 trading days; a fortnight of
# calendar days covers that with room for a long weekend.
HORIZON_DAYS = 14

# Statuses where a primary completion date means a readout is plausibly coming.
# A completed trial has already reported; a withdrawn one never will.
READOUT_STATUSES = frozenset({"RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"})


@dataclass
class CatalystReport:
    """What one calendar refresh found."""

    earnings: int = 0
    readouts: int = 0
    horizon_days: int = HORIZON_DAYS
    note: str | None = None
    sources_failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "earnings": self.earnings,
            "readouts": self.readouts,
            "horizon_days": self.horizon_days,
            "note": self.note,
            "sources_failed": self.sources_failed,
        }


async def refresh_calendar(
    db: AsyncSession, horizon_days: int = HORIZON_DAYS
) -> CatalystReport:
    """Rebuild the forward calendar from every source that has dates.

    Rebuild rather than append: a scheduled date that has moved should move
    here too, and an event that has been cancelled should disappear. Only
    future events are replaced — past ones are left alone, because what was
    scheduled and then happened is a record worth keeping.
    """
    report = CatalystReport(horizon_days=horizon_days)
    settings = get_settings()
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=horizon_days)

    by_symbol = {
        stock.ticker.upper(): stock
        for stock in (
            await db.execute(select(Stock).where(Stock.is_active.is_(True)))
        ).scalars()
    }
    if not by_symbol:
        report.note = "No active symbols."
        return report

    fresh: list[CatalystEvent] = []

    if settings.finnhub_api_key:
        try:
            fresh.extend(await _earnings_events(by_symbol, settings.finnhub_api_key, now, until))
        except (FinnhubRejected, FinnhubRateLimited, FinnhubNotCovered) as exc:
            report.sources_failed.append("finnhub_earnings_calendar")
            logger.warning("Earnings calendar unavailable: %s", exc)
    else:
        report.note = "FINNHUB_API_KEY is not set; earnings dates are unavailable."

    try:
        fresh.extend(await _trial_readouts(db, now, until))
    except Exception:  # noqa: BLE001 - one source must not sink the calendar
        report.sources_failed.append("clinicaltrials")
        logger.exception("Trial readout lookup failed")

    # Clear the future window before writing, so a moved date does not leave a
    # ghost behind at the old one.
    await db.execute(
        delete(CatalystEvent).where(
            CatalystEvent.expected_at >= now, CatalystEvent.expected_at <= until
        )
    )
    for event in fresh:
        db.add(event)
        if event.kind == "earnings":
            report.earnings += 1
        else:
            report.readouts += 1

    await db.commit()
    logger.info("Catalyst calendar refreshed: %s", report.as_dict())
    return report


async def _earnings_events(
    by_symbol: dict[str, Stock], api_key: str, now: datetime, until: datetime
) -> list[CatalystEvent]:
    """Scheduled earnings, filtered to the tracked universe."""
    async with httpx.AsyncClient() as client:
        rows = await fetch_earnings_calendar(client, api_key, now.date(), until.date())

    events: list[CatalystEvent] = []
    for row in rows:
        stock = by_symbol.get(row["symbol"])
        if stock is None:
            continue

        when = row["expected_at"]
        timing = {"bmo": "before the open", "amc": "after the close"}.get(
            row["hour"], row["hour"] or "time unconfirmed"
        )
        estimate = row.get("eps_estimate")
        detail = f"Reporting {timing}."
        if estimate is not None:
            detail += f" Consensus EPS {estimate}."

        events.append(
            CatalystEvent(
                ticker_id=stock.id,
                kind="earnings",
                expected_at=when,
                # The company has announced this date; it is not a guess.
                confidence="confirmed",
                title=f"{stock.ticker} Q{row.get('quarter') or '?'} earnings",
                detail=detail,
                source="finnhub",
                external_id=f"{row['symbol']}:{when:%Y-%m-%d}",
            )
        )
    return events


async def _trial_readouts(
    db: AsyncSession, now: datetime, until: datetime
) -> list[CatalystEvent]:
    """Trials whose primary completion falls inside the horizon.

    Matched on the sponsor only, like the news ingest: a trial title names the
    drug and the indication, and a competitor's compound in it would attribute
    the readout to the wrong company.
    """
    index = await build_index(db)
    by_ticker = {
        stock.ticker: stock
        for stock in (
            await db.execute(select(Stock).where(Stock.is_active.is_(True)))
        ).scalars()
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            CLINICAL_TRIALS_URL,
            params={
                "filter.advanced": (
                    f"AREA[PrimaryCompletionDate]RANGE"
                    f"[{now:%Y-%m-%d},{until:%Y-%m-%d}]"
                ),
                "pageSize": 200,
                "format": "json",
            },
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
    if response.status_code != 200:
        logger.warning("ClinicalTrials.gov returned HTTP %s", response.status_code)
        return []

    try:
        payload = response.json()
    except ValueError:
        return []

    events: list[CatalystEvent] = []
    for study in payload.get("studies") or []:
        section = study.get("protocolSection") or {}
        identification = section.get("identificationModule") or {}
        status_module = section.get("statusModule") or {}
        sponsors = section.get("sponsorCollaboratorsModule") or {}
        design = section.get("designModule") or {}

        nct_id = identification.get("nctId")
        title = identification.get("briefTitle")
        status = (status_module.get("overallStatus") or "").upper()
        if not nct_id or not title or status not in READOUT_STATUSES:
            continue

        when = _parse_partial_date(
            (status_module.get("primaryCompletionDateStruct") or {}).get("date")
        )
        if when is None or not (now <= when <= until):
            continue

        sponsor = ((sponsors.get("leadSponsor") or {}).get("name") or "").strip()
        matches = match_tickers(sponsor, index, limit=1)
        if not matches:
            continue
        stock = by_ticker.get(matches[0])
        if stock is None:
            continue

        phases = ", ".join((design.get("phases") or [])) or "phase not stated"
        events.append(
            CatalystEvent(
                ticker_id=stock.id,
                kind="trial_readout",
                expected_at=when,
                # The sponsor's own estimate, and they slip constantly.
                confidence="estimated",
                title=title[:512],
                detail=f"{phases}; primary completion estimated. Sponsor: {sponsor}.",
                url=f"https://clinicaltrials.gov/study/{nct_id}",
                source="clinicaltrials",
                external_id=nct_id,
            )
        )
    return events


def _parse_partial_date(value: object) -> datetime | None:
    """ClinicalTrials.gov dates are sometimes only a month: "2026-09".

    A month-only date is read as the first of that month rather than
    discarded — knowing a readout is due in September is useful even when the
    day is not known, and `confidence` already says the whole date is an
    estimate.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def upcoming(
    db: AsyncSession,
    days: int = 7,
    kind: str | None = None,
    tickers: list[str] | None = None,
) -> list[dict]:
    """The calendar, soonest first."""
    now = datetime.now(timezone.utc)
    query = (
        select(CatalystEvent, Stock)
        .join(Stock, Stock.id == CatalystEvent.ticker_id)
        .where(
            CatalystEvent.expected_at >= now,
            CatalystEvent.expected_at <= now + timedelta(days=days),
        )
        .order_by(CatalystEvent.expected_at)
    )
    if kind:
        query = query.where(CatalystEvent.kind == kind)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))

    rows = (await db.execute(query)).all()
    return [
        {
            "ticker": stock.ticker,
            "company_name": stock.company_name,
            "kind": event.kind,
            "expected_at": event.expected_at.isoformat(),
            # Calendar days, not elapsed time. Truncating the difference makes
            # an event 47 hours away report "1 day", and something at 09:00
            # tomorrow report "0" — which reads as today on a calendar whose
            # entire purpose is telling you what is coming tomorrow.
            "days_away": max(0, (_aware(event.expected_at).date() - now.date()).days),
            "confidence": event.confidence,
            "title": event.title,
            "detail": event.detail,
            "url": event.url,
            "source": event.source,
        }
        for event, stock in rows
    ]


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
