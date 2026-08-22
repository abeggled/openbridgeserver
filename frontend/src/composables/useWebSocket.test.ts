// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createWebSocketClient, useWebSocket } from './useWebSocket'
import { notifyAuthTokenRefreshed } from '@/utils/authEvents'

const mocks = vi.hoisted(() => ({
  getJwt: vi.fn(),
  sockets: [] as Array<{
    url: string
    protocols?: string | string[]
    readyState: number
    sent: string[]
    onclose?: ((event: { code: number }) => void) | null
  }>,
}))

vi.mock('@/api/client', () => ({
  getJwt: mocks.getJwt,
}))

class MockWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1

  url: string
  protocols?: string | string[]
  readyState = MockWebSocket.CONNECTING
  sent: string[] = []
  onopen?: () => void
  onclose?: ((event: { code: number }) => void) | null
  onerror?: () => void
  onmessage?: (event: { data: string }) => void

  constructor(url: string, protocols?: string | string[]) {
    this.url = url
    this.protocols = protocols
    mocks.sockets.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = 3
    this.onclose?.({ code: 1000 })
  }
}

describe('createWebSocketClient', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', MockWebSocket)
    mocks.getJwt.mockReset()
    mocks.sockets.length = 0
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('uses page scope when requested even if a JWT exists', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = createWebSocketClient()
    client.connect({ pageId: 'source-page', sessionToken: 'session-1', preferPageScope: true })

    expect(mocks.sockets).toHaveLength(1)
    expect(mocks.sockets[0].url).toContain('/api/v1/ws?page_id=source-page&session_token=session-1')
    expect(mocks.sockets[0].protocols).toBeUndefined()
  })

  it('keeps JWT auth when a page context is provided', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = createWebSocketClient()
    client.connect({ pageId: 'viewer-page', sessionToken: 'session-1' })

    expect(mocks.sockets).toHaveLength(1)
    expect(mocks.sockets[0].url).toContain('page_id=viewer-page')
    expect(mocks.sockets[0].url).not.toContain('session_token')
    expect(mocks.sockets[0].protocols).toEqual(['obs.jwt.jwt-token'])
  })

  it('does not send session_token in URL when JWT auth is used', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = createWebSocketClient()
    client.connect({ pageId: 'page-x', sessionToken: 'pin-secret' })

    expect(mocks.sockets[0].protocols).toEqual(['obs.jwt.jwt-token'])
    expect(mocks.sockets[0].url).not.toContain('session_token')
    expect(mocks.sockets[0].url).not.toContain('pin-secret')
  })

  it('keeps JWT transport as the default authenticated path', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = createWebSocketClient()
    client.connect()

    expect(mocks.sockets).toHaveLength(1)
    expect(mocks.sockets[0].protocols).toEqual(['obs.jwt.jwt-token'])
  })

  it('reconnects when a JWT socket gains page context', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = createWebSocketClient()
    client.connect()
    const initialSocket = mocks.sockets[0]

    client.connect({ pageId: 'viewer-page', sessionToken: 'session-1' })

    expect(initialSocket.readyState).toBe(3)
    expect(initialSocket.onclose).toBeNull()
    expect(mocks.sockets).toHaveLength(2)
    expect(mocks.sockets[1].url).toContain('page_id=viewer-page')
    expect(mocks.sockets[1].url).not.toContain('session_token')
    expect(mocks.sockets[1].protocols).toEqual(['obs.jwt.jwt-token'])
  })

  it('reconnects with the renewed token after a refresh', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = createWebSocketClient()
    client.connect({ pageId: 'viewer-page' })
    const initialSocket = mocks.sockets[0]

    mocks.getJwt.mockReturnValue('jwt-new')
    client.reconnectWithFreshToken()

    expect(initialSocket.readyState).toBe(3)
    expect(initialSocket.onclose).toBeNull()
    expect(mocks.sockets).toHaveLength(2)
    expect(mocks.sockets[1].protocols).toEqual(['obs.jwt.jwt-new'])
    expect(mocks.sockets[1].url).toContain('page_id=viewer-page')
  })

  it('cancels a pending backoff reconnect when the token is refreshed', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = createWebSocketClient()
    client.connect()
    mocks.sockets[0].onclose?.({ code: 1006 })
    expect(vi.getTimerCount()).toBe(1)

    mocks.getJwt.mockReturnValue('jwt-new')
    client.reconnectWithFreshToken()

    // Der Backoff-Timer ist weg — sonst würde er später eine zweite, mit dem
    // gleichen Token zum Scheitern verurteilte Verbindung aufbauen.
    expect(vi.getTimerCount()).toBe(0)
    expect(mocks.sockets).toHaveLength(2)
    expect(mocks.sockets[1].protocols).toEqual(['obs.jwt.jwt-new'])
  })

  it('does not reconnect a page-scoped connection, which carries no JWT', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = createWebSocketClient()
    client.connect({ pageId: 'anon-page', sessionToken: 'session-1', preferPageScope: true })
    client.reconnectWithFreshToken()

    expect(mocks.sockets).toHaveLength(1)
  })

  it('does not reconnect once the JWT is gone', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = createWebSocketClient()
    client.connect()

    mocks.getJwt.mockReturnValue(null)
    client.reconnectWithFreshToken()

    expect(mocks.sockets).toHaveLength(1)
  })

  it('does not reconnect before the first connect or after a disconnect', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = createWebSocketClient()
    client.reconnectWithFreshToken()
    expect(mocks.sockets).toHaveLength(0)

    client.connect()
    client.disconnect()
    client.reconnectWithFreshToken()
    expect(mocks.sockets).toHaveLength(1)
  })

  it('reconnects the shared client when a token refresh is announced', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = useWebSocket()
    client.connect()
    expect(mocks.sockets).toHaveLength(1)

    mocks.getJwt.mockReturnValue('jwt-new')
    notifyAuthTokenRefreshed()

    expect(mocks.sockets).toHaveLength(2)
    expect(mocks.sockets[1].protocols).toEqual(['obs.jwt.jwt-new'])
    client.disconnect()
  })

  it('does not reconnect after an explicit disconnect', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = createWebSocketClient()
    client.connect()
    const initialSocket = mocks.sockets[0]
    client.disconnect()
    vi.advanceTimersByTime(2_000)

    expect(initialSocket.onclose).toBeNull()
    expect(mocks.sockets).toHaveLength(1)
  })
})
