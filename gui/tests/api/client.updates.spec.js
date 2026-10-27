/**
 * Update check endpoints in the API client (#1370).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

let api

beforeEach(() => {
  vi.resetModules()
  api = Object.assign(vi.fn().mockResolvedValue({ data: {} }), {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
  })
  vi.doMock('axios', () => ({ default: { create: vi.fn(() => api), get: vi.fn(), post: vi.fn() } }))
})

afterEach(() => {
  vi.doUnmock('axios')
})

describe('updatesApi', () => {
  it('reads the cached status and release notes', async () => {
    const { updatesApi } = await import('@/api/client')

    await updatesApi.status()
    await updatesApi.releaseNotes()

    expect(api.get).toHaveBeenNthCalledWith(1, '/system/update-status')
    expect(api.get).toHaveBeenNthCalledWith(2, '/system/update-status/release-notes')
  })

  it('triggers a manual check and changes the channel', async () => {
    const { updatesApi } = await import('@/api/client')

    await updatesApi.check()
    await updatesApi.setChannel('nightly')

    expect(api.post).toHaveBeenCalledWith('/system/update-status/check')
    expect(api.put).toHaveBeenCalledWith('/system/update-channel', { channel: 'nightly' })
  })
})
