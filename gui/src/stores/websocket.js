/**
 * WebSocket store — connects to /api/v1/ws and distributes live value events.
 *
 * Protocol (server → client):
 *   { type: "value", datapoint_id, value, quality, ts, source_adapter }
 *   { type: "pong" }
 *
 * Protocol (client → server):
 *   { type: "subscribe",   ids: ["uuid", ...] }
 *   { type: "unsubscribe", ids: ["uuid", ...] }
 *   { type: "ping" }
 */
import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'

export const useWebSocketStore = defineStore('websocket', () => {
  const connected    = ref(false)
  const liveValues   = ref({})   // { [datapoint_id]: { value, quality, ts } }
  const _ws          = shallowRef(null)
  const _handlers    = []        // [{ id, fn }] — external value listeners
  const _rbHandlers  = []        // ringbuffer entry listeners
  const _logHandlers = []        // log_entry listeners
  let   _pingInterval = null
  let   _reconnectTimer = null
  let   _shouldReconnect = true

  function connect() {
    if (_ws.value?.readyState === WebSocket.OPEN) return

    const token = localStorage.getItem('access_token')
    if (!token) return

    _shouldReconnect = true

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url   = `${proto}://${window.location.host}/api/v1/ws`
    const ws    = new WebSocket(url, [`obs.jwt.${token}`])
    _ws.value   = ws

    ws.onopen = () => {
      connected.value = true
      _pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: 'ping' }))
      }, 30_000)
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        // Per-subscription DP value event
        if (msg.id && msg.v !== undefined && !msg.action) {
          const dpId = msg.id
          liveValues.value = {
            ...liveValues.value,
            [dpId]: { value: msg.v, quality: msg.q, ts: msg.t }
          }
          _handlers.forEach(h => h.fn(dpId, msg.v, msg.q, msg.t))
        }
        // Legacy format (type: "value")
        if (msg.type === 'value') {
          const { datapoint_id, value, quality, ts } = msg
          liveValues.value = {
            ...liveValues.value,
            [datapoint_id]: { value, quality, ts }
          }
          _handlers.forEach(h => h.fn(datapoint_id, value, quality, ts))
        }
        // RingBuffer live push
        if (msg.action === 'ringbuffer_entry') {
          _rbHandlers.forEach(h => h.fn(msg.entry))
        }
        // Log live push
        if (msg.action === 'log_entry') {
          _logHandlers.forEach(h => h.fn(msg.entry))
        }
        // Server keepalive ping — reply with pong
        if (msg.action === 'ping') {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: 'pong' }))
        }
      } catch { /* ignore malformed */ }
    }

    ws.onclose = (evt) => {
      connected.value = false
      clearInterval(_pingInterval)
      _ws.value = null
      if (evt?.code === 4001) _shouldReconnect = false
      // Reconnect after 5 s
      if (!_shouldReconnect) return
      _reconnectTimer = setTimeout(() => {
        _reconnectTimer = null
        if (_shouldReconnect) connect()
      }, 5000)
    }

    ws.onerror = () => ws.close()
  }

  function disconnect() {
    _shouldReconnect = false
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer)
      _reconnectTimer = null
    }
    clearInterval(_pingInterval)
    _ws.value?.close()
    _ws.value   = null
    connected.value = false
  }

  function subscribe(ids) {
    if (_ws.value?.readyState === WebSocket.OPEN)
      _ws.value.send(JSON.stringify({ action: 'subscribe', ids }))
  }

  function unsubscribe(ids) {
    if (_ws.value?.readyState === WebSocket.OPEN)
      _ws.value.send(JSON.stringify({ action: 'unsubscribe', ids }))
  }

  /** Register a handler to be called on every value event. Returns an unregister fn. */
  function onValue(fn) {
    const entry = { id: Math.random(), fn }
    _handlers.push(entry)
    return () => {
      const idx = _handlers.indexOf(entry)
      if (idx !== -1) _handlers.splice(idx, 1)
    }
  }

  /** Register a handler to be called on every ringbuffer_entry push. Returns unregister fn. */
  function onRingbufferEntry(fn) {
    const entry = { id: Math.random(), fn }
    _rbHandlers.push(entry)
    return () => {
      const idx = _rbHandlers.indexOf(entry)
      if (idx !== -1) _rbHandlers.splice(idx, 1)
    }
  }

  /** Register a handler to be called on every log_entry push. Returns unregister fn. */
  function onLogEntry(fn) {
    const entry = { id: Math.random(), fn }
    _logHandlers.push(entry)
    return () => {
      const idx = _logHandlers.indexOf(entry)
      if (idx !== -1) _logHandlers.splice(idx, 1)
    }
  }

  return { connected, liveValues, connect, disconnect, subscribe, unsubscribe, onValue, onRingbufferEntry, onLogEntry }
})
