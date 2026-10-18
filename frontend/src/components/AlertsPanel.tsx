import { useState } from 'react'

import { createAlert, deleteAlert, getAlertHistory, getAlerts } from '../api/client'
import type { AlertType, EventType } from '../api/types'
import { useAsync } from '../hooks/useAsync'
import { eventLabel, formatTime } from './badges'

const ALERT_TYPES: { value: AlertType; label: string }[] = [
  { value: 'positive_news', label: 'Positive news' },
  { value: 'negative_news', label: 'Negative news' },
  { value: 'sentiment_spike', label: 'Sentiment spike' },
  { value: 'event_type', label: 'Specific event' },
]

const EVENT_OPTIONS: EventType[] = [
  'fda_approval',
  'clinical_trial',
  'revenue',
  'merger_acquisition',
  'recall',
  'partnership',
  'litigation',
  'analyst_rating',
]

interface Props {
  tickers: string[]
  defaultTicker: string | null
}

export function AlertsPanel({ tickers, defaultTicker }: Props) {
  const alerts = useAsync(getAlerts, [])
  const history = useAsync(getAlertHistory, [])

  const [ticker, setTicker] = useState('')
  const [alertType, setAlertType] = useState<AlertType>('positive_news')
  const [eventType, setEventType] = useState<EventType>('fda_approval')
  const [minScore, setMinScore] = useState('0.5')
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const effectiveTicker = ticker || defaultTicker || ''

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!effectiveTicker) {
      setSubmitError('Pick a ticker first')
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const condition: Record<string, unknown> =
        alertType === 'event_type'
          ? { event_type: eventType }
          : { min_score: Number(minScore) || 0 }
      await createAlert({
        ticker: effectiveTicker,
        alert_type: alertType,
        condition,
        channels: ['in_app'],
      })
      alerts.reload()
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  async function remove(id: number) {
    try {
      await deleteAlert(id)
      alerts.reload()
    } catch {
      // The list reload will surface any real problem.
      alerts.reload()
    }
  }

  return (
    <section className="panel alerts">
      <header>
        <h2>Alerts</h2>
      </header>

      <form onSubmit={submit} className="alert-form">
        <select
          value={effectiveTicker}
          onChange={(event) => setTicker(event.target.value)}
          aria-label="Alert ticker"
        >
          <option value="">Ticker…</option>
          {tickers.map((symbol) => (
            <option key={symbol} value={symbol}>
              {symbol}
            </option>
          ))}
        </select>
        <select
          value={alertType}
          onChange={(event) => setAlertType(event.target.value as AlertType)}
          aria-label="Alert type"
        >
          {ALERT_TYPES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {alertType === 'event_type' ? (
          <select
            value={eventType}
            onChange={(event) => setEventType(event.target.value as EventType)}
            aria-label="Event type"
          >
            {EVENT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {eventLabel(option)}
              </option>
            ))}
          </select>
        ) : (
          <input
            type="number"
            step="0.1"
            min="0"
            max="1"
            value={minScore}
            onChange={(event) => setMinScore(event.target.value)}
            aria-label="Minimum score"
            title="Minimum |score| to trigger (0–1)"
          />
        )}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Adding…' : 'Add alert'}
        </button>
      </form>
      {submitError && <p className="error">{submitError}</p>}

      <ul className="alert-list">
        {alerts.data?.map((alert) => (
          <li key={alert.id}>
            <div>
              <strong>{alert.ticker}</strong>{' '}
              <span className="muted">
                {alert.alert_type.replace(/_/g, ' ')}
                {typeof alert.condition.event_type === 'string' &&
                  ` · ${eventLabel(alert.condition.event_type as EventType)}`}
                {alert.last_triggered_at &&
                  ` · last fired ${formatTime(alert.last_triggered_at)}`}
              </span>
            </div>
            <button className="ghost danger" onClick={() => remove(alert.id)}>
              Remove
            </button>
          </li>
        ))}
        {alerts.data?.length === 0 && <li className="muted empty">No alerts yet</li>}
      </ul>

      <h3>Recent firings</h3>
      <ul className="alert-history">
        {history.data?.slice(0, 8).map((entry) => (
          <li key={entry.id}>
            <span className="muted">{formatTime(entry.triggered_at)}</span>{' '}
            {String(entry.payload.headline ?? 'Alert fired')}
          </li>
        ))}
        {history.data?.length === 0 && <li className="muted empty">Nothing yet</li>}
      </ul>
    </section>
  )
}
