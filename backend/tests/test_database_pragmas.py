"""SQLite concurrency settings, checked through the real engine builder.

Eight ingest jobs now write while the dashboard reads. SQLite's default
journal mode has a writer hold an EXCLUSIVE lock for its whole transaction,
blocking every reader behind it — so the app becomes intermittently
unreachable rather than merely slow, which is much harder to recognise.

These build an engine the way the application does, rather than asserting
against the test fixture's own engine, which would prove nothing about
production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.config import Settings
from app.database import _build_engine


def sqlite_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path/'pragma.db'}"
    )


@pytest.mark.asyncio
async def test_sqlite_connections_use_wal(tmp_path):
    """WAL lets readers continue against the last commit during a write."""
    engine = _build_engine(sqlite_settings(tmp_path))
    try:
        async with engine.connect() as conn:
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
        assert mode.lower() == "wal"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_waits_rather_than_failing_on_a_locked_database(tmp_path):
    """WAL does not remove writer-versus-writer contention; the timeout covers it."""
    engine = _build_engine(sqlite_settings(tmp_path))
    try:
        async with engine.connect() as conn:
            timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
        assert timeout >= 15000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_urls_are_left_alone(tmp_path):
    """The pragmas are SQLite-only; attaching them elsewhere would error."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
    )
    engine = _build_engine(settings)
    try:
        # Building the engine must not raise, and connecting is not attempted.
        assert engine.dialect.name == "postgresql"
    finally:
        await engine.dispose()
