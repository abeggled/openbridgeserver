import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let api

beforeEach(() => {
  vi.resetModules()
  api = {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  }
  vi.doMock('axios', () => ({
    default: {
      create: vi.fn(() => api),
      get: vi.fn(),
      post: vi.fn(),
    },
  }))
})

afterEach(() => {
  vi.doUnmock('axios')
})

describe('authzApi client', () => {
  it('calls encoded user grant and preview endpoints with the frozen contract', async () => {
    const { authzApi } = await import('@/api/client')
    const grants = [{ node_type: 'hierarchy', node_id: 'room', role: 'guest', effect: 'allow' }]
    const preview = { principal: { principal_type: 'user', principal_id: 'alice/name' }, draft_grants: [] }

    await authzApi.getUserGrants('alice/name')
    await authzApi.updateUserGrants('alice/name', grants)
    await authzApi.preview(preview)

    expect(api.get).toHaveBeenCalledWith('/authz/principals/user/alice%2Fname/grants')
    expect(api.put).toHaveBeenCalledWith('/authz/principals/user/alice%2Fname/grants', { grants })
    expect(api.post).toHaveBeenCalledWith('/authz/preview', preview)
  })
})
