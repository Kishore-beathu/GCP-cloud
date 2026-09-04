import { getBacktest } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { ChangeText, SentimentBadge, eventLabel } from './badges'

interface Props {
  ticker: string
}

export function BacktestPanel({ ticker }: Props) {
  const { data, error, loading } = useAsync(() => getBacktest(ticker, 180), [ticker])

  return (
    <section className="panel backtest">
      <header>
        <h2>{ticker} backtest · 180d</h2>
        {data?.overall_sentiment_accuracy != null && (
          <span className="badge badge-event">
            signal accuracy {data.overall_sentiment_accuracy.toFixed(0)}%
          </span>
        )}
      </header>

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">Backtest failed: {error}</p>}
      {data && data.articles_with_price_data === 0 && (
        <p className="muted">
          Not enough data: {data.articles_analysed} scored article(s), none with price history.
          Backfill prices to unlock this view.
        </p>
      )}

      {data && data.analysis.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Event</th>
                <th>Sentiment</th>
                <th>n</th>
                <th>1d</th>
                <th>5d</th>
                <th>30d</th>
                <th>Acc.</th>
              </tr>
            </thead>
            <tbody>
              {data.analysis.map((row) => (
                <tr key={`${row.event_type}-${row.sentiment}`}>
                  <td>{eventLabel(row.event_type)}</td>
                  <td>
                    <SentimentBadge sentiment={row.sentiment} />
                  </td>
                  <td>{row.count}</td>
                  <td>
                    <ChangeText value={row.avg_impact_1d} />
                  </td>
                  <td>
                    <ChangeText value={row.avg_impact_5d} />
                  </td>
                  <td>
                    <ChangeText value={row.avg_impact_30d} />
                  </td>
                  <td>{row.accuracy != null ? `${row.accuracy.toFixed(0)}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
