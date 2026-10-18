import { useCallback, useState } from 'react'

import {
  createPortfolio,
  createTrade,
  getPortfolio,
  getPortfolios,
  runSimulation,
} from '../api/client'
import type { TradeSide } from '../api/types'
import { useAsync } from '../hooks/useAsync'
import { ChangeText, MoneyDelta } from './badges'

interface Props {
  tickers: string[]
  defaultTicker: string | null
}

export function PortfolioPanel({ tickers, defaultTicker }: Props) {
  const portfolios = useAsync(getPortfolios, [])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Fall back to the first portfolio so the panel is useful without a click.
  const activeId = selectedId ?? portfolios.data?.[0]?.id ?? null

  const detail = useAsync(
    () => (activeId === null ? Promise.resolve(null) : getPortfolio(activeId)),
    [activeId, busy],
  )

  const [ticker, setTicker] = useState('')
  const [side, setSide] = useState<TradeSide>('buy')
  const [quantity, setQuantity] = useState('10')

  const run = useCallback(
    async (action: () => Promise<unknown>, message?: string) => {
      setBusy(true)
      setError(null)
      setNotice(null)
      try {
        await action()
        if (message) setNotice(message)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  async function addPortfolio() {
    await run(async () => {
      const created = await createPortfolio({ name: 'Paper portfolio', starting_cash: 100_000 })
      setSelectedId(created.id)
      portfolios.reload()
    })
  }

  async function submitTrade(event: React.FormEvent) {
    event.preventDefault()
    if (activeId === null) return
    const symbol = ticker || defaultTicker
    if (!symbol) {
      setError('Pick a ticker first')
      return
    }
    await run(
      () => createTrade(activeId, { ticker: symbol, side, quantity: Number(quantity) }),
      `${side === 'buy' ? 'Bought' : 'Sold'} ${quantity} ${symbol}`,
    )
  }

  async function simulate() {
    if (activeId === null) return
    await run(async () => {
      const result = await runSimulation(activeId, { days: 180, hold_days: 5 })
      setNotice(
        `Simulation: ${result.trades_executed} trades from ${result.signals_seen} signals` +
          (result.signals_skipped ? ` (${result.signals_skipped} skipped)` : ''),
      )
    })
  }

  const value = detail.data

  return (
    <section className="panel portfolio">
      <header>
        <h2>Portfolio</h2>
        <div className="filters">
          {portfolios.data && portfolios.data.length > 0 && (
            <select
              value={activeId ?? ''}
              onChange={(event) => setSelectedId(Number(event.target.value))}
              aria-label="Select portfolio"
            >
              {portfolios.data.map((portfolio) => (
                <option key={portfolio.id} value={portfolio.id}>
                  {portfolio.name}
                </option>
              ))}
            </select>
          )}
          <button className="ghost" onClick={addPortfolio} disabled={busy}>
            New
          </button>
        </div>
      </header>

      {portfolios.data?.length === 0 && (
        <p className="muted">
          No portfolio yet. Create one to paper-trade against live prices, or replay the
          sentiment strategy over stored history.
        </p>
      )}

      {value && (
        <>
          <div className="totals">
            <div>
              <span className="muted">Total</span>
              <strong>
                {value.total_value.toLocaleString(undefined, {
                  style: 'currency',
                  currency: 'USD',
                  maximumFractionDigits: 0,
                })}
              </strong>
            </div>
            <div>
              <span className="muted">Return</span>
              <ChangeText value={value.total_return_pct} />
            </div>
            <div>
              <span className="muted">Cash</span>
              <strong>
                {value.cash.toLocaleString(undefined, {
                  style: 'currency',
                  currency: 'USD',
                  maximumFractionDigits: 0,
                })}
              </strong>
            </div>
            <div>
              <span className="muted">Realised</span>
              <MoneyDelta value={value.realised_pnl} />
            </div>
          </div>

          <form onSubmit={submitTrade} className="alert-form">
            <select
              value={ticker || defaultTicker || ''}
              onChange={(event) => setTicker(event.target.value)}
              aria-label="Trade ticker"
            >
              <option value="">Ticker…</option>
              {tickers.map((symbol) => (
                <option key={symbol} value={symbol}>
                  {symbol}
                </option>
              ))}
            </select>
            <select
              value={side}
              onChange={(event) => setSide(event.target.value as TradeSide)}
              aria-label="Side"
            >
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
            <input
              type="number"
              min="0"
              step="1"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              aria-label="Quantity"
            />
            <button type="submit" disabled={busy}>
              Trade
            </button>
            <button type="button" className="ghost" onClick={simulate} disabled={busy}>
              Simulate
            </button>
          </form>

          {error && <p className="error">{error}</p>}
          {notice && <p className="muted">{notice}</p>}

          {value.positions.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Qty</th>
                    <th>Avg cost</th>
                    <th>Last</th>
                    <th>Value</th>
                    <th>P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {value.positions.map((position) => (
                    <tr key={position.ticker}>
                      <td className="ticker">{position.ticker}</td>
                      <td>{position.quantity.toFixed(2)}</td>
                      <td>{position.average_cost.toFixed(2)}</td>
                      <td>
                        {position.last_price != null ? (
                          position.last_price.toFixed(2)
                        ) : (
                          <span className="muted" title="No price history; valued at cost">
                            at cost
                          </span>
                        )}
                      </td>
                      <td>{position.market_value.toFixed(0)}</td>
                      <td>
                        <ChangeText
                          value={
                            position.average_cost > 0 && position.priced
                              ? (position.unrealised_pnl /
                                  (position.average_cost * position.quantity)) *
                                100
                              : null
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">No open positions.</p>
          )}
        </>
      )}
    </section>
  )
}
