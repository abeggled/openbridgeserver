import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

let constructorCalls = 0

class FakeWS {
  constructor(url, protocols) {
    constructorCalls++
    this.url = url
    this.protocols = protocols
    this.readyState = 0 // CONNECTING
    this.sent = []
    FakeWS.instance = this
  }
  send(data) { this.sent.push(data) }
  close(code) {
    this.readyState = 3 // CLOSED
    this.onclose?.({ code })
  }
  simulateOpen() { this.readyState = 1; this.onopen?.() }
  simulateMessage(obj) { this.onmessage?.({ data: JSON.stringify(obj) }) }
  simulateError() { this.onerror?.() }
}
FakeWS.OPEN = 1
FakeWS.CONNECTING = 0
FakeWS.CLOSED = 3

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  constructorCalls = 0
  FakeWS.instance = null
  globalThis.WebSocket = FakeWS
  localStorage.setItem('access_token', 'test-token')
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.useRealTimers()
  localStorage.removeItem('access_token')
})

describe('useWebSocketStore', () => {
  it('connect builds a WebSocket URL and sets connected=true on open', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()

    store.connect()
    expect(constructorCalls).toBe(1)
    expect(FakeWS.instance.url).toContain('/api/v1/ws')

    FakeWS.instance.simulateOpen()
    expect(store.connected).toBe(true)
  })

  it('connect does nothing when no access_token in localStorage', async () => {
    localStorage.removeItem('access_token')
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()

    store.connect()
    expect(constructorCalls).toBe(0)
    expect(store.connected).toBe(false)
  })

  it('connect is a no-op when the socket is already OPEN', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()

    store.connect()
    FakeWS.instance.simulateOpen()
    store.connect() // already OPEN → guard fires

    expect(constructorCalls).toBe(1)
  })

  it('handles compact value event and updates liveValues', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    FakeWS.instance.simulateMessage({ id: 'dp-1', v: 42, q: 'good', t: '2024-01-01T00:00:00Z' })

    expect(store.liveValues['dp-1']).toEqual({ value: 42, quality: 'good', ts: '2024-01-01T00:00:00Z' })
  })

  it('handles legacy type:"value" event and updates liveValues', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    FakeWS.instance.simulateMessage({ type: 'value', datapoint_id: 'dp-2', value: 99, quality: 'uncertain', ts: 'now' })

    expect(store.liveValues['dp-2']).toEqual({ value: 99, quality: 'uncertain', ts: 'now' })
  })

  it('calls onValue handlers for compact events', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    const handler = vi.fn()
    store.onValue(handler)
    FakeWS.instance.simulateMessage({ id: 'dp-3', v: 1, q: 'good', t: 'ts' })

    expect(handler).toHaveBeenCalledWith('dp-3', 1, 'good', 'ts')
  })

  it('onValue unregister fn removes the handler', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    const handler = vi.fn()
    const unregister = store.onValue(handler)
    unregister()
    FakeWS.instance.simulateMessage({ id: 'dp-x', v: 1, q: 'good', t: 'ts' })

    expect(handler).not.toHaveBeenCalled()
  })

  it('calls onRingbufferEntry handlers on matching action', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    const handler = vi.fn()
    store.onRingbufferEntry(handler)
    FakeWS.instance.simulateMessage({ action: 'ringbuffer_entry', entry: { id: 99 } })

    expect(handler).toHaveBeenCalledWith({ id: 99 })
  })

  it('onRingbufferEntry unregister fn removes the handler', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    const handler = vi.fn()
    const off = store.onRingbufferEntry(handler)
    off()
    FakeWS.instance.simulateMessage({ action: 'ringbuffer_entry', entry: {} })

    expect(handler).not.toHaveBeenCalled()
  })

  it('calls onLogEntry handlers on matching action', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    const handler = vi.fn()
    store.onLogEntry(handler)
    FakeWS.instance.simulateMessage({ action: 'log_entry', entry: { level: 'ERROR', msg: 'oops' } })

    expect(handler).toHaveBeenCalledWith({ level: 'ERROR', msg: 'oops' })
  })

  it('onLogEntry unregister fn removes the handler', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    const handler = vi.fn()
    const off = store.onLogEntry(handler)
    off()
    FakeWS.instance.simulateMessage({ action: 'log_entry', entry: {} })

    expect(handler).not.toHaveBeenCalled()
  })

  it('replies with pong on server ping action', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    FakeWS.instance.simulateMessage({ action: 'ping' })

    expect(FakeWS.instance.sent).toContainEqual(JSON.stringify({ action: 'pong' }))
  })

  it('subscribe sends subscribe message when OPEN', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    store.subscribe(['dp-1', 'dp-2'])

    expect(FakeWS.instance.sent).toContainEqual(JSON.stringify({ action: 'subscribe', ids: ['dp-1', 'dp-2'] }))
  })

  it('unsubscribe sends unsubscribe message when OPEN', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    store.unsubscribe(['dp-1'])

    expect(FakeWS.instance.sent).toContainEqual(JSON.stringify({ action: 'unsubscribe', ids: ['dp-1'] }))
  })

  it('subscribe is a no-op when socket is not OPEN', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect() // CONNECTING, not yet OPEN

    store.subscribe(['dp-1'])

    expect(FakeWS.instance.sent).toHaveLength(0)
  })

  it('onclose sets connected=false and schedules reconnect after 5s', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()
    expect(store.connected).toBe(true)

    FakeWS.instance.close(1000)
    expect(store.connected).toBe(false)

    // After the 5-second timer fires, connect() is called again
    vi.advanceTimersByTime(5001)
    expect(constructorCalls).toBe(2)
  })

  it('onclose with code 4001 suppresses reconnect', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    FakeWS.instance.close(4001)
    vi.advanceTimersByTime(6000)

    expect(constructorCalls).toBe(1) // no reconnect
    expect(store.connected).toBe(false)
  })

  it('onerror calls close which triggers reconnect flow', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    FakeWS.instance.simulateError() // onerror → ws.close()
    expect(store.connected).toBe(false)
  })

  it('disconnect prevents reconnect and sets connected=false', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    store.disconnect()
    expect(store.connected).toBe(false)

    vi.advanceTimersByTime(6000)
    expect(constructorCalls).toBe(1) // no reconnect after explicit disconnect
  })

  it('ignores malformed JSON messages without throwing', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    const store = useWebSocketStore()
    store.connect()
    FakeWS.instance.simulateOpen()

    FakeWS.instance.onmessage?.({ data: 'not-valid-json{{' })

    expect(store.connected).toBe(true) // connection unaffected
  })
})
