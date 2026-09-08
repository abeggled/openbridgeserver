/**
 * First-run setup in the API client (#1229).
 *
 * The setup endpoints run before any token exists, and an installation that
 * lost its owner answers 503 on everything else — the client must send the
 * user to the setup page instead of the login it cannot reach.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

let api
let axiosDefault
let onError

beforeEach(() => {
  vi.resetModules()
  onError = null
  api = Object.assign(vi.fn().mockResolvedValue({ data: {} }), {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn((_ok, err) => { onError = err }) },
    },
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  })
  axiosDefault = {
    create: vi.fn(() => api),
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  }
  vi.doMock('axios', () => ({ default: axiosDefault }))
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: { pathname: '/', href: '/' },
  })
})

afterEach(() => {
  vi.doUnmock('axios')
})

describe('setupApi', () => {
  it('talks to the setup endpoints without the authenticated instance', async () => {
    const { setupApi } = await import('@/api/client')

    await setupApi.status()
    await setupApi.createOwner('owner', 'a-good-password')

    expect(axiosDefault.get).toHaveBeenCalledWith('/api/v1/setup/status')
    expect(axiosDefault.post).toHaveBeenCalledWith('/api/v1/setup/owner', {
      username: 'owner',
      password: 'a-good-password',
    })
    expect(api.get).not.toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()
  })
})

describe('response interceptor — installation awaiting setup', () => {
  it('sends the user to the setup page', async () => {
    await import('@/api/client')

    await expect(onError({ config: {}, response: { status: 503, data: { setup_required: true } } })).rejects.toBeTruthy()

    expect(window.location.href).toBe('/setup')
  })

  it('does not navigate when already on the setup page', async () => {
    window.location.pathname = '/setup'
    await import('@/api/client')

    await expect(onError({ config: {}, response: { status: 503, data: { setup_required: true } } })).rejects.toBeTruthy()

    expect(window.location.href).toBe('/')
  })

  it('leaves an ordinary 503 alone', async () => {
    await import('@/api/client')

    await expect(onError({ config: {}, response: { status: 503, data: {} } })).rejects.toBeTruthy()

    expect(window.location.href).toBe('/')
  })
})
