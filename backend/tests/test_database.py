"""Engine configuration, especially the Supabase pooler accommodations."""

from __future__ import annotations

from app.config import Settings
from app.database import _engine_kwargs, _statement_cache_size


def _settings(**overrides) -> Settings:
    # _env_file=None keeps a developer's local .env from leaking into tests.
    return Settings(_env_file=None, **overrides)


def test_transaction_pooler_disables_statement_cache():
    settings = _settings(
        database_url="postgresql+asyncpg://postgres.ref:pw@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
    )
    assert _statement_cache_size(settings) == 0
    assert _engine_kwargs(settings)["connect_args"] == {"statement_cache_size": 0}


def test_session_pooler_keeps_default_cache():
    settings = _settings(
        database_url="postgresql+asyncpg://postgres.ref:pw@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
    )
    assert _statement_cache_size(settings) is None
    assert "connect_args" not in _engine_kwargs(settings)


def test_explicit_setting_wins_over_detection():
    settings = _settings(
        database_url="postgresql+asyncpg://postgres.ref:pw@aws-0-eu-central-1.pooler.supabase.com:6543/postgres",
        db_statement_cache_size=100,
    )
    assert _statement_cache_size(settings) == 100


def test_sqlite_gets_no_pool_or_connect_args():
    settings = _settings(database_url="sqlite+aiosqlite:///:memory:")
    kwargs = _engine_kwargs(settings)
    assert "pool_size" not in kwargs
    assert "connect_args" not in kwargs


def test_plain_postgres_gets_pool_tuning_only():
    settings = _settings(database_url="postgresql+asyncpg://u:p@localhost:5432/pharma")
    kwargs = _engine_kwargs(settings)
    assert kwargs["pool_size"] == 10
    assert "connect_args" not in kwargs
