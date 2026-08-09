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
    body = response.json()
    assert body["running"] is False
    assert body["jobs"] == []
    # The live stream reports itself even when it is not running.
    assert body["price_stream"] == {
        "enabled": False,
        "connected": False,
        "subscribed": [],
        "live_prices": 0,
    }


async def test_price_history_endpoint(client, db, seeded_stocks):
    from datetime import datetime, timedelta, timezone

    from app.models import StockPrice

    mrna = seeded_stocks[0]
    now = datetime.now(timezone.utc)
    for offset, close in enumerate([100.0, 101.0, 102.0]):
        db.add(
            StockPrice(
                ticker_id=mrna.id,
                close=close,
                price_date=now - timedelta(days=2 - offset),
                source="test",
            )
        )
    await db.commit()

    response = await client.get("/stocks/MRNA/prices", params={"days": 30})
    assert response.status_code == 200
    closes = [row["close"] for row in response.json()]
    assert closes == [100.0, 101.0, 102.0]  # oldest first

    assert (await client.get("/stocks/NOSUCH/prices")).status_code == 404


async def test_portfolio_crud_and_valuation(client, db, seeded_stocks):
    from datetime import datetime, timezone

    from app.models import StockPrice

    created = await client.post(
        "/portfolios", json={"name": "Paper", "starting_cash": 5000.0}
    )
    assert created.status_code == 201
    portfolio_id = created.json()["id"]
    assert created.json()["cash"] == 5000.0

    listed = await client.get("/portfolios")
    assert [p["id"] for p in listed.json()] == [portfolio_id]

    # A trade with an explicit price needs no stored history.
    trade = await client.post(
        f"/portfolios/{portfolio_id}/trades",
        json={"ticker": "mrna", "side": "buy", "quantity": 10, "price": 100.0},
    )
    assert trade.status_code == 201
    assert trade.json()["ticker"] == "MRNA"

    detail = (await client.get(f"/portfolios/{portfolio_id}")).json()
    assert detail["cash"] == 4000.0
    assert detail["positions"][0]["quantity"] == 10
    assert detail["total_value"] == 5000.0  # unpriced: valued at cost

    # Once a price exists, the valuation follows it.
    db.add(
        StockPrice(
            ticker_id=seeded_stocks[0].id,
            close=150.0,
            price_date=datetime.now(timezone.utc),
            source="test",
        )
    )
    await db.commit()

    detail = (await client.get(f"/portfolios/{portfolio_id}")).json()
    assert detail["total_value"] == 5500.0
    assert detail["unrealised_pnl"] == 500.0
    assert detail["total_return_pct"] == 10.0

    trades = (await client.get(f"/portfolios/{portfolio_id}/trades")).json()
    assert len(trades) == 1

    assert (await client.delete(f"/portfolios/{portfolio_id}")).status_code == 204
    assert (await client.get(f"/portfolios/{portfolio_id}")).status_code == 404


async def test_trade_without_price_or_history_is_422(client, seeded_stocks):
    portfolio_id = (
        await client.post("/portfolios", json={"name": "P", "starting_cash": 1000.0})
    ).json()["id"]

    response = await client.post(
        f"/portfolios/{portfolio_id}/trades",
        json={"ticker": "MRNA", "side": "buy", "quantity": 1},
    )
    assert response.status_code == 422
    assert "backfill" in response.json()["detail"]


async def test_trade_beyond_cash_is_409(client, seeded_stocks):
    portfolio_id = (
        await client.post("/portfolios", json={"name": "P", "starting_cash": 100.0})
    ).json()["id"]

    response = await client.post(
        f"/portfolios/{portfolio_id}/trades",
        json={"ticker": "MRNA", "side": "buy", "quantity": 10, "price": 100.0},
    )
    assert response.status_code == 409


async def test_trade_unknown_ticker_404(client, seeded_stocks):
    portfolio_id = (
        await client.post("/portfolios", json={"name": "P", "starting_cash": 100.0})
    ).json()["id"]

    response = await client.post(
        f"/portfolios/{portfolio_id}/trades",
        json={"ticker": "NOSUCH", "side": "buy", "quantity": 1, "price": 1.0},
    )
    assert response.status_code == 404


async def test_simulate_endpoint_returns_valuation(client, db, seeded_stocks):
    from datetime import datetime, timedelta, timezone

    from app.models import StockPrice
    from app.services.ingest import RawArticle, store_articles

    start = datetime.now(timezone.utc) - timedelta(days=20)
    for offset in range(20):
        db.add(
            StockPrice(
                ticker_id=seeded_stocks[0].id,
                close=100.0 + offset,
                price_date=start + timedelta(days=offset),
                source="test",
            )
        )
    await db.commit()
    await store_articles(
        db,
        [
            RawArticle(
                ticker="MRNA",
                headline="FDA approves therapy after priority review",
                url="https://example.com/sim-api",
                source="test_feed",
                published_at=start + timedelta(days=1),
            )
        ],
    )

    portfolio_id = (
        await client.post("/portfolios", json={"name": "Sim", "starting_cash": 10000.0})
    ).json()["id"]

    response = await client.post(
        f"/portfolios/{portfolio_id}/simulate", json={"days": 60, "hold_days": 5}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["signals_seen"] == 1
    assert body["trades_executed"] == 2
    assert body["valuation"]["total_value"] > 10000.0


async def test_simulate_unknown_portfolio_404(client, seeded_stocks):
    assert (await client.post("/portfolios/999/simulate", json={})).status_code == 404
