import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  API_URL,
  Unreachable,
  getNews,
  getSectorGroups,
  getSession,
  getStocks,
  logout,
  onSessionExpired,
} from './api/client'
import { AlertToasts } from './components/AlertToasts'
import { AlertsPanel } from './components/AlertsPanel'
import { BacktestPanel } from './components/BacktestPanel'
import { NewsFeed } from './components/NewsFeed'
import { PortfolioPanel } from './components/PortfolioPanel'
import { PriceChart } from './components/PriceChart'
import { ScorePanel } from './components/ScorePanel'
import { SignIn } from './components/SignIn'
import { SECTOR_PREFIX, Watchlist } from './components/Watchlist'
import { ChangeText } from './components/badges'
import { useAsync } from './hooks/useAsync'
import { useTickerSocket } from './hooks/useTickerSocket'

// How many tickers the single WebSocket subscribes to for the live tape.
const LIVE_TICKER_LIMIT = 30

export default function App() {
  // Gate the dashboard behind sign-in only when the deployment requires it;
  // a local backend with no password configured goes straight through.
  const [gate, setGate] = useState<
    'checking' | 'open' | 'locked' | 'expired' | 'unreachable'
  >('checking')

  const checkSession = useCallback(() => {
    setGate('checking')
    getSession()
      .then((s) => setGate(s.authenticated ? 'open' : 'locked'))
      // "Backend is down" and "you are not signed in" were both shown as the
      // sign-in form, which invites someone to type a password at a server
      // that cannot receive it.
      .catch((error) => setGate(error instanceof Unreachable ? 'unreachable' : 'locked'))
  }, [])

  useEffect(checkSession, [checkSession])
  useEffect(() => onSessionExpired(() => setGate('expired')), [])

  if (gate === 'checking') return <div className="booting">Connecting…</div>
  if (gate === 'unreachable') {
    return (
      <div className="booting">
        <p>Cannot reach the API at <code>{API_URL}</code>.</p>
        <p className="muted">
          Start the backend, then retry:
          <br />
          <code>.venv\Scripts\python -m uvicorn app.main:app --port 8000</code>
        </p>
        <button className="ghost" onClick={checkSession}>
          Retry
        </button>
      </div>
    )
  }
  if (gate === 'locked' || gate === 'expired') {
    return <SignIn expired={gate === 'expired'} onSignedIn={() => setGate('open')} />
  }
  return <Dashboard onSignOut={() => { logout(); setGate('locked') }} />
}

function Dashboard({ onSignOut }: { onSignOut: () => void }) {
  const [region, setRegion] = useState('')
  // One control, two levels: a bare value is an industry group, a `sector:`
  // prefix is one sector inside it. They go to different query parameters
  // because the API filters them differently — a group expands to its member
  // sectors, a sector matches exactly.
  const [group, setGroup] = useState('')
  const isSector = group.startsWith(SECTOR_PREFIX)
  const stocks = useAsync(
    () =>
      getStocks({
        region: region || undefined,
        group: !isSector && group ? group : undefined,
        sector: isSector ? group.slice(SECTOR_PREFIX.length) : undefined,
      }),
    [region, group, isSector],
  )
  const sectorGroups = useAsync(() => getSectorGroups(), [])

  // /scores filters by group, not by sector. Narrowing to one sector shows the
  // ranks for the group that contains it rather than the whole universe —
  // sending "sector:cro" would be rejected as an unknown group.
  const scoreGroup = useMemo(() => {
    if (!isSector) return group
    const sector = group.slice(SECTOR_PREFIX.length)
    return sectorGroups.data?.groups.find((info) => info.sectors.includes(sector))?.key ?? ''
  }, [group, isSector, sectorGroups.data])
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

  // Land on a ticker that has something to show. Defaulting to the first
  // symbol alphabetically opens on 068270.KS, whose news and prices need
  // vendor coverage a free plan does not include — so the first thing every
  // new user saw was an empty dashboard. The newest stored article names a
  // ticker that demonstrably has data; the alphabetical first is the fallback
  // for a database with no news in it yet.
  const latestArticle = useAsync(() => getNews({ limit: 1 }), [])
  const defaultTicker = latestArticle.data?.[0]?.ticker ?? allTickers[0] ?? null

  const active = selected ?? defaultTicker

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
          groups={sectorGroups.data?.groups ?? []}
          selected={active}
          prices={prices}
          onSelect={setSelected}
          filter={filter}
          onFilter={setFilter}
          region={region}
          onRegion={setRegion}
          group={group}
          onGroup={setGroup}
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
              <ScorePanel group={scoreGroup} onSelect={setSelected} selected={active} />
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
