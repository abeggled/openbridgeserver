// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: FakeWebSocket[] = []

  readyState = FakeWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  sent: string[] = []
  closed = false

  constructor(
    public url: string,
    public protocols?: string | string[],
  ) {
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.closed = true
    this.readyState = FakeWebSocket.CLOSED
  }
}

async function loadWebSocket(jwt = '') {
  vi.resetModules()
  FakeWebSocket.instances = []
  vi.doMock('@/api/client', () => ({
    getJwt: () => jwt,
  }))
  vi.stubGlobal('WebSocket', FakeWebSocket)
  const { useWebSocket } = await import('./useWebSocket')
  return useWebSocket()
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.doUnmock('@/api/client')
})

describe('useWebSocket', () => {
  it('reconnects when the page scope changes', async () => {
    const ws = await loadWebSocket()

    ws.connect({ pageId: 'page-1', sessionToken: 'token-1' })
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toContain('page_id=page-1')
    expect(FakeWebSocket.instances[0].url).toContain('session_token=token-1')

    ws.connect({ pageId: 'page-1', sessionToken: 'token-1' })
    expect(FakeWebSocket.instances).toHaveLength(1)

    ws.connect({ pageId: 'page-2', sessionToken: 'token-2' })
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(FakeWebSocket.instances[0].closed).toBe(true)
    expect(FakeWebSocket.instances[1].url).toContain('page_id=page-2')
    expect(FakeWebSocket.instances[1].url).toContain('session_token=token-2')

    ws.disconnect()
  })

  it('keeps page scope in the URL for JWT sockets', async () => {
    const ws = await loadWebSocket('jwt-token')

    ws.connect({ pageId: 'page-1' })

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toContain('page_id=page-1')
    expect(FakeWebSocket.instances[0].protocols).toEqual(['obs.jwt.jwt-token'])

    ws.disconnect()
  })
})
