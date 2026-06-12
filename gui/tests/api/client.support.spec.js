import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

let api
let axiosDefault

beforeEach(() => {
  vi.resetModules()
  api = {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  }
  axiosDefault = {
    create: vi.fn(() => api),
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  }
  vi.doMock('axios', () => ({ default: axiosDefault }))
})

afterEach(() => {
  vi.doUnmock('axios')
})

describe('supportApi client', () => {
  it('calls the support diagnostics endpoints', async () => {
    const { supportApi } = await import('@/api/client')

    await supportApi.categories()
    await supportApi.createPackage()
    await supportApi.getDebugStatus()
    await supportApi.enableDebugLog({ duration_seconds: 300, level: 'DEBUG' })
    await supportApi.disableDebugLog()

    expect(api.get).toHaveBeenCalledWith('/support/categories')
    expect(api.post).toHaveBeenCalledWith('/support/package', null, { timeout: 120_000 })
    expect(api.get).toHaveBeenCalledWith('/support/debug-log')
    expect(api.post).toHaveBeenCalledWith('/support/debug-log', { duration_seconds: 300, level: 'DEBUG' })
    expect(api.delete).toHaveBeenCalledWith('/support/debug-log')
  })
})
