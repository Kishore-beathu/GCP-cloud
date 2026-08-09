import { useCallback, useEffect, useMemo, useState } from 'react'

import { getSession, getStocks, logout, onSessionExpired } from './api/client'
import { AlertToasts } from './components/AlertToasts'
import { AlertsPanel } from './components/AlertsPanel'
import { BacktestPanel } from './components/BacktestPanel'
import { NewsFeed } from './components/NewsFeed'
import { PortfolioPanel } from './components/PortfolioPanel'
import { PriceChart } from './components/PriceChart'
import { SignIn } from './components/SignIn'
import { Watchlist } from './components/Watchlist'
import { ChangeText } from './components/badges'
import { useAsync } from './hooks/useAsync'
import { useTickerSocket } from './hooks/useTickerSocket'

// How many tickers the single WebSocket subscribes to for the live tape.
const LIVE_TICKER_LIMIT = 30

export default function App() {
  // Gate the dashboard behind sign-in only when the deployment requires it;
  // a local backend with no password configured goes straight through.
  const [gate, setGate] = useState<'checking' | 'open' | 'locked' | 'expired'>('checking')

  const checkSession = useCallback(() => {
    getSession()
      .then((s) => setGate(s.authenticated ? 'open' : 'locked'))
      .catch(() => setGate('locked'))
  }, [])

  useEffect(checkSession, [checkSession])
  useEffect(() => onSessionExpired(() => setGate('expired')), [])

  if (gate === 'checking') return <div className="booting">Connecting…</div>
  if (gate === 'locked' || gate === 'expired') {
    return <SignIn expired={gate === 'expired'} onSignedIn={() => setGate('open')} />
  }
  return <Dashboard onSignOut={() => { logout(); setGate('locked') }} />
}

function Dashboard({ onSignOut }: { onSignOut: () => void }) {
  const [region, setRegion] = useState('')
  const stocks = useAsync(() => getStocks({ region: region || undefined }), [region])
  const [selected, setSelected] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  const allTickers = useMemo(
    () => stocks.data?.map((stock) => stock.ticker) ?? [],
    [stocks.data],
  )

  // Selected ticker always leads the subscription list so its snapshot arrives.
  const liveTickers = useMemo(() => {
    const base = allTickers.slice(0, LIVE_TICKER_LIMIT)
    if (selected && !base.includes(selected)) base.unshift(selected)
    else if (selected) {
      base.splice(base.indexOf(selected), 1)
      base.unshift(selected)
    }
    return base
  }, [allTickers, selected])

  const { prices, alerts, connected, dismissAlert } = useTickerSocket(liveTickers)

  const active = selected ?? allTickers[0] ?? null

  return (
    <div className="app">
      <header className="topbar">
        <h1>Trading Intelligence</h1>
        <div className="tape">
          {liveTickers.slice(0, 8).map((ticker) => (
            <button key={ticker} className="tape-item" onClick={() => setSelected(ticker)}>
              <span className="ticker">{ticker}</span>
              <span>{prices[ticker]?.price?.toFixed(2) ?? '—'}</span>
              <ChangeText value={prices[ticker]?.change} />
            </button>
          ))}
        </div>
        <span
          className={connected ? 'status status-ok' : 'status status-bad'}
          title={connected ? 'Live connection active' : 'Reconnecting…'}
        >
          {connected ? 'LIVE' : 'OFFLINE'}
        </span>
        <button className="ghost" onClick={onSignOut} title="Sign out">
          Sign out
        </button>
      </header>

      <div className="layout">
        <Watchlist
          stocks={stocks.data ?? []}
          selected={active}
          prices={prices}
          onSelect={setSelected}
          filter={filter}
          onFilter={setFilter}
          region={region}
          onRegion={setRegion}
        />

        <main>
          {stocks.error && (
            <section className="panel">
              <p className="error">
                Cannot reach the API at the configured URL: {stocks.error}. Is the backend
                running?
              </p>
            </section>
          )}
          {active && <PriceChart ticker={active} />}
          <div className="columns">
            <NewsFeed ticker={active} />
            <div className="stack">
              <AlertsPanel tickers={allTickers} defaultTicker={active} />
              <PortfolioPanel tickers={allTickers} defaultTicker={active} />
              {active && <BacktestPanel ticker={active} />}
            </div>
          </div>
        </main>
      </div>

      <AlertToasts alerts={alerts} onDismiss={dismissAlert} />
    </div>
  )
}
