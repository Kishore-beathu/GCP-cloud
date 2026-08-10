// Mirrors backend/app/schemas.py — keep the two in sync.

export type Sentiment = 'positive' | 'negative' | 'neutral'

export type EventType =
  | 'fda_approval'
  | 'revenue'
  | 'merger_acquisition'
  | 'litigation'
  | 'recall'
  | 'partnership'
  | 'clinical_trial'
  | 'exec_change'
  | 'facility'
  | 'analyst_rating'
  | 'capital_raise'
  | 'other'

export type AlertType =
  | 'positive_news'
  | 'negative_news'
  | 'sentiment_spike'
  | 'event_type'
  | 'price_change'

export interface SentimentOut {
  sentiment: Sentiment
  score: number
  confidence: number
  event_type: EventType
  event_confidence: number
  model_version: string
}

export interface NewsArticle {
  id: number
  ticker: string
  headline: string
  source: string
  url: string
  published_at: string
  sentiment: SentimentOut | null
}

export type Region = 'north_america' | 'europe' | 'asia_pacific'

/** Derived from `sector` on the server — see backend/app/services/sectors.py. */
export type SectorGroup = 'pharma_life_sciences' | 'ai' | 'data_storage' | 'other'

export interface SectorGroupInfo {
  key: SectorGroup
  label: string
  description: string
  sectors: string[]
  tracked_symbols: number
}

export interface Stock {
  id: number
  ticker: string
  company_name: string
  sector: string | null
  exchange: string | null
  mic: string | null
  region: Region | null
  country: string | null
  /** ISO 4217, except 'GBp' for London, whose prices are quoted in pence. */
  currency: string | null
  market_cap: number | null
  sector_group: SectorGroup
  /** Last stored close. Live ticks override it, but this fills the rest. */
  last_price: number | null
  last_change_pct: number | null
  last_price_date: string | null
}

export interface Price {
  close: number
  open: number | null
  high: number | null
  low: number | null
  volume: number | null
  price_date: string
  source: string
}

/** Short windows come from a live endpoint, not the stored daily series. */
export type IntradayWindow = '1h' | '1d' | '1w'

export interface IntradayPoint {
  at: string
  close: number
}

export interface Intraday {
  ticker: string
  window: IntradayWindow
  interval: string
  currency: string | null
  points: IntradayPoint[]
}

export interface ScoreFactor {
  key: string
  label: string
  value: number | null
  percentile: number | null
  weight: number
  contribution: number
  explanation: string
}

export interface StockScore {
  ticker: string
  company_name: string
  sector_group: SectorGroup
  score: number
  technical_score: number | null
  sentiment_score: number | null
  rank: number
  universe_size: number
  sector_rank: number
  sector_size: number
  coverage: number
  news_count_30d: number
  factors: ScoreFactor[]
}

export interface ScoreList {
  generated_for: number
  method: string
  scores: StockScore[]
}

export interface StockDetail extends Stock {
  latest_price: Price | null
  recent_news: NewsArticle[]
}

export interface Alert {
  id: number
  user_id: string
  ticker: string
  alert_type: AlertType
  condition: Record<string, unknown>
  channels: string[]
  is_active: boolean
  created_at: string
  last_triggered_at: string | null
}

export interface AlertHistoryEntry {
  id: number
  alert_id: number
  article_id: number | null
  triggered_at: string
  payload: Record<string, unknown>
}

export interface EventImpact {
  event_type: EventType
  sentiment: Sentiment
  count: number
  avg_impact_1d: number | null
  avg_impact_5d: number | null
  avg_impact_30d: number | null
  accuracy: number | null
}

export interface BacktestResult {
  ticker: string
  period_days: number
  articles_analysed: number
  articles_with_price_data: number
  overall_sentiment_accuracy: number | null
  analysis: EventImpact[]
}

// --- WebSocket messages -----------------------------------------------------

export interface PriceUpdate {
  type: 'price_update'
  ticker: string
  price: number | null
  change: number | null
  timestamp: string
  // 'stream' = a live Finnhub trade tick; absent = a polled stored close.
  source?: 'stream'
}

export interface AlertPush {
  type: 'alert'
  alert_id: number
  article_id: number
  headline: string
  url: string
  source: string
  sentiment: Sentiment
  score: number
  confidence: number
  event_type: EventType
}

export interface Snapshot {
  type: 'snapshot'
  ticker: string
  company_name?: string
  price: number | null
  change: number | null
  timestamp: string
  recent_news: Array<{
    id: number
    headline: string
    url: string
    published_at: string
    sentiment: Sentiment | null
    score: number | null
  }>
}

export type ServerMessage =
  | PriceUpdate
  | AlertPush
  | Snapshot
  | { type: 'subscribed'; tickers: string[] }
  | { type: 'pong'; timestamp: string }
  | { type: 'error'; detail?: string; ticker?: string }

// --- Portfolio --------------------------------------------------------------

export type TradeSide = 'buy' | 'sell'

export interface Portfolio {
  id: number
  user_id: string
  name: string
  starting_cash: number
  cash: number
  created_at: string
}

export interface PositionRow {
  ticker: string
  quantity: number
  average_cost: number
  last_price: number | null
  market_value: number
  unrealised_pnl: number
  priced: boolean
}

export interface PortfolioDetail extends Portfolio {
  positions: PositionRow[]
  cash_value: number
  positions_value: number
  total_value: number
  realised_pnl: number
  unrealised_pnl: number
  total_return_pct: number | null
}

export interface Trade {
  id: number
  ticker: string
  side: TradeSide
  quantity: number
  price: number
  executed_at: string
  rationale: string | null
}

export interface SimulationResponse {
  portfolio_id: number
  trades_executed: number
  signals_seen: number
  signals_skipped: number
  skip_reasons: Record<string, number>
  valuation: PortfolioDetail | null
}
