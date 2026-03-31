/**
 * useWebSocket — WebSocket-Verbindung zum openTWS Backend
 *
 * Singleton: eine einzige WS-Verbindung für die gesamte App.
 * Automatischer Reconnect mit exponentiellem Backoff.
 */

import { ref, readonly } from 'vue'
import { getJwt } from '@/api/client'

type MessageHandler = (data: Record<string, unknown>) => void

const WS_URL = () => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const jwt = getJwt()
  return `${proto}://${location.host}/api/v1/ws${jwt ? `?token=${jwt}` : ''}`
}

// ── Singleton-State ───────────────────────────────────────────────────────────

let socket: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000
const MAX_DELAY = 30_000

const connected = ref(false)
const handlers = new Set<MessageHandler>()

// ── Interne Funktionen ────────────────────────────────────────────────────────

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return
  }

  const url = WS_URL()
  if (!url.includes('token=')) {
    // Kein JWT → nicht verbinden (public-only Seite)
    return
  }

  socket = new WebSocket(url)

  socket.onopen = () => {
    connected.value = true
    reconnectDelay = 1000
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  socket.onclose = () => {
    connected.value = false
    socket = null
    scheduleReconnect()
  }

  socket.onerror = () => {
    socket?.close()
  }

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data) as Record<string, unknown>
      for (const handler of handlers) handler(data)
    } catch {
      // ungültige Nachricht ignorieren
    }
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_DELAY)
    connect()
  }, reconnectDelay)
}

function send(data: unknown) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(data))
  }
}

// ── Composable ────────────────────────────────────────────────────────────────

export function useWebSocket() {
  return {
    connected: readonly(connected),

    /** Verbindung starten (idempotent) */
    connect,

    /** Verbindung trennen und Reconnect verhindern */
    disconnect() {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      socket?.close()
      socket = null
      connected.value = false
    },

    /** DataPoint-IDs abonnieren */
    subscribe(ids: string[]) {
      send({ action: 'subscribe', ids })
    },

    /** DataPoint-IDs abbestellen */
    unsubscribe(ids: string[]) {
      send({ action: 'unsubscribe', ids })
    },

    /** Handler für eingehende Nachrichten registrieren. Gibt Abmelde-Funktion zurück. */
    onMessage(handler: MessageHandler): () => void {
      handlers.add(handler)
      return () => handlers.delete(handler)
    },
  }
}
