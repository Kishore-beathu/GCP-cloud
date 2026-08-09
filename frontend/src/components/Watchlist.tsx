import type { Stock } from '../api/types'
import type { LivePrice } from '../hooks/useTickerSocket'
import { ChangeText } from './badges'

interface Props {
  stocks: Stock[]
  selected: string | null
  prices: Record<string, LivePrice>
  onSelect: (ticker: string) => void
  filter: string
  onFilter: (value: string) => void
}

export function Watchlist({ stocks, selected, prices, onSelect, filter, onFilter }: Props) {
  const query = filter.trim().toUpperCase()
  const visible = query
    ? stocks.filter(
        (stock) =>
          stock.ticker.includes(query) || stock.company_name.toUpperCase().includes(query),
      )
    : stocks

  return (
    <aside className="watchlist">
      <input
        className="search"
        placeholder="Search tickers…"
        value={filter}
        onChange={(event) => onFilter(event.target.value)}
        aria-label="Search tickers"
      />
      <ul>
        {visible.map((stock) => {
          const live = prices[stock.ticker]
          return (
            <li key={stock.ticker}>
              <button
                className={stock.ticker === selected ? 'row selected' : 'row'}
                onClick={() => onSelect(stock.ticker)}
              >
                <span className="ticker">{stock.ticker}</span>
                <span className="name" title={stock.company_name}>
                  {stock.company_name}
                </span>
                <span className="price">
                  {live?.price != null ? live.price.toFixed(2) : '—'}
                </span>
                <ChangeText value={live?.change} />
              </button>
            </li>
          )
        })}
        {visible.length === 0 && <li className="muted empty">No matches</li>}
      </ul>
    </aside>
  )
}
