import type { AlertPush } from '../api/types'
import { SentimentBadge } from './badges'

interface Props {
  alerts: AlertPush[]
  onDismiss: (index: number) => void
}

export function AlertToasts({ alerts, onDismiss }: Props) {
  if (alerts.length === 0) return null
  return (
    <div className="toasts" role="status" aria-live="polite">
      {alerts.slice(0, 4).map((alert, index) => (
        <div key={`${alert.article_id}-${index}`} className="toast">
          <div className="toast-head">
            <SentimentBadge sentiment={alert.sentiment} score={alert.score} />
            <button className="ghost" onClick={() => onDismiss(index)} aria-label="Dismiss">
              ×
            </button>
          </div>
          <a href={alert.url} target="_blank" rel="noreferrer">
            {alert.headline}
          </a>
        </div>
      ))}
    </div>
  )
}
