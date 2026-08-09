"""HTTP endpoint behaviour."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.ingest import RawArticle, store_articles

pytestmark = pytest.mark.asyncio


async def _seed_news(db, ticker: str = "MRNA") -> None:
    now = datetime.now(timezone.utc)
    await store_articles(
        db,
        [
            RawArticle(
                ticker=ticker,
                headline="FDA approves therapy after priority review",
                url="https://example.com/positive",
                source="test_feed",
                published_at=now - timedelta(hours=2),
            ),
            RawArticle(
                ticker=ticker,
                headline="Company recalls lots after FDA warning letter",
                url="https://example.com/negative",
                source="test_feed",
                published_at=now - timedelta(hours=1),
            ),
        ],
    )


async def test_health(client, seeded_stocks):
    response = await client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["sentiment_backend"] == "lexicon"


async def test_list_stocks(client, seeded_stocks):
    response = await client.get("/stocks")
    assert response.status_code == 200
    assert [item["ticker"] for item in response.json()] == ["MRNA", "PFE"]


async def test_stock_detail(client, db, seeded_stocks):
    await _seed_news(db)

    response = await client.get("/stocks/mrna")
    assert response.status_code == 200

    body = response.json()
    assert body["ticker"] == "MRNA"
    assert body["company_name"] == "Moderna Inc."
    assert len(body["recent_news"]) == 2
    # Newest first.
    assert body["recent_news"][0]["headline"].startswith("Company recalls")


async def test_stock_detail_unknown_ticker_404(client, seeded_stocks):
    response = await client.get("/stocks/NOSUCH")
    assert response.status_code == 404


async def test_news_feed_and_sentiment_filter(client, db, seeded_stocks):
    await _seed_news(db)

    response = await client.get("/news")
    assert response.status_code == 200
    assert len(response.json()) == 2

    response = await client.get("/news", params={"sentiment": "positive"})
    articles = response.json()
    assert len(articles) == 1
    assert articles[0]["sentiment"]["sentiment"] == "positive"
    assert articles[0]["ticker"] == "MRNA"


async def test_news_event_type_filter(client, db, seeded_stocks):
    await _seed_news(db)

    response = await client.get("/news", params={"event_type": "recall"})
    articles = response.json()
    assert len(articles) == 1
    assert articles[0]["sentiment"]["event_type"] == "recall"


async def test_news_limit_and_offset(client, db, seeded_stocks):
    await _seed_news(db)

    first = (await client.get("/news", params={"limit": 1})).json()
    second = (await client.get("/news", params={"limit": 1, "offset": 1})).json()
    assert len(first) == len(second) == 1
    assert first[0]["id"] != second[0]["id"]


async def test_alert_lifecycle(client, seeded_stocks):
    created = await client.post(
        "/alerts",
        json={
            "ticker": "mrna",
            "alert_type": "positive_news",
            "condition": {"min_score": 0.5},
            "channels": ["in_app"],
        },
    )
    assert created.status_code == 201
    alert = created.json()
    assert alert["ticker"] == "MRNA"
    assert alert["is_active"] is True

    listed = await client.get("/alerts")
    assert [item["id"] for item in listed.json()] == [alert["id"]]

    deleted = await client.delete(f"/alerts/{alert['id']}")
    assert deleted.status_code == 204

    assert (await client.get("/alerts")).json() == []
    assert len((await client.get("/alerts", params={"active_only": False})).json()) == 1


async def test_create_alert_unknown_ticker_404(client, seeded_stocks):
    response = await client.post(
        "/alerts", json={"ticker": "NOSUCH", "alert_type": "positive_news"}
    )
    assert response.status_code == 404


async def test_delete_missing_alert_404(client, seeded_stocks):
    assert (await client.delete("/alerts/9999")).status_code == 404


async def test_alert_history_endpoint(client, db, seeded_stocks):
    created = await client.post(
        "/alerts", json={"ticker": "MRNA", "alert_type": "positive_news"}
    )
    assert created.status_code == 201

    await _seed_news(db)

    history = await client.get("/alerts/history")
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 1
    assert entries[0]["payload"]["event_type"] == "fda_approval"


async def test_backtest_unknown_ticker_404(client, seeded_stocks):
    response = await client.get("/backtest", params={"ticker": "NOSUCH"})
    assert response.status_code == 404


async def test_jobs_status_when_scheduler_disabled(client):
    response = await client.get("/jobs/status")
    assert response.status_code == 200
    assert response.json() == {"running": False, "jobs": []}
