// One WebSocket for the whole app: live prices for every watched ticker plus
// alert pushes, with automatic reconnection.

import { useEffect, useRef, useState } from 'react'

import { WS_URL, getToken } from '../api/client'
import type { AlertPush, ServerMessage } from '../api/types'

export interface LivePrice {
  price: number | null
  change: number | null
  timestamp: string
}

const RECONNECT_DELAY_MS = 3000
const MAX_ALERTS_KEPT = 20

export function useTickerSocket(tickers: string[]) {
  const [prices, setPrices] = useState<Record<string, LivePrice>>({})
  const [alerts, setAlerts] = useState<AlertPush[]>([])
  const [connected, setConnected] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)
  const tickersRef = useRef<string[]>(tickers)
  tickersRef.current = tickers

  // The socket lives for the component's lifetime; subscriptions follow the
  // ticker list without tearing the connection down.
  useEffect(() => {
    let disposed = false
    let reconnectTimer: number | undefined

    function connect() {
      if (disposed || tickersRef.current.length === 0) return
      const [first, ...rest] = tickersRef.current
      // Browsers cannot set headers on a WebSocket, so the token rides along
      // as a query parameter; the server rejects the handshake without it.
      const auth = getToken()
      const socket = new WebSocket(
        `${WS_URL}/ws/tickers/${first}${auth ? `?token=${encodeURIComponent(auth)}` : ''}`,
      )
      socketRef.current = socket

      socket.onopen = () => {
        setConnected(true)
        if (rest.length > 0) {
          socket.send(JSON.stringify({ action: 'subscribe', tickers: rest }))
        }
      }

      socket.onmessage = (event: MessageEvent<string>) => {
        let message: ServerMessage
        try {
          message = JSON.parse(event.data) as ServerMessage
        } catch {
          return
        }
        if (message.type === 'price_update' || message.type === 'snapshot') {
          const { ticker, price, change, timestamp } = message
          setPrices((current) => ({ ...current, [ticker]: { price, change, timestamp } }))
        } else if (message.type === 'alert') {
          setAlerts((current) => [message, ...current].slice(0, MAX_ALERTS_KEPT))
        }
      }

      socket.onclose = () => {
        setConnected(false)
        socketRef.current = null
        if (!disposed) {
          reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS)
        }
      }
      socket.onerror = () => socket.close()
    }

    connect()
    return () => {
      disposed = true
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      socketRef.current?.close()
    }
    // Reconnect from scratch only when the first ticker changes identity;
    // other list changes are handled by the subscribe effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickers[0]])

  // Keep subscriptions in sync when the watchlist grows.
  useEffect(() => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN && tickers.length > 1) {
      socket.send(JSON.stringify({ action: 'subscribe', tickers: tickers.slice(1) }))
    }
  }, [tickers])

  const dismissAlert = (index: number) =>
    setAlerts((current) => current.filter((_, i) => i !== index))

  return { prices, alerts, connected, dismissAlert }
}
