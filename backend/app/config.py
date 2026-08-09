"""Application settings, loaded from the environment or a local .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the trading intelligence backend."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Core ---------------------------------------------------------------
    app_name: str = "Pharma Trading Intelligence API"
    environment: str = "development"
    log_level: str = "INFO"
    log_file: str | None = None

    # --- Database -----------------------------------------------------------
    # Async driver required: postgresql+asyncpg://... or sqlite+aiosqlite://...
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pharma"
    db_echo: bool = False
    create_tables_on_startup: bool = True

    # --- HTTP ---------------------------------------------------------------
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:5173",
        ]
    )

    # --- Sentiment ----------------------------------------------------------
    # "lexicon" needs no downloads; "finbert" requires requirements-ml.txt.
    sentiment_backend: str = "lexicon"
    finbert_model: str = "ProsusAI/finbert"

    # --- Integrations -------------------------------------------------------
    # The SEC requires a descriptive User-Agent with a contact address on every
    # request; anonymous traffic gets blocked.
    sec_user_agent: str = "GxP Consulting trading-agent (contact@example.com)"
    finnhub_api_key: str | None = None
    alpha_vantage_api_key: str | None = None

    # --- Scheduler ----------------------------------------------------------
    scheduler_enabled: bool = True
    sec_ingest_interval_minutes: int = 30
    sec_ingest_batch_size: int = 25
    ticker_push_interval_seconds: int = 10
    data_retention_days: int = 365

    # Finnhub company news. The free tier allows ~60 calls/min; one call covers
    # one ticker, so a batch of 50 per 5-minute run stays comfortably inside it.
    finnhub_news_interval_minutes: int = 5
    finnhub_batch_size: int = 50
    finnhub_lookback_days: int = 3

    # Alpha Vantage quotes. The free tier is 5 calls/min, so the default batch
    # matches it exactly; paid tiers can raise the batch size and cadence.
    alpha_vantage_interval_seconds: int = 60
    alpha_vantage_batch_size: int = 5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string so CORS_ORIGINS works as a plain env var."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
