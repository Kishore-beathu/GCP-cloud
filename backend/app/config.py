"""Application settings, loaded from the environment or a local .env file."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_list(value: object) -> object:
    """Turn a plain env-var string into a list.

    Accepts the comma-separated form people actually write in a `.env`
    (`CORS_ORIGINS=http://a,http://b`) and still accepts a JSON array, which is
    what pydantic-settings expects by default. Anything else is passed through
    untouched for pydantic to validate.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fall through to comma-splitting so the error pydantic raises
            # names the offending value rather than the JSON parser.
            pass
    return [item.strip() for item in text.split(",") if item.strip()]


# `NoDecode` stops pydantic-settings from JSON-decoding these fields before
# validation. Without it a comma-separated .env value raises SettingsError
# inside the settings source and the validators below never run at all.
CsvList = Annotated[list[str], NoDecode]


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

    # --- Authentication -----------------------------------------------------
    # Setting AUTH_PASSWORD switches enforcement on; there is no separate flag
    # to forget. Local development with no password left unset stays open, but
    # ENVIRONMENT=production refuses to boot without both of these set — see
    # app/security.py::require_secure_configuration.
    auth_password: str | None = None
    secret_key: str | None = None
    auth_token_ttl_seconds: int = 60 * 60 * 12
    # Failed sign-ins allowed per client per window, to blunt password guessing.
    login_max_attempts: int = 8
    login_window_seconds: int = 300

    # --- Database -----------------------------------------------------------
    # Async driver required: postgresql+asyncpg://... or sqlite+aiosqlite://...
    # Supabase: use the Session pooler URL (port 5432) and swap the scheme to
    # postgresql+asyncpg://. See backend/README.md for the full walkthrough.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pharma"
    db_echo: bool = False
    # Convenient for local development: creates any missing tables at startup.
    # It never ALTERs an existing table, so production should set this false and
    # apply schema changes with `alembic upgrade head` instead.
    create_tables_on_startup: bool = True
    # asyncpg prepared-statement cache. Leave unset for the sensible default:
    # disabled automatically on Supabase's transaction pooler (port 6543),
    # where cached prepared statements break across pooled connections.
    db_statement_cache_size: int | None = None

    # --- HTTP ---------------------------------------------------------------
    # Vite dev server (3000/5173), vite preview (4173), and a spare (3001) —
    # in both localhost and 127.0.0.1 spellings, which browsers treat as
    # different origins. Production origins come from the CORS_ORIGINS env var.
    cors_origins: CsvList = Field(
        default_factory=lambda: [
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in (3000, 3001, 4173, 5173)
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

    # Alpha Vantage quotes. Alpha Vantage is REST-only — there is no streaming
    # API — so "live" prices from it mean polling. Check your plan's actual
    # quota before lowering the interval: free tiers have been as tight as a
    # few dozen requests per *day*, which a 60-second job would exhaust in
    # minutes. On such a plan, raise the interval to hourly (3600) or leave
    # quotes to on-demand backfills.
    alpha_vantage_interval_seconds: int = 60
    alpha_vantage_batch_size: int = 5

    # --- Finnhub quotes ------------------------------------------------------
    # The primary price source. Finnhub's free tier allows ~60 calls/min, so a
    # batch of 50 every 60 seconds covers an 87-symbol universe in under two
    # minutes and can repeat all day. Alpha Vantage's free tier allows ~25
    # calls per *day*, which cannot fill the watchlist even once — keep it for
    # deliberate history backfills instead.
    finnhub_quote_enabled: bool = True
    finnhub_quote_interval_seconds: int = 60
    finnhub_quote_batch_size: int = 50

    # --- Additional news sources ---------------------------------------------
    # EDGAR's current-filings feed covers every registrant within about a
    # minute, where the per-company walk takes up to its full interval. Both
    # run: the feed is fast, the walk backfills anything it missed.
    edgar_firehose_enabled: bool = True
    edgar_firehose_interval_minutes: int = 2
    edgar_firehose_lookback_minutes: int = 30

    # FDA approvals, recalls and enforcement. No key; openFDA rate-limits
    # anonymous callers, which a 15-minute job stays well inside.
    fda_enabled: bool = True
    # Publisher feed URLs are settings, not constants. Every one of these is a
    # third party's routing decision, and when one moves the fix should be a
    # line in .env rather than a code change and a redeploy.
    # Empty by default: the documented newsroom RSS path answers 404, and a
    # default that always fails is worse than an absent one — it produces a
    # broken source in every diagnostic run and teaches you to ignore them.
    # openFDA's structured enforcement data, the more valuable half, needs no
    # feed URL and runs regardless. Set this when you have a URL you have
    # checked returns XML.
    fda_press_feed: str = ""
    fda_interval_minutes: int = 15
    fda_lookback_days: int = 3
    fda_batch_size: int = 100

    # Per-symbol headlines, and the only free news source covering the
    # European and Asia-Pacific listings.
    yahoo_news_enabled: bool = True
    yahoo_news_interval_minutes: int = 10
    yahoo_news_lookback_days: int = 3
    yahoo_news_batch_size: int = 40

    # Newswires carry a release at issue, before aggregators pick it up — and
    # carry every other issuer's releases too. The noisiest source here.
    newswire_enabled: bool = True
    # Comma-separated. Empty means "use the built-in list"; set it to keep only
    # the wires that answer for you.
    newswire_feeds: CsvList = Field(default_factory=list)
    newswire_interval_minutes: int = 5
    newswire_lookback_hours: int = 6

    # A T1 halt says an announcement is imminent, which nothing else can.
    halts_enabled: bool = True
    halts_feed: str = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
    halts_interval_minutes: int = 2
    halts_lookback_hours: int = 12

    # Trial status changes and EMA opinions: slower, but the primary record.
    clinical_enabled: bool = True
    # Also empty by default. EMA answers 404 on the documented path and its
    # site sits behind bot protection, so this needs a verified URL rather than
    # a guess. ClinicalTrials.gov, the other half of this source, is unaffected.
    ema_feed: str = ""
    clinical_interval_minutes: int = 60
    clinical_lookback_days: int = 3
    clinical_batch_size: int = 100
    # Home-regulator feeds (RNS, TDnet, HKEX) for non-US listings. Empty by
    # default: no verified public endpoint, so an operator adds one only after
    # confirming it returns what they expect.
    exchange_filing_feeds: CsvList = Field(default_factory=list)

    # --- Yahoo prices --------------------------------------------------------
    # The only configured source that prices the European and Asia-Pacific
    # listings; one call returns the current price and the daily history. The
    # endpoint is undocumented and unguaranteed — see app/integrations/yahoo.py
    # — so it is a clearly labelled fallback rather than a licensed feed.
    yahoo_prices_enabled: bool = True
    yahoo_price_interval_minutes: int = 30
    yahoo_price_range: str = "3mo"
    yahoo_price_batch_size: int = 30

    # --- Finnhub live trade stream ------------------------------------------
    # The one true real-time source: a WebSocket carrying trade ticks. Only
    # runs when FINNHUB_API_KEY is set.
    finnhub_stream_enabled: bool = True
    # Plans cap concurrent symbol subscriptions; only tickers with a live
    # viewer are subscribed, most-watched first.
    finnhub_stream_max_symbols: int = 50
    # Ticks are coalesced to at most one browser message per symbol per flush.
    finnhub_stream_flush_seconds: float = 1.0
    # How often the subscription set is reconciled against viewer demand.
    finnhub_stream_resync_seconds: float = 5.0
    finnhub_stream_backoff_seconds: float = 2.0
    finnhub_stream_max_backoff_seconds: float = 60.0
    # Used instead of the ordinary backoff after an HTTP 429, and only when the
    # vendor does not send a Retry-After of its own. Reconnecting two seconds
    # after being told to slow down is what sustains a rate limit.
    finnhub_stream_rate_limit_backoff_seconds: float = 60.0

    # --- Notification channels ----------------------------------------------
    # Each channel activates only when configured; alerts naming an
    # unconfigured channel still deliver in-app and log the gap.
    slack_webhook_url: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_from: str | None = None
    # Default recipients when an alert's condition carries no "email_to".
    email_to: CsvList = Field(default_factory=list)

    # Timeout for outbound notification calls; a slow webhook must never stall
    # the ingestion pipeline that triggered it.
    notification_timeout_seconds: float = 10.0

    @field_validator("cors_origins", "email_to", "exchange_filing_feeds", "newswire_feeds", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept a comma-separated string so these work as plain env vars."""
        return _split_list(value)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
