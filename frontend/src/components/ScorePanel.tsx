import { useMemo } from 'react'

import { getScores } from '../api/client'
import type { StockScore } from '../api/types'
import { useAsync } from '../hooks/useAsync'

interface Props {
  group: string
  onSelect: (ticker: string) => void
  selected: string | null
}

/** 0-100 maps onto the same up/down colours prices use, via a neutral middle. */
function scoreClass(score: number): string {
  if (score >= 65) return 'up'
  if (score <= 35) return 'down'
  return 'muted'
}

function Bar({ value }: { value: number }) {
  return (
    <span className="score-bar" aria-hidden="true">
      <span className="score-bar-fill" style={{ width: `${Math.max(2, value)}%` }} />
    </span>
  )
}

function Factors({ score }: { score: StockScore }) {
  // The three that moved the score most. Showing all nine turns an
  // explanation into a wall, and the tail rarely changes the conclusion.
  const top = score.factors.slice(0, 3)
  if (!top.length) return null

  return (
    <ul className="score-factors">
      {top.map((factor) => (
        <li key={factor.key}>
          <span className="muted">{factor.label}</span>
          <span className={scoreClass(factor.percentile ?? 50)}>
            {factor.percentile != null ? `${Math.round(factor.percentile)}th` : '—'}
          </span>
        </li>
      ))}
    </ul>
  )
}

// A score built from every intended input. Below this the weights were
// renormalised over whatever was available, which is not wrong but is worth
// knowing before reading a rank off the top of the list.
const FULL_COVERAGE = 1

function rowTitle(item: StockScore, duplicated: boolean): string {
  const parts = [item.company_name, `rank ${item.rank} of ${item.universe_size}`]
  if (item.coverage < FULL_COVERAGE) {
    parts.push(`scored on ${Math.round(item.coverage * 100)}% of the inputs`)
  }
  if (duplicated) parts.push('same company as another row')
  return parts.join(' · ')
}

export function ScorePanel({ group, onSelect, selected }: Props) {
  const { data, error, loading } = useAsync(
    () => getScores({ group: group || undefined, limit: 10 }),
    [group],
  )

  // A dual-listed company holds two slots in the ranking, and a top ten with
  // an A-share and its H-share in it is more concentrated than it looks.
  // Detected by company name because that is what the seed list keys them on.
  const duplicates = useMemo(() => {
    const seen = new Map<string, number>()
    for (const item of data?.scores ?? []) {
      seen.set(item.company_name, (seen.get(item.company_name) ?? 0) + 1)
    }
    return new Set([...seen].filter(([, count]) => count > 1).map(([name]) => name))
  }, [data])

  const hasPartial = (data?.scores ?? []).some((item) => item.coverage < FULL_COVERAGE)

  return (
    <section className="panel">
      <header>
        <h2>Ranked {group ? 'in this industry' : 'universe'}</h2>
        <span className="muted">
          {data ? `${data.generated_for} scored` : ''}
        </span>
      </header>

      {loading && <p className="muted">Scoring…</p>}
      {error && <p className="error">Failed to score: {error}</p>}
      {data && data.scores.length === 0 && (
        <p className="muted">
          Nothing scored yet. A score needs either 21 sessions of price history or
          scored news — load history with{' '}
          <code>POST /admin/ingest/yahoo?range=1y&amp;only_missing=false</code>.
        </p>
      )}

      {data && data.scores.length > 0 && (
        <>
          <ol className="score-list">
            {data.scores.map((item) => (
              <li key={item.ticker}>
                <button
                  className={item.ticker === selected ? 'row selected' : 'row'}
                  onClick={() => onSelect(item.ticker)}
                  title={rowTitle(item, duplicates.has(item.company_name))}
                >
                  <span className="rank muted">{item.rank}</span>
                  <span className="ticker">{item.ticker}</span>
                  {/* Always rendered, even when empty: the row is a fixed
                      grid, and a cell that appears only sometimes would
                      shift every column after it on those rows. */}
                  <span className="flags">
                    {duplicates.has(item.company_name) && (
                      <span aria-label="Also listed elsewhere in this ranking">⧉</span>
                    )}
                    {item.coverage < FULL_COVERAGE && (
                      <span aria-label="Scored on partial inputs">◐</span>
                    )}
                  </span>
                  <Bar value={item.score} />
                  <strong className={scoreClass(item.score)}>{item.score.toFixed(0)}</strong>
                </button>
                {item.ticker === selected && <Factors score={item} />}
              </li>
            ))}
          </ol>
          <p className="muted score-method">
            Each factor is a percentile against the rest of the universe today. The
            score ranks; it does not forecast. Select a row to see what drove it.
          </p>
          {(hasPartial || duplicates.size > 0) && (
            <p className="muted score-method">
              {hasPartial && (
                <>
                  <strong>◐</strong> scored on part of the inputs — usually too
                  little price history for the 52-week factors, which flatters a
                  symbol that has only recently been added.{' '}
                </>
              )}
              {duplicates.size > 0 && (
                <>
                  <strong>⧉</strong> the same company on a second listing. Two
                  rows, one bet.
                </>
              )}
            </p>
          )}
        </>
      )}
    </section>
  )
}
