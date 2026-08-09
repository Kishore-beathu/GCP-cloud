"""Async SQLAlchemy engine, session factory, and FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _statement_cache_size(settings: Settings) -> int | None:
    """Resolve the asyncpg prepared-statement cache size for this URL.

    Explicit config wins. Otherwise the cache is disabled on Supabase's
    transaction pooler (port 6543), where connections are shared per
    transaction and server-side prepared statements leak across clients with
    "prepared statement ... already exists" errors. The session pooler (5432)
    and direct connections keep asyncpg's default cache.
    """
    if settings.db_statement_cache_size is not None:
        return settings.db_statement_cache_size
    if "pooler.supabase.com:6543" in settings.database_url:
        return 0
    return None


def _engine_kwargs(settings: Settings) -> dict[str, object]:
    kwargs: dict[str, object] = {"echo": settings.db_echo, "future": True}
    if not settings.is_sqlite:
        # Pool tuning is meaningless for SQLite's single-file driver.
        kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
        cache_size = _statement_cache_size(settings)
        if cache_size is not None:
            kwargs["connect_args"] = {"statement_cache_size": cache_size}
    return kwargs


def _build_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, **_engine_kwargs(settings))


def get_engine() -> AsyncEngine:
    """Return the lazily created global engine."""
    global _engine
    if _engine is None:
        _engine = _build_engine(get_settings())
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the lazily created global session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that rolls back on error."""
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Create any missing tables. Real migrations belong in Alembic later."""
    from app import models  # noqa: F401  (import registers the mappers)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Close pooled connections and reset the globals (used on shutdown/tests)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
