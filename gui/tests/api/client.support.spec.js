import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

let api
let axiosDefault

beforeEach(() => {
  vi.resetModules()
  api = Object.assign(vi.fn().mockResolvedValue({ data: {} }), {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  })
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

describe('dpApi client', () => {
  it('calls the datapoint duplication endpoint with the requested name', async () => {
    const { dpApi } = await import('@/api/client')

    await dpApi.duplicate('dp-1', 'Copy')

    expect(api.post).toHaveBeenCalledWith('/datapoints/dp-1/duplicate', { name: 'Copy' }, { timeout: 0 })
  })
})

describe('logicApi client', () => {
  it('passes optional debug input overrides to graph runs', async () => {
    const { logicApi } = await import('@/api/client')
    const payload = { input_overrides: { node: { value: 42 } } }

    await logicApi.runGraph('graph-1', payload)

    expect(api.post).toHaveBeenCalledWith('/logic/graphs/graph-1/run', payload)
  })
})

describe('authentication refresh', () => {
  it('notifies WebSocket consumers after storing refreshed tokens', async () => {
    const listener = vi.fn()
    const { AUTH_TOKEN_REFRESHED_EVENT } = await import('@/utils/authEvents')
    window.addEventListener(AUTH_TOKEN_REFRESHED_EVENT, listener)
    localStorage.setItem('refresh_token', 'old-refresh')
    axiosDefault.post.mockResolvedValueOnce({
      data: { access_token: 'fresh-access', refresh_token: 'fresh-refresh' },
    })
    await import('@/api/client')
    const rejectResponse = api.interceptors.response.use.mock.calls[0][1]

    await rejectResponse({
      config: { headers: {} },
      response: { status: 401 },
    })

    expect(localStorage.getItem('access_token')).toBe('fresh-access')
    expect(localStorage.getItem('refresh_token')).toBe('fresh-refresh')
    expect(listener).toHaveBeenCalledOnce()
    window.removeEventListener(AUTH_TOKEN_REFRESHED_EVENT, listener)
  })
})

describe('adapterApi onewire client', () => {
  it('calls the onewire browse and alias endpoints', async () => {
    const { adapterApi } = await import('@/api/client')

    await adapterApi.onewireBrowseSensors('ow-1')
    await adapterApi.onewireSetAlias('ow-1', '28.4B057F0A1C10', 'Gästebad')

    expect(api.get).toHaveBeenCalledWith('/adapters/instances/ow-1/onewire/browse', { timeout: 60_000 })
    expect(api.patch).toHaveBeenCalledWith('/adapters/instances/ow-1/onewire/aliases', {
      rom_id: '28.4B057F0A1C10',
      label: 'Gästebad',
    })
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

describe('settingsApi client (#1073)', () => {
  it('reads and writes app settings including the regional format', async () => {
    const { settingsApi } = await import('@/api/client')

    await settingsApi.get()
    await settingsApi.update({ region_format: 'de-CH', currency: 'CHF' })
    await settingsApi.displaySettings()

    expect(api.get).toHaveBeenCalledWith('/system/settings')
    expect(api.put).toHaveBeenCalledWith('/system/settings', { region_format: 'de-CH', currency: 'CHF' })
    expect(api.get).toHaveBeenCalledWith('/system/display-settings')
  })
})

describe('helpApi client (#896)', () => {
  it('fetches help-index.json via the raw axios instance, bypassing /api/v1 and its JWT interceptor', async () => {
    const { helpApi } = await import('@/api/client')

    await helpApi.index()

    expect(axiosDefault.get).toHaveBeenCalledWith('/help/help-index.json')
    expect(api.get).not.toHaveBeenCalledWith('/help/help-index.json')
  })
})
