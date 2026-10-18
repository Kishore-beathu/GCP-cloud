"""Notification channel formatting, dispatch, and failure isolation."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings, get_settings
from app.services import notifications
from app.services.notifications import (
    format_email,
    format_slack_message,
    notify,
    send_email,
    send_slack,
)

PAYLOAD = {
    "alert_id": 7,
    "article_id": 42,
    "ticker": "MRNA",
    "headline": "FDA approves next-generation vaccine",
    "url": "https://news.example.com/mrna",
    "source": "finnhub",
    "sentiment": "positive",
    "score": 0.82,
    "confidence": 0.91,
    "event_type": "fda_approval",
}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_slack_message_shape():
    message = format_slack_message(PAYLOAD)
    assert "MRNA" in message["text"]
    assert message["blocks"][0]["text"]["text"].startswith("*📈 MRNA:")
    context = message["blocks"][1]["elements"][0]["text"]
    assert "score +0.82" in context
    assert "confidence 91%" in context
    assert "event fda approval" in context
    # The link becomes a button.
    assert message["blocks"][2]["elements"][0]["url"] == PAYLOAD["url"]


def test_slack_message_without_url_has_no_button():
    message = format_slack_message({**PAYLOAD, "url": None})
    assert all(block["type"] != "actions" for block in message["blocks"])


def test_email_subject_and_body():
    subject, body = format_email(PAYLOAD)
    assert subject == "[MRNA] POSITIVE: FDA approves next-generation vaccine"
    assert PAYLOAD["url"] in body
    assert "score +0.82" in body


def test_email_subject_is_truncated():
    subject, _ = format_email({**PAYLOAD, "headline": "x" * 400})
    assert len(subject) <= 200


@pytest.mark.asyncio
async def test_slack_skips_without_webhook_url():
    assert await send_slack(PAYLOAD, _settings()) is False


@pytest.mark.asyncio
async def test_slack_posts_and_reports_success(monkeypatch):
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, text="ok")

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))
    settings = _settings(slack_webhook_url="https://hooks.slack.test/abc")

    assert await send_slack(PAYLOAD, settings) is True
    assert captured["url"] == "https://hooks.slack.test/abc"
    assert b"MRNA" in captured["body"]


@pytest.mark.asyncio
async def test_slack_failure_is_swallowed(monkeypatch):
    _patch_httpx(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(500)))
    settings = _settings(slack_webhook_url="https://hooks.slack.test/abc")
    assert await send_slack(PAYLOAD, settings) is False


@pytest.mark.asyncio
async def test_email_skips_without_smtp_config():
    assert await send_email(PAYLOAD, settings=_settings()) is False
    # Host set but no recipients is still a skip.
    assert await send_email(PAYLOAD, settings=_settings(smtp_host="smtp.test")) is False


@pytest.mark.asyncio
async def test_email_sends_via_smtp(monkeypatch):
    sent: dict = {}

    def fake_send(settings, recipients, subject, body):
        sent.update(recipients=recipients, subject=subject, body=body)

    monkeypatch.setattr(notifications, "_send_email_blocking", fake_send)
    settings = _settings(
        smtp_host="smtp.test", email_from="bot@example.com", email_to=["desk@example.com"]
    )

    assert await send_email(PAYLOAD, settings=settings) is True
    assert sent["recipients"] == ["desk@example.com"]
    assert "MRNA" in sent["subject"]


@pytest.mark.asyncio
async def test_email_failure_is_swallowed(monkeypatch):
    def boom(*args):
        raise OSError("connection refused")

    monkeypatch.setattr(notifications, "_send_email_blocking", boom)
    settings = _settings(smtp_host="smtp.test", email_to=["desk@example.com"])
    assert await send_email(PAYLOAD, settings=settings) is False


@pytest.mark.asyncio
async def test_notify_routes_channels(monkeypatch):
    calls: list[str] = []

    async def fake_slack(payload, settings=None):
        calls.append("slack")
        return True

    async def fake_email(payload, recipients=None, settings=None):
        calls.append(f"email:{recipients}")
        return True

    monkeypatch.setattr(notifications, "send_slack", fake_slack)
    monkeypatch.setattr(notifications, "send_email", fake_email)

    results = await notify(
        ["in_app", "slack", "email", "slack"],
        PAYLOAD,
        {"email_to": "trader@example.com"},
    )

    # in_app is the hub's job, not this module's; duplicates collapse.
    assert set(results) == {"slack", "email"}
    assert results == {"slack": True, "email": True}
    assert calls == ["slack", "email:['trader@example.com']"]


@pytest.mark.asyncio
async def test_notify_reports_unknown_channel():
    results = await notify(["carrier_pigeon"], PAYLOAD)
    assert results == {"carrier_pigeon": False}


@pytest.mark.asyncio
async def test_notify_isolates_a_raising_channel(monkeypatch):
    async def explode(payload, settings=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(notifications, "send_slack", explode)
    results = await notify(["slack"], PAYLOAD)
    assert results == {"slack": False}


def _patch_httpx(monkeypatch, transport: httpx.MockTransport) -> None:
    """Route every httpx.AsyncClient in the module under test at a mock transport."""
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(notifications.httpx, "AsyncClient", patched)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
