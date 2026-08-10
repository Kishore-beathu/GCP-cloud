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

export function ScorePanel({ group, onSelect, selected }: Props) {
  const { data, error, loading } = useAsync(
    () => getScores({ group: group || undefined, limit: 10 }),
    [group],
  )

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
                  title={`${item.company_name} · rank ${item.rank} of ${item.universe_size}`}
                >
                  <span className="rank muted">{item.rank}</span>
                  <span className="ticker">{item.ticker}</span>
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
        </>
      )}
    </section>
  )
}
