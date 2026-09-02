import { useMemo } from 'react'

import type { SectorGroup, SectorGroupInfo, Stock } from '../api/types'
import type { LivePrice } from '../hooks/useTickerSocket'
import { ChangeText } from './badges'

const REGIONS = [
  { value: '', label: 'All regions' },
  { value: 'north_america', label: 'North America' },
  { value: 'europe', label: 'Europe' },
  { value: 'asia_pacific', label: 'Asia-Pacific' },
]

// Falls back to these if /stocks/sectors has not answered yet, so the headings
// never flash as raw keys.
const FALLBACK_LABELS: Record<SectorGroup, string> = {
  pharma_life_sciences: 'Pharma & Life Sciences',
  ai: 'Artificial Intelligence',
  data_storage: 'Data Storage & Infrastructure',
  other: 'Other',
}

const GROUP_ORDER: SectorGroup[] = ['pharma_life_sciences', 'ai', 'data_storage', 'other']

// Marks a dropdown value as a sector rather than a group. One <select> keeps
// the two levels in one control; the prefix is what tells them apart, since a
// sector key and a group key are both bare strings.
export const SECTOR_PREFIX = 'sector:'

// The API returns sector keys, not display names. Anything missing here falls
// back to the raw key rather than being hidden, so a sector added to the
// backend shows up immediately — ugly, but present.
const SECTOR_LABELS: Record<string, string> = {
  pharma: 'Pharma',
  biotech: 'Biotech',
  clinical_stage: 'Clinical-stage (pre-revenue)',
  cdmo: 'Contract manufacturing (CDMO)',
  cro: 'Contract research (CRO)',
  life_science_tools: 'Life science tools',
  medtech: 'Medical devices',
  consumer_health: 'Consumer health',
  health_it: 'Health IT',
  ai_tech: 'AI platforms',
  ai_semiconductor: 'AI semiconductors',
  semiconductor: 'Semiconductors (broad)',
  ai_equipment: 'Semiconductor equipment',
  ai_networking: 'AI networking',
  ai_software: 'AI software',
  ai_health: 'AI drug discovery',
  storage_hardware: 'Storage hardware',
  server_hardware: 'Servers & ODMs',
  memory: 'Memory',
  cloud_storage: 'Cloud storage',
  data_platform: 'Data platforms',
  data_center: 'Data centres',
  datacenter_power: 'Data centre power & cooling',
}

interface Props {
  stocks: Stock[]
  groups: SectorGroupInfo[]
  selected: string | null
  prices: Record<string, LivePrice>
  onSelect: (ticker: string) => void
  filter: string
  onFilter: (value: string) => void
  region: string
  onRegion: (value: string) => void
  group: string
  onGroup: (value: string) => void
}

export function Watchlist({
  stocks,
  groups,
  selected,
  prices,
  onSelect,
  filter,
  onFilter,
  region,
  onRegion,
  group,
  onGroup,
}: Props) {
  const query = filter.trim().toUpperCase()
  const visible = query
    ? stocks.filter(
        (stock) =>
          stock.ticker.includes(query) || stock.company_name.toUpperCase().includes(query),
      )
    : stocks

  const labels = useMemo(() => {
    const map = { ...FALLBACK_LABELS }
    for (const info of groups) map[info.key] = info.label
    return map
  }, [groups])

  // Group in a fixed order rather than by first appearance, so the headings
  // don't reshuffle as filters change.
  const sections = useMemo(() => {
    const byGroup = new Map<SectorGroup, Stock[]>()
    for (const stock of visible) {
      const key = stock.sector_group ?? 'other'
      const bucket = byGroup.get(key)
      if (bucket) bucket.push(stock)
      else byGroup.set(key, [stock])
    }
    return GROUP_ORDER.filter((key) => byGroup.has(key)).map((key) => ({
      key,
      label: labels[key],
      stocks: byGroup.get(key) ?? [],
    }))
  }, [visible, labels])

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
        value={group}
        onChange={(event) => onGroup(event.target.value)}
        aria-label="Filter by industry"
      >
        <option value="">All industries</option>
        {/* Groups are the headings; the sectors inside them are what someone
            actually asks for — "the CROs", "the memory names". Without these
            a cohort could only be found by knowing every ticker in it and
            reading past the ninety-odd others it is sorted among. */}
        {(groups.length ? groups : []).map((info) => (
          <optgroup key={info.key} label={info.label}>
            <option value={info.key}>
              All {info.label} ({info.tracked_symbols})
            </option>
            {info.sectors.map((sector) => (
              <option key={sector} value={`${SECTOR_PREFIX}${sector}`}>
                {SECTOR_LABELS[sector] ?? sector}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
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
        {sections.map((section) => (
          <li key={section.key} className="group">
            <h3 className="group-heading">
              {section.label}
              <span className="muted"> · {section.stocks.length}</span>
            </h3>
            <ul>
              {section.stocks.map((stock) => {
                // A live tick is the freshest number, but only the subscribed
                // few ever get one. Everything else falls back to the last
                // stored close rather than showing a dash forever.
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
                      <span
                        className="name"
                        title={`${stock.company_name} · ${stock.sector ?? ''} · ${
                          stock.exchange ?? ''
                        }`}
                      >
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
            </ul>
          </li>
        ))}
        {visible.length === 0 && <li className="muted empty">No matches</li>}
      </ul>
    </aside>
  )
}
