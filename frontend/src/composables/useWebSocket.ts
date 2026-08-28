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
import { AUTH_TOKEN_REFRESHED_EVENT } from '@/utils/authEvents'

type MessageHandler = (data: Record<string, unknown>) => void
type ConnectContext = {
  pageId?: string
  sessionToken?: string
  preferPageScope?: boolean
}

const WS_URL = () => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/v1/ws`
}

const MAX_DELAY = 30_000

export function createWebSocketClient() {
  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectDelay = 1000
  let shouldReconnect = false
  const connected = ref(false)
  const handlers = new Set<MessageHandler>()
  let connectContext: ConnectContext = {}
  // Nur ein mit JWT aufgebauter Socket trägt den Scope der Anmeldung und muss
  // fallen, wenn diese endet — ein seitenbezogener ist davon unabhängig.
  let socketUsesJwt = false
  const subscribedIds = new Set<string>()

  function send(data: unknown) {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(data))
    }
  }

  function dispatch(data: Record<string, unknown>) {
    for (const handler of handlers) handler(data)
  }

  function sameConnectContext(a: ConnectContext, b: ConnectContext) {
    return a.pageId === b.pageId && a.sessionToken === b.sessionToken && a.preferPageScope === b.preferPageScope
  }

  function detachSocketHandlers(current: WebSocket) {
    current.onclose = null
    current.onerror = null
    current.onmessage = null
    current.onopen = null
  }

  function connect(nextContext: ConnectContext = {}) {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      if (sameConnectContext(connectContext, nextContext)) return
      detachSocketHandlers(socket)
      socket.close()
      socket = null
      connected.value = false
    }

    connectContext = nextContext
    shouldReconnect = true
    attachAuthListeners()
    const jwt = getJwt()
    let url = WS_URL()
    if (jwt && !connectContext.preferPageScope) {
      if (connectContext.pageId) {
        url = `${url}?${new URLSearchParams({ page_id: connectContext.pageId }).toString()}`
      }
      socket = new WebSocket(url, [`obs.jwt.${jwt}`])
      socketUsesJwt = true
    } else {
      if (!connectContext.pageId) return
      const params = new URLSearchParams({ page_id: connectContext.pageId })
      if (connectContext.sessionToken) params.set('session_token', connectContext.sessionToken)
      url = `${url}?${params.toString()}`
      socket = new WebSocket(url)
      socketUsesJwt = false
    }

    socket.onopen = () => {
      connected.value = true
      reconnectDelay = 1000
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (subscribedIds.size > 0) {
        send({ action: 'subscribe', ids: Array.from(subscribedIds) })
      }
    }

    socket.onclose = (event) => {
      connected.value = false
      socket = null
      if (!shouldReconnect) return
      if (event.code === 4001) return
      scheduleReconnect()
    }

    socket.onerror = () => {
      socket?.close()
    }

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>
        dispatch(data)
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

  // Jeder Client hört selbst auf Refresh und Sitzungsende — WidgetRef betreibt
  // eine eigene Instanz neben dem Singleton und muss genauso reagieren.
  let authListenersAttached = false

  function attachAuthListeners() {
    if (authListenersAttached) return
    window.addEventListener(AUTH_TOKEN_REFRESHED_EVENT, reconnectWithFreshToken)
    window.addEventListener('visu:unauthorized', dropSessionSocket)
    authListenersAttached = true
  }

  function detachAuthListeners() {
    if (!authListenersAttached) return
    window.removeEventListener(AUTH_TOKEN_REFRESHED_EVENT, reconnectWithFreshToken)
    window.removeEventListener('visu:unauthorized', dropSessionSocket)
    authListenersAttached = false
  }

  /**
   * Nach einem Token-Refresh neu verbinden.
   *
   * Der JWT steckt im Subprotokoll (`obs.jwt.<token>`) und wird beim Handshake
   * gebunden — ein erneuerter Token erreicht eine bestehende Verbindung nicht.
   * Ohne Reconnect verliert das Backend beim nächsten Datapoint-Scope-Refresh
   * den Scope und die Seite friert still ein (Issue #1160).
   */
  function reconnectWithFreshToken() {
    if (!shouldReconnect) return
    if (connectContext.preferPageScope || !getJwt()) return
    const context = connectContext
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (socket) {
      detachSocketHandlers(socket)
      socket.close()
      socket = null
      connected.value = false
    }
    reconnectDelay = 1000
    connect(context)
  }

  /**
   * Die Anmeldung ist endgültig weg (Refresh-Token abgelehnt oder Logout).
   *
   * Der Handshake bindet den JWT — ein bereits offener Socket behält deshalb
   * den Datapoint-Scope der beendeten Anmeldung, bis der Token serverseitig
   * abläuft. Besonders sichtbar auf einer öffentlichen Seite, die nach dem
   * Ereignis bewusst stehen bleibt: die Anzeige meldet „abgemeldet", während
   * der Socket weiter Werte aus dem Scope der alten Anmeldung liefert. Mit
   * Seiten-ID wird danach anonym im öffentlichen Scope neu verbunden.
   */
  function dropSessionSocket() {
    if (!socketUsesJwt) return
    // Eine andere Anmeldung hat den Speicher bereits übernommen — deren Socket
    // gehört nicht uns und darf nicht wegen unseres 401 fallen.
    if (getJwt()) return
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (socket) {
      detachSocketHandlers(socket)
      socket.close()
      socket = null
      connected.value = false
    }
    socketUsesJwt = false
    reconnectDelay = 1000
    // Der Listener hängt nur zwischen connect() und disconnect(), ein
    // `shouldReconnect`-Check wäre hier also immer wahr.
    if (connectContext.pageId) connect(connectContext)
  }

  return {
    connected: readonly(connected),

    /** Verbindung starten (idempotent) */
    connect,

    reconnectWithFreshToken,

    /** Verbindung trennen und Reconnect verhindern */
    disconnect() {
      shouldReconnect = false
      detachAuthListeners()
      subscribedIds.clear()
      connectContext = {}
      socketUsesJwt = false
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (socket) {
        detachSocketHandlers(socket)
        socket.close()
      }
      socket = null
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

    dispatch,
  }
}

const defaultClient = createWebSocketClient()

// ── Composable ────────────────────────────────────────────────────────────────

export function useWebSocket() {
  return defaultClient
}
