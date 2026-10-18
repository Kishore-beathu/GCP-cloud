"""Finnhub news integration, using a mocked transport."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.integrations.finnhub import (
    FinnhubRateLimited,
    fetch_company_news,
    ingest_finnhub_news,
)
from app.models import NewsArticle

pytestmark = pytest.mark.asyncio

NEWS_PAYLOAD = [
    {
        "category": "company",
        "datetime": 1754732400,
        "headline": "FDA approves Moderna's updated vaccine",
        "id": 1,
        "related": "MRNA",
        "source": "Reuters",
        "summary": "The FDA granted approval following priority review.",
        "url": "https://news.example.com/mrna-approval",
    },
    {
        "category": "company",
        "datetime": 1754646000,
        "headline": "Moderna announces Q2 results",
        "id": 2,
        "related": "MRNA",
        "source": "PR",
        "summary": "",
        "url": "https://news.example.com/mrna-q2",
    },
    # Unusable entries: missing url, missing headline, bad timestamp.
    {"datetime": 1754646000, "headline": "No url here", "url": ""},
    {"datetime": 1754646000, "headline": "", "url": "https://news.example.com/x"},
    {"datetime": "not-a-time", "headline": "Bad ts", "url": "https://news.example.com/y"},
]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_parses_valid_items_and_skips_junk():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "MRNA"
        assert request.url.params["from"] == "2026-08-06"
        assert request.url.params["to"] == "2026-08-09"
        return httpx.Response(200, json=NEWS_PAYLOAD)

    async with _client(handler) as client:
        articles = await fetch_company_news(
            client, "MRNA", "key", date(2026, 8, 6), date(2026, 8, 9)
        )

    assert len(articles) == 2
    first = articles[0]
    assert first.source == "finnhub"
    assert first.published_at == datetime.fromtimestamp(1754732400, tz=timezone.utc)
    assert first.body == "The FDA granted approval following priority review."
    # An empty summary becomes None, not "".
    assert articles[1].body is None


async def test_fetch_raises_on_rate_limit():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "API limit reached"})

    async with _client(handler) as client:
        with pytest.raises(FinnhubRateLimited):
            await fetch_company_news(client, "MRNA", "key", date(2026, 8, 6), date(2026, 8, 9))


async def test_fetch_returns_empty_on_server_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _client(handler) as client:
        assert (
            await fetch_company_news(client, "MRNA", "key", date(2026, 8, 6), date(2026, 8, 9))
            == []
        )


async def test_fetch_handles_non_list_payload():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "wrong shape"})

    async with _client(handler) as client:
        assert (
            await fetch_company_news(client, "MRNA", "key", date(2026, 8, 6), date(2026, 8, 9))
            == []
        )


async def test_ingest_skips_without_api_key(db, seeded_stocks):
    get_settings.cache_clear()
    report = await ingest_finnhub_news(db, ["MRNA"])
    assert report.as_dict()["added"] == 0
    assert (await db.execute(select(func.count(NewsArticle.id)))).scalar_one() == 0


async def test_ingest_stores_articles_with_key(db, seeded_stocks, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=NEWS_PAYLOAD))
        original_client = httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched_client)

        report = await ingest_finnhub_news(db, ["MRNA"])
        assert report.added == 2
        assert report.skipped_duplicate == 0

        # Re-running the same window is a clean no-op thanks to URL dedup.
        report = await ingest_finnhub_news(db, ["MRNA"])
        assert report.added == 0
        assert report.skipped_duplicate == 2
    finally:
        get_settings.cache_clear()
