import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let api

beforeEach(() => {
  vi.resetModules()
  api = {
    get: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  }
  vi.doMock('@/api/client', () => ({ default: api }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

describe('accountAdminApi', () => {
  it('binds lifecycle and capability calls to encoded targets and exact payloads', async () => {
    const { accountAdminApi } = await import('@/api/accountAdmin')
    const deletion = { revision: 'delete-v1', successor_username: 'owner' }
    const capabilities = ['visu.page_config.write']

    await accountAdminApi.userDeletionPreflight('alice/name')
    await accountAdminApi.deleteUser('alice/name', deletion)
    await accountAdminApi.getApiKeyCapabilities('key/id')
    await accountAdminApi.replaceApiKeyCapabilities('key/id', 3, capabilities)

    expect(api.get).toHaveBeenNthCalledWith(1, '/auth/users/alice%2Fname/deletion-preflight')
    expect(api.delete).toHaveBeenCalledWith('/auth/users/alice%2Fname', { data: deletion })
    expect(api.get).toHaveBeenNthCalledWith(2, '/auth/apikeys/key%2Fid/capabilities')
    expect(api.put).toHaveBeenCalledWith(
      '/auth/apikeys/key%2Fid/capabilities',
      { expected_revision: 3, capabilities },
    )
  })
})
