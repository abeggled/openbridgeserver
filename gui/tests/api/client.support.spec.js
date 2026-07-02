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
    patch: vi.fn().mockResolvedValue({ data: {} }),
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

describe('messageArchivesApi client', () => {
  it('calls message archive endpoints including DB import/export', async () => {
    const { messageArchivesApi } = await import('@/api/client')
    const file = new File(['sqlite'], 'messages.sqlite3')

    await messageArchivesApi.list()
    await messageArchivesApi.create({ id: 'system', name: 'System' })
    await messageArchivesApi.update('system', { name: 'System' })
    await messageArchivesApi.delete('system', true)
    await messageArchivesApi.clear('system', true)
    await messageArchivesApi.integrityCheck()
    await messageArchivesApi.entries({ archive_id: 'system' })
    await messageArchivesApi.export('system', 'csv')
    await messageArchivesApi.exportDb()
    await messageArchivesApi.importDb(file)

    expect(api.get).toHaveBeenCalledWith('/message-archives')
    expect(api.post).toHaveBeenCalledWith('/message-archives', { id: 'system', name: 'System' })
    expect(api.patch).toHaveBeenCalledWith('/message-archives/system', { name: 'System' })
    expect(api.delete).toHaveBeenCalledWith('/message-archives/system', { params: { confirm: true } })
    expect(api.post).toHaveBeenCalledWith('/message-archives/system/clear', null, { params: { confirm: true } })
    expect(api.post).toHaveBeenCalledWith('/message-archives/integrity-check')
    expect(api.get).toHaveBeenCalledWith('/message-archives/entries', { params: { archive_id: 'system' } })
    expect(api.get).toHaveBeenCalledWith('/message-archives/system/export', { params: { format: 'csv' }, responseType: 'blob' })
    expect(api.get).toHaveBeenCalledWith('/message-archives/export/db', { responseType: 'blob' })
    expect(api.post).toHaveBeenCalledWith(
      '/message-archives/import/db',
      expect.any(FormData),
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
  })
})
