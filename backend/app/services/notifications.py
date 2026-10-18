"""Outbound notification channels for alert firings.

Each channel is independent and best-effort: a channel that is not configured
is skipped with a log line, and a channel that fails logs the error without
propagating. Notifications are a side effect of ingestion — a broken Slack
webhook must never abort a data pull or lose an ``alert_history`` row.

Channels are addressed by the strings stored in ``user_alerts.channels``:
``in_app`` (handled by the WebSocket hub), ``slack``, and ``email``.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

SLACK = "slack"
EMAIL = "email"
IN_APP = "in_app"

SUPPORTED_CHANNELS = frozenset({IN_APP, SLACK, EMAIL})

_SENTIMENT_EMOJI = {"positive": "📈", "negative": "📉", "neutral": "➖"}


def _headline_line(payload: dict) -> str:
    sentiment = str(payload.get("sentiment", "neutral"))
    emoji = _SENTIMENT_EMOJI.get(sentiment, "•")
    ticker = payload.get("ticker") or "?"
    return f"{emoji} {ticker}: {payload.get('headline', '(no headline)')}"


def _detail_line(payload: dict) -> str:
    score = payload.get("score")
    confidence = payload.get("confidence")
    parts = [
        f"sentiment {payload.get('sentiment', '?')}",
        f"score {score:+.2f}" if isinstance(score, (int, float)) else None,
        f"confidence {confidence:.0%}" if isinstance(confidence, (int, float)) else None,
        f"event {str(payload.get('event_type', 'other')).replace('_', ' ')}",
        f"source {payload.get('source', '?')}",
    ]
    return " · ".join(part for part in parts if part)


def format_slack_message(payload: dict) -> dict:
    """Build a Slack incoming-webhook body for one alert firing."""
    url = payload.get("url")
    headline = _headline_line(payload)
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*"}},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _detail_line(payload)}],
        },
    ]
    if url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Read article"},
                        "url": str(url),
                    }
                ],
            }
        )
    # `text` is the notification preview and the fallback for clients that
    # cannot render blocks.
    return {"text": headline, "blocks": blocks}


def format_email(payload: dict) -> tuple[str, str]:
    """Return (subject, body) for one alert firing."""
    ticker = payload.get("ticker") or "?"
    sentiment = str(payload.get("sentiment", "neutral")).upper()
    subject = f"[{ticker}] {sentiment}: {payload.get('headline', 'Alert')}"[:200]
    body = "\n".join(
        [
            str(payload.get("headline", "")),
            "",
            _detail_line(payload),
            "",
            str(payload.get("url", "")),
            "",
            "— Pharma Trading Intelligence",
        ]
    )
    return subject, body


async def send_slack(payload: dict, settings: Settings | None = None) -> bool:
    """Post one firing to the configured Slack webhook. Returns True on success."""
    settings = settings or get_settings()
    if not settings.slack_webhook_url:
        logger.info("Slack channel requested but SLACK_WEBHOOK_URL is not set")
        return False

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.slack_webhook_url,
                json=format_slack_message(payload),
                timeout=settings.notification_timeout_seconds,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Slack notification failed: %s", exc)
        return False
    return True


def _send_email_blocking(
    settings: Settings, recipients: list[str], subject: str, body: str
) -> None:
    """Synchronous SMTP send, run off the event loop by ``send_email``."""
    message = EmailMessage()
    message["From"] = settings.email_from or (settings.smtp_username or "alerts@localhost")
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(
        settings.smtp_host or "", settings.smtp_port, timeout=settings.notification_timeout_seconds
    ) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_email(
    payload: dict, recipients: list[str] | None = None, settings: Settings | None = None
) -> bool:
    """Email one firing. Returns True on success."""
    settings = settings or get_settings()
    targets = recipients or settings.email_to
    if not settings.smtp_host or not targets:
        logger.info("Email channel requested but SMTP_HOST/EMAIL_TO are not configured")
        return False

    subject, body = format_email(payload)
    try:
        # smtplib is blocking; keep it off the event loop.
        await asyncio.to_thread(_send_email_blocking, settings, targets, subject, body)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("Email notification failed: %s", exc)
        return False
    return True


async def notify(channels: list[str], payload: dict, condition: dict | None = None) -> dict:
    """Fan one firing out to every external channel named in ``channels``.

    ``in_app`` is deliberately not handled here — the WebSocket hub owns it.
    Returns a per-channel result map for logging and tests.
    """
    settings = get_settings()
    condition = condition or {}
    results: dict[str, bool] = {}
    tasks: dict[str, asyncio.Future] = {}

    for channel in dict.fromkeys(channels):  # de-duplicate, preserve order
        if channel == IN_APP:
            continue
        if channel == SLACK:
            tasks[SLACK] = asyncio.ensure_future(send_slack(payload, settings))
        elif channel == EMAIL:
            recipients = condition.get("email_to")
            if isinstance(recipients, str):
                recipients = [recipients]
            tasks[EMAIL] = asyncio.ensure_future(
                send_email(payload, recipients, settings)
            )
        else:
            logger.warning("Unknown notification channel %r; skipping", channel)
            results[channel] = False

    for channel, task in tasks.items():
        try:
            results[channel] = await task
        except Exception:  # noqa: BLE001 - a channel must never break ingestion
            logger.exception("Notification channel %s raised", channel)
            results[channel] = False

    return results
