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

import { getIntraday, getPrices } from '../api/client'
import type { IntradayWindow } from '../api/types'
import { useAsync } from '../hooks/useAsync'
import { ChangeText } from './badges'

/**
 * Two kinds of range, deliberately.
 *
 * The short windows are intraday bars fetched live — the stored series holds
 * one row per trading day, so it cannot draw an hour. The long ones read that
 * stored series, which is what the backtester and the portfolio value too.
 */
type Range =
  | { label: string; kind: 'intraday'; window: IntradayWindow }
  | { label: string; kind: 'daily'; days: number }

const DEFAULT_RANGE = '3M'

const RANGES: Range[] = [
  // Spelled out rather than "5M", which reads as five months beside 3M and 1M.
  { label: '5 min', kind: 'intraday', window: '5m' },
  { label: '1H', kind: 'intraday', window: '1h' },
  { label: '1D', kind: 'intraday', window: '1d' },
  { label: '1W', kind: 'intraday', window: '1w' },
  { label: '1M', kind: 'daily', days: 30 },
  { label: '3M', kind: 'daily', days: 90 },
  { label: '1Y', kind: 'daily', days: 365 },
  { label: '5Y', kind: 'daily', days: 1825 },
]

const TIME = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' })
const DAY_AND_TIME = new Intl.DateTimeFormat(undefined, {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
})

interface Props {
  ticker: string
}

export function PriceChart({ ticker }: Props) {
  const [label, setLabel] = useState(DEFAULT_RANGE)
  // Found by label, not by index: a positional fallback silently pointed at a
  // different range every time a button was added to the row.
  const range =
    RANGES.find((option) => option.label === label) ??
    RANGES.find((option) => option.label === DEFAULT_RANGE) ??
    RANGES[0]

  const { data, error, loading } = useAsync(
    () =>
      range.kind === 'intraday'
        ? getIntraday(ticker, range.window).then((result) =>
            // A 1H chart labels points by time; a 1W chart needs the day too,
            // or Monday 10:00 and Friday 10:00 read identically.
            result.points.map((point) => ({
              date: (range.window === '1w' ? DAY_AND_TIME : TIME).format(new Date(point.at)),
              close: point.close,
            })),
          )
        : getPrices(ticker, range.days).then((prices) =>
            prices.map((price) => ({
              date: price.price_date.slice(0, 10),
              close: price.close,
            })),
          ),
    [ticker, label],
  )

  const points = data ?? []

  // The move across the window on screen, not the day's change. A 3M chart
  // showing "+1.58%" would be quoting today's tick beside three months of
  // prices, which reads as the range's return and is not.
  const first = points[0]?.close
  const last = points[points.length - 1]?.close
  const change =
    first != null && last != null && first !== 0
      ? ((last - first) / first) * 100
      : null

  return (
    <section className="panel chart">
      <header>
        <h2>
          {ticker} price
          {points.length > 1 && (
            <span className="range-change">
              <ChangeText value={change} />
              <span className="muted"> over {label}</span>
            </span>
          )}
        </h2>
        <div className="filters">
          {RANGES.map((option) => (
            <button
              key={option.label}
              className={option.label === label ? 'ghost active' : 'ghost'}
              onClick={() => setLabel(option.label)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">Failed to load prices: {error}</p>}
      {!loading && !error && points.length === 0 && (
        <p className="muted">
          {range.kind === 'intraday' ? (
            <>
              No intraday bars for {ticker} in this window. Intraday comes from a live
              feed rather than stored history — a symbol it does not carry, or a market
              that has not traded today, shows nothing here. The daily ranges will still
              work.
            </>
          ) : (
            <>
              No price history yet. Load it with{' '}
              <code>POST /admin/ingest/yahoo?ticker={ticker}&amp;only_missing=false</code>.
            </>
          )}
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
