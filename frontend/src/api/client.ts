// Thin fetch wrapper over the FastAPI backend.

import type {
  Alert,
  AlertHistoryEntry,
  AlertType,
  BacktestResult,
  NewsArticle,
  Portfolio,
  PortfolioDetail,
  Price,
  Sentiment,
  SimulationResponse,
  Stock,
  StockDetail,
  Trade,
  TradeSide,
} from './types'

export const API_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export const WS_URL = API_URL.replace(/^http/, 'ws')

// --- Session ----------------------------------------------------------------

const TOKEN_KEY = 'ti.token'

let token: string | null = localStorage.getItem(TOKEN_KEY)

/** Raised on a 401 so callers can show the sign-in screen instead of an error. */
export class Unauthorized extends Error {}

export function getToken(): string | null {
  return token
}

export function setToken(value: string | null): void {
  token = value
  if (value) localStorage.setItem(TOKEN_KEY, value)
  else localStorage.removeItem(TOKEN_KEY)
}

/** Listeners fire when the stored session stops being accepted. */
const expiryListeners = new Set<() => void>()

export function onSessionExpired(listener: () => void): () => void {
  expiryListeners.add(listener)
  return () => expiryListeners.delete(listener)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
  })

  // A 401 from /auth/login means "wrong password" and belongs to the caller.
  // A 401 anywhere else means the token we sent is gone or stale.
  if (response.status === 401 && path !== '/auth/login') {
    setToken(null)
    expiryListeners.forEach((listener) => listener())
    throw new Unauthorized('Your session has expired. Sign in again.')
  }

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

export interface SessionState {
  authenticated: boolean
  auth_required: boolean
  subject: string | null
}

export function getSession(): Promise<SessionState> {
  return request('/auth/session')
}

export async function login(password: string): Promise<void> {
  const result = await request<{ token: string }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  })
  setToken(result.token)
}

export function logout(): void {
  setToken(null)
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

// --- Portfolio --------------------------------------------------------------

export function getPortfolios(): Promise<Portfolio[]> {
  return request('/portfolios')
}

export function createPortfolio(input: {
  name: string
  starting_cash: number
}): Promise<Portfolio> {
  return request('/portfolios', { method: 'POST', body: JSON.stringify(input) })
}

export function getPortfolio(id: number): Promise<PortfolioDetail> {
  return request(`/portfolios/${id}`)
}

export function deletePortfolio(id: number): Promise<void> {
  return request(`/portfolios/${id}`, { method: 'DELETE' })
}

export function getTrades(id: number): Promise<Trade[]> {
  return request(`/portfolios/${id}/trades`)
}

export function createTrade(
  id: number,
  input: { ticker: string; side: TradeSide; quantity: number; price?: number },
): Promise<Trade> {
  return request(`/portfolios/${id}/trades`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function runSimulation(
  id: number,
  input: { days?: number; hold_days?: number; min_score?: number },
): Promise<SimulationResponse> {
  return request(`/portfolios/${id}/simulate`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}
