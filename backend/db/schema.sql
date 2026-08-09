-- PostgreSQL schema for the pharma trading intelligence platform.
--
-- The application creates these tables itself from the SQLAlchemy models when
-- CREATE_TABLES_ON_STARTUP=true. This file is the hand-provisioning path (e.g.
-- the Supabase SQL editor, or a managed Cloud SQL instance where the app role
-- has no DDL rights). Keep it in sync with app/models.py.

BEGIN;

-- ---------------------------------------------------------------- stocks ----
CREATE TABLE IF NOT EXISTS stocks (
    id            SERIAL PRIMARY KEY,
    ticker        VARCHAR(16)  NOT NULL UNIQUE,
    company_name  VARCHAR(255) NOT NULL,
    sector        VARCHAR(64),
    exchange      VARCHAR(32),
    cik           VARCHAR(16),
    market_cap    DOUBLE PRECISION,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_stocks_sector ON stocks (sector);
CREATE INDEX IF NOT EXISTS ix_stocks_cik    ON stocks (cik);

-- --------------------------------------------------------- news_articles ----
CREATE TABLE IF NOT EXISTS news_articles (
    id            SERIAL PRIMARY KEY,
    ticker_id     INTEGER      NOT NULL REFERENCES stocks (id) ON DELETE CASCADE,
    headline      TEXT         NOT NULL,
    body          TEXT,
    source        VARCHAR(64)  NOT NULL,
    url           VARCHAR(1024) NOT NULL,
    published_at  TIMESTAMPTZ  NOT NULL,
    ingested_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Deduplication: re-ingesting one source's URL is a no-op, but the same
    -- story syndicated by two sources is legitimately two rows.
    CONSTRAINT uq_news_articles_url_source UNIQUE (url, source)
);

CREATE INDEX IF NOT EXISTS ix_news_articles_ticker_published
    ON news_articles (ticker_id, published_at DESC);
CREATE INDEX IF NOT EXISTS ix_news_articles_published ON news_articles (published_at DESC);
CREATE INDEX IF NOT EXISTS ix_news_articles_source    ON news_articles (source);

-- ------------------------------------------------------ sentiment_scores ----
CREATE TABLE IF NOT EXISTS sentiment_scores (
    id                SERIAL PRIMARY KEY,
    article_id        INTEGER     NOT NULL REFERENCES news_articles (id) ON DELETE CASCADE,
    sentiment         VARCHAR(16) NOT NULL,
    score             DOUBLE PRECISION NOT NULL,
    confidence        DOUBLE PRECISION NOT NULL,
    event_type        VARCHAR(32) NOT NULL DEFAULT 'other',
    event_confidence  DOUBLE PRECISION NOT NULL DEFAULT 0,
    model_version     VARCHAR(64) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_sentiment_scores_article UNIQUE (article_id),
    CONSTRAINT ck_sentiment_scores_sentiment
        CHECK (sentiment IN ('positive', 'negative', 'neutral')),
    CONSTRAINT ck_sentiment_scores_score CHECK (score BETWEEN -1 AND 1)
);

CREATE INDEX IF NOT EXISTS ix_sentiment_scores_sentiment  ON sentiment_scores (sentiment);
CREATE INDEX IF NOT EXISTS ix_sentiment_scores_event_type ON sentiment_scores (event_type);

-- ----------------------------------------------------------- stock_prices ---
CREATE TABLE IF NOT EXISTS stock_prices (
    id          SERIAL PRIMARY KEY,
    ticker_id   INTEGER     NOT NULL REFERENCES stocks (id) ON DELETE CASCADE,
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION NOT NULL,
    volume      BIGINT,
    price_date  TIMESTAMPTZ NOT NULL,
    source      VARCHAR(32) NOT NULL DEFAULT 'unknown',
    CONSTRAINT uq_stock_prices_ticker_date UNIQUE (ticker_id, price_date)
);

CREATE INDEX IF NOT EXISTS ix_stock_prices_ticker_date
    ON stock_prices (ticker_id, price_date DESC);

-- ------------------------------------------------------------ user_alerts ---
CREATE TABLE IF NOT EXISTS user_alerts (
    id                 SERIAL PRIMARY KEY,
    user_id            VARCHAR(64) NOT NULL DEFAULT 'local',
    ticker_id          INTEGER     NOT NULL REFERENCES stocks (id) ON DELETE CASCADE,
    alert_type         VARCHAR(32) NOT NULL,
    condition          JSONB       NOT NULL DEFAULT '{}'::JSONB,
    channels           JSONB       NOT NULL DEFAULT '["in_app"]'::JSONB,
    is_active          BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_triggered_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_user_alerts_user   ON user_alerts (user_id);
CREATE INDEX IF NOT EXISTS ix_user_alerts_ticker ON user_alerts (ticker_id);
CREATE INDEX IF NOT EXISTS ix_user_alerts_active ON user_alerts (is_active);

-- ---------------------------------------------------------- alert_history ---
CREATE TABLE IF NOT EXISTS alert_history (
    id            SERIAL PRIMARY KEY,
    alert_id      INTEGER     NOT NULL REFERENCES user_alerts (id) ON DELETE CASCADE,
    article_id    INTEGER              REFERENCES news_articles (id) ON DELETE SET NULL,
    triggered_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload       JSONB       NOT NULL DEFAULT '{}'::JSONB,
    -- One article fires a given alert at most once, however often it is re-scored.
    CONSTRAINT uq_alert_history_alert_article UNIQUE (alert_id, article_id)
);

CREATE INDEX IF NOT EXISTS ix_alert_history_triggered ON alert_history (triggered_at DESC);

-- ------------------------------------------------------ row level security --
-- Supabase (and similar platforms) expose the public schema through an
-- auto-generated REST API keyed by a publishable token. Enabling RLS with no
-- policies blocks that path. The backend is unaffected: it connects as the
-- table owner over a direct Postgres connection, and owners bypass RLS.
ALTER TABLE stocks           ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_articles    ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentiment_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_prices     ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_alerts      ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_history    ENABLE ROW LEVEL SECURITY;

COMMIT;
