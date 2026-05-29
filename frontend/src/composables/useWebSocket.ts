/**
 * useWebSocket — WebSocket-Verbindung zum open bridge server Backend
 *
 * Singleton: eine einzige WS-Verbindung für die gesamte App.
 * Automatischer Reconnect mit exponentiellem Backoff.
 * Subscription-Buffering: Abonnements werden beim Verbindungsaufbau
 * automatisch erneut gesendet.
 */

import { ref, readonly } from 'vue'
import { getJwt, getWriteContext } from '@/api/client'

type MessageHandler = (data: Record<string, unknown>) => void

const WS_PREFIX = 'obs.jwt.'
const WS_SESSION_PREFIX = 'obs.session.'

type SocketContext = {
  url: string
  protocols?: string[]
}

const getSocketContext = (): SocketContext => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const jwt = getJwt()
  const ctx = getWriteContext()
  const params = new URLSearchParams()
  if (ctx.pageId) params.set('page_id', ctx.pageId)
  const query = params.toString()
  const url = `${proto}://${location.host}/api/v1/ws${query ? `?${query}` : ''}`
  if (jwt) return { url, protocols: [`${WS_PREFIX}${jwt}`] }
  if (ctx.sessionToken) return { url, protocols: [`${WS_SESSION_PREFIX}${ctx.sessionToken}`] }
  return { url }
}

// ── Singleton-State ───────────────────────────────────────────────────────────

let socket: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000
const MAX_DELAY = 30_000
let socketContextKey = ''

const connected = ref(false)
const handlers = new Set<MessageHandler>()

// Puffert alle aktuell abonnierten IDs → wird beim (Re-)Connect gesendet
const subscribedIds = new Set<string>()

// ── Interne Funktionen ────────────────────────────────────────────────────────

function send(data: unknown) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(data))
  }
}

function connect() {
  const ctx = getSocketContext()
  const nextContextKey = `${ctx.url}::${ctx.protocols?.join(',') ?? ''}`

  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    if (socketContextKey === nextContextKey) return
    socket.close()
  }

  const ws = ctx.protocols ? new WebSocket(ctx.url, ctx.protocols) : new WebSocket(ctx.url)
  socket = ws
  socketContextKey = nextContextKey

  ws.onopen = () => {
    if (socket !== ws) return
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

  ws.onclose = () => {
    if (socket !== ws) return
    connected.value = false
    socket = null
    socketContextKey = ''
    scheduleReconnect()
  }

  ws.onerror = () => {
    if (socket !== ws) return
    ws.close()
  }

  ws.onmessage = (event) => {
    if (socket !== ws) return
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

// ── Composable ────────────────────────────────────────────────────────────────

export function useWebSocket() {
  return {
    connected: readonly(connected),

    /** Verbindung starten (idempotent) */
    connect,

    /** Verbindung trennen und Reconnect verhindern */
    disconnect() {
      subscribedIds.clear()
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      socket?.close()
      socket = null
      socketContextKey = ''
      connected.value = false
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
