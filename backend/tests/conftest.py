"""Shared test fixtures: an in-memory SQLite database and an HTTP client."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

# Configure the environment before importing anything that reads settings.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("CREATE_TABLES_ON_STARTUP", "false")
os.environ.setdefault("SENTIMENT_BACKEND", "lexicon")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Stock  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def engine():
    """A fresh in-memory database per test.

    StaticPool keeps every connection pointed at the same in-memory database;
    without it each checkout would get its own empty one.
    """
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def seeded_stocks(db) -> list[Stock]:
    """Two tracked stocks, enough for feed, alert, and backtest tests."""
    stocks = [
        Stock(ticker="MRNA", company_name="Moderna Inc.", sector="biotech", cik="0001682852"),
        Stock(ticker="PFE", company_name="Pfizer Inc.", sector="pharma", cik="0000078003"),
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)
    return stocks


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired to the test database, with lifespan startup skipped."""
    app = create_app()

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
