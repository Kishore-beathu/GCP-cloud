import { useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { getPrices } from '../api/client'
import { useAsync } from '../hooks/useAsync'

const RANGES = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '1Y', days: 365 },
  { label: '5Y', days: 1825 },
]

interface Props {
  ticker: string
}

export function PriceChart({ ticker }: Props) {
  const [days, setDays] = useState(90)
  const { data, error, loading } = useAsync(() => getPrices(ticker, days), [ticker, days])

  const points =
    data?.map((price) => ({
      date: price.price_date.slice(0, 10),
      close: price.close,
    })) ?? []

  return (
    <section className="panel chart">
      <header>
        <h2>{ticker} price</h2>
        <div className="filters">
          {RANGES.map((range) => (
            <button
              key={range.label}
              className={range.days === days ? 'ghost active' : 'ghost'}
              onClick={() => setDays(range.days)}
            >
              {range.label}
            </button>
          ))}
        </div>
      </header>

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">Failed to load prices: {error}</p>}
      {!loading && !error && points.length === 0 && (
        <p className="muted">
          No price history yet. Backfill it with{' '}
          <code>POST /admin/backfill/prices?ticker={ticker}&amp;outputsize=full</code>.
        </p>
      )}

      {points.length > 0 && (
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="close-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: 'var(--text-dim)', fontSize: 11 }} minTickGap={48} />
            <YAxis
              tick={{ fill: 'var(--text-dim)', fontSize: 11 }}
              domain={['auto', 'auto']}
              width={56}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--panel)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                color: 'var(--text)',
              }}
            />
            <Area
              type="monotone"
              dataKey="close"
              stroke="var(--accent)"
              strokeWidth={2}
              fill="url(#close-fill)"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </section>
  )
}
