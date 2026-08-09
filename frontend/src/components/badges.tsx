// Small shared presentational helpers.

import type { EventType, Sentiment } from '../api/types'

const EVENT_LABELS: Record<EventType, string> = {
  fda_approval: 'FDA / Regulatory',
  revenue: 'Earnings',
  merger_acquisition: 'M&A',
  litigation: 'Litigation',
  recall: 'Recall',
  partnership: 'Partnership',
  clinical_trial: 'Clinical Trial',
  exec_change: 'Exec Change',
  facility: 'Facility',
  analyst_rating: 'Analyst',
  capital_raise: 'Capital Raise',
  other: 'Other',
}

export function eventLabel(event: EventType): string {
  return EVENT_LABELS[event] ?? event
}

export function SentimentBadge({ sentiment, score }: { sentiment: Sentiment; score?: number }) {
  const symbol = sentiment === 'positive' ? '▲' : sentiment === 'negative' ? '▼' : '■'
  return (
    <span className={`badge badge-${sentiment}`}>
      {symbol} {sentiment}
      {score !== undefined ? ` ${score.toFixed(2)}` : ''}
    </span>
  )
}

export function EventBadge({ event }: { event: EventType }) {
  return <span className="badge badge-event">{eventLabel(event)}</span>
}

export function ChangeText({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="muted">—</span>
  const cls = value > 0 ? 'up' : value < 0 ? 'down' : 'muted'
  return (
    <span className={cls}>
      {value > 0 ? '+' : ''}
      {value.toFixed(2)}%
    </span>
  )
}

export function formatTime(iso: string): string {
  const date = new Date(iso)
  const now = Date.now()
  const diffMinutes = Math.round((now - date.getTime()) / 60_000)
  if (diffMinutes < 1) return 'just now'
  if (diffMinutes < 60) return `${diffMinutes}m ago`
  if (diffMinutes < 60 * 24) return `${Math.round(diffMinutes / 60)}h ago`
  return date.toLocaleDateString()
}
