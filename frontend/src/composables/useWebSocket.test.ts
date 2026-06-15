// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createWebSocketClient } from './useWebSocket'

const mocks = vi.hoisted(() => ({
  getJwt: vi.fn(),
  sockets: [] as Array<{
    url: string
    protocols?: string | string[]
    readyState: number
    sent: string[]
    onclose?: (event: { code: number }) => void
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
  onclose?: (event: { code: number }) => void
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

  it('keeps JWT transport as the default authenticated path', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = createWebSocketClient()
    client.connect()

    expect(mocks.sockets).toHaveLength(1)
    expect(mocks.sockets[0].protocols).toEqual(['obs.jwt.jwt-token'])
  })

  it('does not reconnect after an explicit disconnect', () => {
    mocks.getJwt.mockReturnValue('jwt-token')

    const client = createWebSocketClient()
    client.connect()
    client.disconnect()
    vi.advanceTimersByTime(2_000)

    expect(mocks.sockets).toHaveLength(1)
  })
})
