"""Alert matching and dispatch.

Every firing is recorded in ``alert_history`` and pushed to connected WebSocket
clients immediately. External channels (Slack, email) are collected into a
``PendingNotification`` list and delivered by the caller *after* the database
transaction commits, so a slow webhook or SMTP server never holds a transaction
open and a delivery failure never costs us the history row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertHistory, AlertType, NewsArticle, SentimentScore, Stock, UserAlert
from app.services.notifications import IN_APP, notify
from app.services.streams import ticker_hub

logger = logging.getLogger(__name__)


@dataclass
class PendingNotification:
    """One firing waiting to go out over its external channels."""

    channels: list[str]
    payload: dict
    condition: dict = field(default_factory=dict)


def _matches(alert: UserAlert, score: SentimentScore) -> bool:
    """Decide whether one article satisfies one alert's condition."""
    condition = alert.condition or {}
    min_score = float(condition.get("min_score", 0.0))
    min_confidence = float(condition.get("min_confidence", 0.0))

    if score.confidence < min_confidence:
        return False

    match alert.alert_type:
        case AlertType.POSITIVE_NEWS.value:
            return score.sentiment == "positive" and score.score >= min_score
        case AlertType.NEGATIVE_NEWS.value:
            return score.sentiment == "negative" and abs(score.score) >= min_score
        case AlertType.SENTIMENT_SPIKE.value:
            return abs(score.score) >= max(min_score, 0.8)
        case AlertType.EVENT_TYPE.value:
            wanted = condition.get("event_type")
            if not wanted:
                return False
            return score.event_type == str(wanted).lower()
        case AlertType.PRICE_CHANGE.value:
            # Price-driven alerts are evaluated by the price refresh job, not here.
            return False
        case _:
            logger.warning("Unknown alert_type %r on alert %s", alert.alert_type, alert.id)
            return False


async def evaluate_alerts_for_article(
    db: AsyncSession,
    article: NewsArticle,
    score: SentimentScore,
    ticker: str | None = None,
    pending: list[PendingNotification] | None = None,
) -> int:
    """Fire every active alert on this article's ticker that matches.

    Returns the number of alerts triggered. External-channel deliveries are
    appended to ``pending`` for the caller to flush post-commit; when no list is
    supplied they are sent inline (convenient for one-off callers and tests).
    """
    result = await db.execute(
        select(UserAlert).where(
            UserAlert.ticker_id == article.ticker_id, UserAlert.is_active.is_(True)
        )
    )
    alerts = list(result.scalars())
    if not alerts:
        return 0

    if ticker is None:
        ticker = await db.scalar(select(Stock.ticker).where(Stock.id == article.ticker_id))

    triggered = 0
    for alert in alerts:
        if not _matches(alert, score):
            continue

        payload = {
            "alert_id": alert.id,
            "article_id": article.id,
            "ticker": ticker,
            "headline": article.headline,
            "url": article.url,
            "source": article.source,
            "sentiment": score.sentiment,
            "score": score.score,
            "confidence": score.confidence,
            "event_type": score.event_type,
        }
        db.add(AlertHistory(alert_id=alert.id, article_id=article.id, payload=payload))
        alert.last_triggered_at = datetime.now(timezone.utc)
        triggered += 1

        channels = list(alert.channels or [IN_APP])
        if IN_APP in channels:
            # In-memory fan-out to WebSocket clients: instant, no I/O to wait on.
            await ticker_hub.broadcast_alert(payload)

        external = [channel for channel in channels if channel != IN_APP]
        if external:
            item = PendingNotification(external, payload, dict(alert.condition or {}))
            if pending is None:
                await deliver(item)
            else:
                pending.append(item)

    return triggered


async def deliver(item: PendingNotification) -> dict:
    """Send one pending notification over its external channels."""
    results = await notify(item.channels, item.payload, item.condition)
    failed = [channel for channel, ok in results.items() if not ok]
    if failed:
        logger.warning(
            "Alert %s: delivery failed on %s",
            item.payload.get("alert_id"),
            ", ".join(failed),
        )
    return results


async def deliver_all(pending: list[PendingNotification]) -> None:
    """Flush a batch of pending notifications. Never raises."""
    for item in pending:
        try:
            await deliver(item)
        except Exception:  # noqa: BLE001 - delivery is best-effort
            logger.exception("Notification delivery raised for alert %s", item.payload.get("alert_id"))
