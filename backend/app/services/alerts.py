"""Alert matching and dispatch.

Week 1 records every firing in ``alert_history`` and pushes it to connected
WebSocket clients. Slack, email, and mobile push land in Week 4 — they plug in
at ``_dispatch`` without touching the matching rules.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertHistory, AlertType, NewsArticle, SentimentScore, UserAlert
from app.services.streams import ticker_hub

logger = logging.getLogger(__name__)


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
    db: AsyncSession, article: NewsArticle, score: SentimentScore
) -> int:
    """Fire every active alert on this article's ticker that matches. Returns the count."""
    result = await db.execute(
        select(UserAlert).where(
            UserAlert.ticker_id == article.ticker_id, UserAlert.is_active.is_(True)
        )
    )
    triggered = 0

    for alert in result.scalars():
        if not _matches(alert, score):
            continue

        payload = {
            "alert_id": alert.id,
            "article_id": article.id,
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
        await _dispatch(alert, payload)

    return triggered


async def _dispatch(alert: UserAlert, payload: dict) -> None:
    """Deliver a firing to its configured channels."""
    channels = alert.channels or ["in_app"]
    if "in_app" in channels:
        await ticker_hub.broadcast_alert(payload)

    unsupported = [c for c in channels if c != "in_app"]
    if unsupported:
        # Explicit and visible rather than silently dropped, so the gap is obvious
        # in logs until the Week 4 notification service lands.
        logger.info(
            "Alert %s: channels %s not yet implemented, delivered in-app only",
            alert.id,
            ", ".join(unsupported),
        )
