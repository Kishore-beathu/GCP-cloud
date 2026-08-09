"""SEC EDGAR parsing, using a stubbed transport so no network is touched."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.sec import (
    INTERESTING_FORMS,
    fetch_sec_filings,
    fetch_ticker_cik_map,
)

pytestmark = pytest.mark.asyncio


SUBMISSIONS_PAYLOAD = {
    "name": "Moderna, Inc.",
    "filings": {
        "recent": {
            "form": ["8-K", "4", "10-Q", "SC 13G"],
            "accessionNumber": [
                "0001682852-25-000101",
                "0001682852-25-000102",
                "0001682852-25-000103",
                "0001682852-25-000104",
            ],
            "filingDate": ["2025-08-01", "2025-07-30", "2025-07-25", "2025-07-20"],
            "primaryDocument": ["a8k.htm", "form4.xml", "a10q.htm", "sc13g.htm"],
            "primaryDocDescription": ["8-K", "FORM 4", "10-Q", "SC 13G"],
            "items": ["8.01,9.01", "", "", ""],
        }
    },
}

TICKER_MAP_PAYLOAD = {
    "0": {"cik_str": 1682852, "ticker": "MRNA", "title": "Moderna, Inc."},
    "1": {"cik_str": 78003, "ticker": "PFE", "title": "Pfizer Inc."},
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_ticker_cik_map_zero_pads():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=TICKER_MAP_PAYLOAD)

    async with _client(handler) as client:
        mapping = await fetch_ticker_cik_map(client)

    assert mapping["MRNA"] == "0001682852"
    assert mapping["PFE"] == "0000078003"


async def test_fetch_filings_keeps_only_interesting_forms():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SUBMISSIONS_PAYLOAD)

    async with _client(handler) as client:
        articles = await fetch_sec_filings(client, "MRNA", "0001682852")

    forms = {a.headline.split("filed ")[1].split(":")[0] for a in articles}
    assert forms <= INTERESTING_FORMS
    assert forms == {"8-K", "10-Q"}


async def test_filing_article_fields():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SUBMISSIONS_PAYLOAD)

    async with _client(handler) as client:
        articles = await fetch_sec_filings(client, "MRNA", "0001682852")

    first = articles[0]
    assert first.ticker == "MRNA"
    assert first.source == "sec_edgar"
    assert first.published_at.year == 2025
    # CIK is unpadded in archive URLs and the accession number loses its dashes.
    assert "/edgar/data/1682852/000168285225000101/a8k.htm" in first.url
    assert "8.01,9.01" in first.body


async def test_limit_is_respected():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SUBMISSIONS_PAYLOAD)

    async with _client(handler) as client:
        articles = await fetch_sec_filings(client, "MRNA", "0001682852", limit=1)

    assert len(articles) == 1


async def test_http_error_returns_empty_list():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    async with _client(handler) as client:
        assert await fetch_sec_filings(client, "MRNA", "0001682852") == []


async def test_malformed_payload_returns_empty_list():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async with _client(handler) as client:
        assert await fetch_sec_filings(client, "MRNA", "0001682852") == []


async def test_unparsable_date_is_skipped():
    payload = json.loads(json.dumps(SUBMISSIONS_PAYLOAD))
    payload["filings"]["recent"]["filingDate"][0] = "not-a-date"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        articles = await fetch_sec_filings(client, "MRNA", "0001682852")

    assert len(articles) == 1
    assert "10-Q" in articles[0].headline


async def test_user_agent_header_is_sent():
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["user-agent"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json=SUBMISSIONS_PAYLOAD)

    async with _client(handler) as client:
        await fetch_sec_filings(client, "MRNA", "0001682852")

    # The SEC blocks requests without a descriptive, contactable User-Agent.
    assert seen["user-agent"]
    assert "python-httpx" not in seen["user-agent"]
