// Thin fetch wrapper over the FastAPI backend.

import type {
  Alert,
  AlertHistoryEntry,
  AlertType,
  BacktestResult,
  NewsArticle,
  Price,
  Sentiment,
  Stock,
  StockDetail,
} from './types'

export const API_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export const WS_URL = API_URL.replace(/^http/, 'ws')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // Non-JSON error body; keep the status text.
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export interface NewsFilters {
  ticker?: string
  sentiment?: Sentiment
  event_type?: string
  since_days?: number
  limit?: number
}

export function getNews(filters: NewsFilters = {}): Promise<NewsArticle[]> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  return request(`/news?${params}`)
}

export function getStocks(): Promise<Stock[]> {
  return request('/stocks?limit=1000')
}

export function getStock(ticker: string): Promise<StockDetail> {
  return request(`/stocks/${encodeURIComponent(ticker)}`)
}

export function getPrices(ticker: string, days = 90): Promise<Price[]> {
  return request(`/stocks/${encodeURIComponent(ticker)}/prices?days=${days}`)
}

export function getAlerts(): Promise<Alert[]> {
  return request('/alerts')
}

export function createAlert(input: {
  ticker: string
  alert_type: AlertType
  condition?: Record<string, unknown>
  channels?: string[]
}): Promise<Alert> {
  return request('/alerts', { method: 'POST', body: JSON.stringify(input) })
}

export function deleteAlert(id: number): Promise<void> {
  return request(`/alerts/${id}`, { method: 'DELETE' })
}

export function getAlertHistory(): Promise<AlertHistoryEntry[]> {
  return request('/alerts/history')
}

export function getBacktest(ticker: string, days = 180): Promise<BacktestResult> {
  return request(`/backtest?ticker=${encodeURIComponent(ticker)}&days=${days}`)
}
