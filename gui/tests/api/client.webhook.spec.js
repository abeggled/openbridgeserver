import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

let api

beforeEach(() => {
  vi.resetModules()
  api = Object.assign(vi.fn().mockResolvedValue({ data: {} }), {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  })
  vi.doMock('axios', () => ({ default: { create: vi.fn(() => api), get: vi.fn(), post: vi.fn() } }))
})

afterEach(() => {
  vi.doUnmock('axios')
})

describe('adapterApi webhook routes', () => {
  it('reads the webhook bindings of one instance', async () => {
    const { adapterApi } = await import('@/api/client')

    await adapterApi.webhookBindings('hook-1')

    expect(api.get).toHaveBeenCalledWith('/adapters/instances/hook-1/webhook/bindings')
  })

  it('rotates the token of one webhook binding', async () => {
    const { adapterApi } = await import('@/api/client')

    await adapterApi.webhookRotateToken('hook-1', 'binding-1')

    expect(api.post).toHaveBeenCalledWith('/adapters/instances/hook-1/webhook/bindings/binding-1/rotate-token')
  })
})
