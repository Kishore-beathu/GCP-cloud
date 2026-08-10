import type { Stock } from '../api/types'
import type { LivePrice } from '../hooks/useTickerSocket'
import { ChangeText } from './badges'

const REGIONS = [
  { value: '', label: 'All regions' },
  { value: 'north_america', label: 'North America' },
  { value: 'europe', label: 'Europe' },
  { value: 'asia_pacific', label: 'Asia-Pacific' },
]

interface Props {
  stocks: Stock[]
  selected: string | null
  prices: Record<string, LivePrice>
  onSelect: (ticker: string) => void
  filter: string
  onFilter: (value: string) => void
  region: string
  onRegion: (value: string) => void
}

export function Watchlist({
  stocks,
  selected,
  prices,
  onSelect,
  filter,
  onFilter,
  region,
  onRegion,
}: Props) {
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
      <select
        className="search"
        value={region}
        onChange={(event) => onRegion(event.target.value)}
        aria-label="Filter by region"
      >
        {REGIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ul>
        {visible.map((stock) => {
          // A live tick is the freshest number, but only the subscribed few
          // ever get one. Everything else falls back to the last stored close
          // rather than showing a dash forever.
          const live = prices[stock.ticker]
          const price = live?.price ?? stock.last_price
          const change = live?.change ?? stock.last_change_pct
          return (
            <li key={stock.ticker}>
              <button
                className={stock.ticker === selected ? 'row selected' : 'row'}
                onClick={() => onSelect(stock.ticker)}
              >
                <span className="ticker">{stock.ticker}</span>
                <span className="name" title={`${stock.company_name} · ${stock.exchange ?? ''}`}>
                  {stock.company_name}
                </span>
                <span className="price">
                  {price != null ? price.toFixed(2) : '—'}
                </span>
                <ChangeText value={change ?? undefined} />
              </button>
            </li>
          )
        })}
        {visible.length === 0 && <li className="muted empty">No matches</li>}
      </ul>
    </aside>
  )
}
