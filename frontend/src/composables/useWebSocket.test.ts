// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createWebSocketClient, useWebSocket } from './useWebSocket'
import { AUTH_TOKEN_REFRESHED_EVENT, notifyAuthTokenRefreshed } from '@/utils/authEvents'

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
  const clients: Array<ReturnType<typeof createWebSocketClient>> = []

  /** Client, der nach dem Test wieder abgemeldet wird (er hört auf Refresh-Events) */
  function newClient() {
    const client = createWebSocketClient()
    clients.push(client)
    return client
  }

  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', MockWebSocket)
    mocks.getJwt.mockReset()
    mocks.sockets.length = 0
  })

  afterEach(() => {
    for (const client of clients.splice(0)) client.disconnect()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('uses page scope when requested even if a JWT exists', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = newClient()
    client.connect({ pageId: 'source-page', sessionToken: 'session-1', preferPageScope: true })

    expect(mocks.sockets).toHaveLength(1)
    expect(mocks.sockets[0].url).toContain('/api/v1/ws?page_id=source-page&session_token=session-1')
    expect(mocks.sockets[0].protocols).toBeUndefined()
  })

  it('keeps JWT auth when a page context is provided', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = newClient()
    client.connect({ pageId: 'viewer-page', sessionToken: 'session-1' })

    expect(mocks.sockets).toHaveLength(1)
    expect(mocks.sockets[0].url).toContain('page_id=viewer-page')
    expect(mocks.sockets[0].url).not.toContain('session_token')
    expect(mocks.sockets[0].protocols).toEqual(['obs.jwt.jwt-token'])
  })

  it('does not send session_token in URL when JWT auth is used', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = newClient()
    client.connect({ pageId: 'page-x', sessionToken: 'pin-secret' })

    expect(mocks.sockets[0].protocols).toEqual(['obs.jwt.jwt-token'])
    expect(mocks.sockets[0].url).not.toContain('session_token')
    expect(mocks.sockets[0].url).not.toContain('pin-secret')
  })

  it('keeps JWT transport as the default authenticated path', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = newClient()
    client.connect()

    expect(mocks.sockets).toHaveLength(1)
    expect(mocks.sockets[0].protocols).toEqual(['obs.jwt.jwt-token'])
  })

  it('reconnects when a JWT socket gains page context', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = newClient()
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

    const client = newClient()
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

    const client = newClient()
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

    const client = newClient()
    client.connect({ pageId: 'anon-page', sessionToken: 'session-1', preferPageScope: true })
    client.reconnectWithFreshToken()

    expect(mocks.sockets).toHaveLength(1)
  })

  it('does not reconnect once the JWT is gone', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = newClient()
    client.connect()

    mocks.getJwt.mockReturnValue(null)
    client.reconnectWithFreshToken()

    expect(mocks.sockets).toHaveLength(1)
  })

  it('does not reconnect before the first connect or after a disconnect', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = newClient()
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

  it('reconnects a second client, as WidgetRef runs its own', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const shared = useWebSocket()
    const widgetRef = newClient()
    shared.connect({ pageId: 'viewer-page' })
    widgetRef.connect({ pageId: 'source-page' })
    expect(mocks.sockets).toHaveLength(2)

    mocks.getJwt.mockReturnValue('jwt-new')
    notifyAuthTokenRefreshed()

    expect(mocks.sockets).toHaveLength(4)
    expect(mocks.sockets.slice(2).map(s => s.protocols)).toEqual([
      ['obs.jwt.jwt-new'],
      ['obs.jwt.jwt-new'],
    ])
    shared.disconnect()
  })

  it('releases its refresh listener on disconnect', () => {
    mocks.getJwt.mockReturnValue('jwt-old')
    const removeListener = vi.spyOn(window, 'removeEventListener')

    const client = newClient()
    client.connect()
    client.disconnect()

    // Sonst sammelt jede WidgetRef-Instanz über die Seitenwechsel hinweg Listener an
    expect(removeListener).toHaveBeenCalledWith(AUTH_TOKEN_REFRESHED_EVENT, expect.any(Function))
    removeListener.mockRestore()

    mocks.getJwt.mockReturnValue('jwt-new')
    notifyAuthTokenRefreshed()
    expect(mocks.sockets).toHaveLength(1)
  })

  it('closes the JWT socket when the session ends and continues anonymously', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = newClient()
    client.connect({ pageId: 'public-page' })
    const initialSocket = mocks.sockets[0]
    expect(initialSocket.protocols).toEqual(['obs.jwt.jwt-old'])

    // Endgültig abgelehnte Erneuerung: der Handshake hat den alten JWT
    // gebunden, der Socket lieferte sonst weiter dessen Datapoint-Scope.
    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))

    expect(initialSocket.readyState).toBe(3)
    expect(initialSocket.onclose).toBeNull()
    expect(mocks.sockets).toHaveLength(2)
    expect(mocks.sockets[1].protocols).toBeUndefined()
    expect(mocks.sockets[1].url).toContain('page_id=public-page')
  })

  it('drops a pending backoff reconnect when the session ends', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = newClient()
    client.connect({ pageId: 'public-page' })
    mocks.sockets[0].onclose?.({ code: 1006 })
    expect(vi.getTimerCount()).toBe(1)

    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))

    // Sonst baute der Backoff gleich noch eine Verbindung mit dem alten Token auf
    expect(vi.getTimerCount()).toBe(0)
    expect(mocks.sockets).toHaveLength(2)
    expect(mocks.sockets[1].protocols).toBeUndefined()
  })

  it('leaves no connection behind when the ended session had no page context', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = newClient()
    client.connect()
    const initialSocket = mocks.sockets[0]

    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))

    expect(initialSocket.readyState).toBe(3)
    expect(mocks.sockets).toHaveLength(1)
  })

  it('keeps a page-scoped socket, which never carried the session', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = newClient()
    client.connect({ pageId: 'anon-page', sessionToken: 'session-1', preferPageScope: true })
    const initialSocket = mocks.sockets[0]

    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))

    expect(initialSocket.readyState).toBe(MockWebSocket.CONNECTING)
    expect(mocks.sockets).toHaveLength(1)
  })

  it('keeps the socket when another login already replaced the session', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = newClient()
    client.connect({ pageId: 'viewer-page' })
    const initialSocket = mocks.sockets[0]

    // Anderer Tab hat sich neu angemeldet — dieser Socket gehört bereits ihm
    mocks.getJwt.mockReturnValue('jwt-other')
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))

    expect(initialSocket.readyState).toBe(MockWebSocket.CONNECTING)
    expect(mocks.sockets).toHaveLength(1)
  })

  it('releases its session-end listener on disconnect', () => {
    mocks.getJwt.mockReturnValue('jwt-old')

    const client = newClient()
    client.connect({ pageId: 'viewer-page' })
    client.disconnect()

    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))

    expect(mocks.sockets).toHaveLength(1)
  })

  it('does not reconnect after an explicit disconnect', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = newClient()
    client.connect()
    const initialSocket = mocks.sockets[0]
    client.disconnect()
    vi.advanceTimersByTime(2_000)

    expect(initialSocket.onclose).toBeNull()
    expect(mocks.sockets).toHaveLength(1)
  })
})
