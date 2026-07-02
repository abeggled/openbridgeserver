/**
 * useWebSocket — WebSocket-Verbindung zum open bridge server Backend
 *
 * Singleton: eine einzige WS-Verbindung für die gesamte App.
 * Automatischer Reconnect mit exponentiellem Backoff.
 * Subscription-Buffering: Abonnements werden beim Verbindungsaufbau
 * automatisch erneut gesendet.
 */

import { ref, readonly } from 'vue'
import { getJwt } from '@/api/client'

type MessageHandler = (data: Record<string, unknown>) => void
type ConnectContext = {
  pageId?: string
  sessionToken?: string
}

const WS_URL = () => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/v1/ws`
}

// ── Singleton-State ───────────────────────────────────────────────────────────

let socket: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000
const MAX_DELAY = 30_000

const connected = ref(false)
const handlers = new Set<MessageHandler>()
let connectContext: ConnectContext = {}

// Puffert alle aktuell abonnierten IDs → wird beim (Re-)Connect gesendet
const subscribedIds = new Set<string>()

// ── Interne Funktionen ────────────────────────────────────────────────────────

function contextKey(context: ConnectContext, jwt: string | null): string {
  return JSON.stringify({
    jwt: jwt || '',
    pageId: context.pageId || '',
    sessionToken: context.sessionToken || '',
  })
}

function socketUrl(context: ConnectContext): string {
  let url = WS_URL()
  if (!context.pageId) return url
  const params = new URLSearchParams({ page_id: context.pageId })
  if (context.sessionToken) params.set('session_token', context.sessionToken)
  return `${url}?${params.toString()}`
}

function closeCurrentSocket() {
  if (!socket) return
  socket.onopen = null
  socket.onclose = null
  socket.onerror = null
  socket.onmessage = null
  socket.close()
  socket = null
  connected.value = false
}

function send(data: unknown) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(data))
  }
}

function connect(nextContext: ConnectContext = {}) {
  const jwt = getJwt()
  const nextKey = contextKey(nextContext, jwt)
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    if (contextKey(connectContext, jwt) === nextKey) return
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    closeCurrentSocket()
  }

  connectContext = nextContext
  const url = socketUrl(connectContext)
  if (jwt) {
    socket = new WebSocket(url, [`obs.jwt.${jwt}`])
  } else {
    if (!connectContext.pageId) return
    socket = new WebSocket(url)
  }
  const activeSocket = socket

  socket.onopen = () => {
    if (socket !== activeSocket) return
    connected.value = true
    reconnectDelay = 1000
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    // Gepufferte Subscriptions nach (Re-)Connect sofort senden
    if (subscribedIds.size > 0) {
      send({ action: 'subscribe', ids: Array.from(subscribedIds) })
    }
  }

  socket.onclose = (event) => {
    if (socket !== activeSocket) return
    connected.value = false
    socket = null
    if (event.code === 4001) return
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
    connect(connectContext)
  }, reconnectDelay)
}

// ── Composable ────────────────────────────────────────────────────────────────

export function useWebSocket() {
  return {
    connected: readonly(connected),

    /** Verbindung starten (idempotent) */
    connect,

    /** Verbindung trennen und Reconnect verhindern */
    disconnect() {
      subscribedIds.clear()
      connectContext = {}
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      closeCurrentSocket()
    },

    /** DataPoint-IDs abonnieren — puffert und sendet bei Verbindungsaufbau */
    subscribe(ids: string[]) {
      ids.forEach(id => subscribedIds.add(id))
      // Sofort senden wenn Socket offen, sonst automatisch bei onopen
      send({ action: 'subscribe', ids })
    },

    /** DataPoint-IDs abbestellen */
    unsubscribe(ids: string[]) {
      ids.forEach(id => subscribedIds.delete(id))
      send({ action: 'unsubscribe', ids })
    },

    /** Handler für eingehende Nachrichten registrieren. Gibt Abmelde-Funktion zurück. */
    onMessage(handler: MessageHandler): () => void {
      handlers.add(handler)
      return () => handlers.delete(handler)
    },
  }
}
