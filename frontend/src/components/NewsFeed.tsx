import { useState } from 'react'

import { getNews } from '../api/client'
import type { EventType, Sentiment } from '../api/types'
import { useAsync } from '../hooks/useAsync'
import { EventBadge, SentimentBadge, formatTime } from './badges'

const EVENT_OPTIONS: EventType[] = [
  'fda_approval',
  'clinical_trial',
  'revenue',
  'merger_acquisition',
  'recall',
  'partnership',
  'litigation',
  'analyst_rating',
  'exec_change',
  'facility',
  'capital_raise',
  'other',
]

interface Props {
  ticker: string | null
}

export function NewsFeed({ ticker }: Props) {
  const [sentiment, setSentiment] = useState<Sentiment | ''>('')
  const [eventType, setEventType] = useState<EventType | ''>('')

  const { data, error, loading, reload } = useAsync(
    () =>
      getNews({
        ticker: ticker ?? undefined,
        sentiment: sentiment || undefined,
        event_type: eventType || undefined,
        limit: 50,
      }),
    [ticker, sentiment, eventType],
  )

  return (
    <section className="panel news-feed">
      <header>
        <h2>{ticker ? `${ticker} news` : 'All news'}</h2>
        <div className="filters">
          <select
            value={sentiment}
            onChange={(event) => setSentiment(event.target.value as Sentiment | '')}
            aria-label="Filter by sentiment"
          >
            <option value="">All sentiment</option>
            <option value="positive">Positive</option>
            <option value="negative">Negative</option>
            <option value="neutral">Neutral</option>
          </select>
          <select
            value={eventType}
            onChange={(event) => setEventType(event.target.value as EventType | '')}
            aria-label="Filter by event type"
          >
            <option value="">All events</option>
            {EVENT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
          <button className="ghost" onClick={reload}>
            Refresh
          </button>
        </div>
      </header>

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">Failed to load news: {error}</p>}
      {data && data.length === 0 && (
        <p className="muted">
          No articles yet. Once ingestion runs (SEC or Finnhub), scored news appears here.
        </p>
      )}

      <ul className="articles">
        {data?.map((article) => (
          <li key={article.id}>
            <div className="article-meta">
              <span className="ticker">{article.ticker}</span>
              {article.sentiment && (
                <>
                  <SentimentBadge
                    sentiment={article.sentiment.sentiment}
                    score={article.sentiment.score}
                  />
                  <EventBadge event={article.sentiment.event_type} />
                </>
              )}
              <span className="muted">
                {article.source} · {formatTime(article.published_at)}
              </span>
            </div>
            <a href={article.url} target="_blank" rel="noreferrer">
              {article.headline}
            </a>
          </li>
        ))}
      </ul>
    </section>
  )
}
