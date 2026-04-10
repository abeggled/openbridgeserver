/**
 * Unit tests for iconsApi (gui/src/api/client.js)
 *
 * Axios wird vollständig gemockt — kein laufender Server nötig.
 * Geprüft wird: korrekter HTTP-Method, URL und Übergabe von Parametern.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ── Axios mock ────────────────────────────────────────────────────────────
// Muss VOR dem Import von client.js definiert werden (Hoisting).
const mockApi = {
  get:    vi.fn(),
  post:   vi.fn(),
  delete: vi.fn(),
  interceptors: {
    request:  { use: vi.fn() },
    response: { use: vi.fn() },
  },
}

vi.mock('axios', () => ({
  default: {
    create: () => mockApi,
    post:   vi.fn(),
    get:    vi.fn(),
  },
}))

// Import NACH dem Mock
const { iconsApi } = await import('@/api/client.js')

// ── Helpers ───────────────────────────────────────────────────────────────
const RESOLVED = (data) => Promise.resolve({ data })

beforeEach(() => {
  vi.clearAllMocks()
})

// ── Tests ─────────────────────────────────────────────────────────────────

describe('iconsApi.list', () => {
  it('sendet GET /icons/', async () => {
    mockApi.get.mockReturnValueOnce(RESOLVED({ total: 0, icons: [] }))
    await iconsApi.list()
    expect(mockApi.get).toHaveBeenCalledWith('/icons/')
  })
})

describe('iconsApi.import', () => {
  it('sendet POST /icons/import mit multipart/form-data Header', async () => {
    mockApi.post.mockReturnValueOnce(RESOLVED({ imported: 1, skipped: 0, names: ['home'] }))
    const fd = new FormData()
    await iconsApi.import(fd)
    expect(mockApi.post).toHaveBeenCalledWith(
      '/icons/import',
      fd,
      expect.objectContaining({ headers: { 'Content-Type': 'multipart/form-data' } }),
    )
  })
})

describe('iconsApi.get', () => {
  it('sendet GET /icons/{name}', async () => {
    mockApi.get.mockReturnValueOnce(RESOLVED('<svg/>'))
    await iconsApi.get('home')
    expect(mockApi.get).toHaveBeenCalledWith(
      '/icons/home',
      expect.objectContaining({ responseType: 'text' }),
    )
  })
})

describe('iconsApi.delete', () => {
  it('sendet DELETE /icons/ mit names im Body', async () => {
    mockApi.delete.mockReturnValueOnce(RESOLVED({ deleted: 1 }))
    await iconsApi.delete(['home', 'star'])
    expect(mockApi.delete).toHaveBeenCalledWith(
      '/icons/',
      { data: { names: ['home', 'star'] } },
    )
  })

  it('sendet leere names-Liste korrekt', async () => {
    mockApi.delete.mockReturnValueOnce(RESOLVED({ deleted: 0 }))
    await iconsApi.delete([])
    expect(mockApi.delete).toHaveBeenCalledWith(
      '/icons/',
      { data: { names: [] } },
    )
  })
})

describe('iconsApi.export', () => {
  it('sendet POST /icons/export mit names im JSON-Body', async () => {
    mockApi.post.mockReturnValueOnce(RESOLVED(new Blob()))
    await iconsApi.export(['home', 'star'])
    expect(mockApi.post).toHaveBeenCalledWith(
      '/icons/export',
      { names: ['home', 'star'] },
      expect.objectContaining({ responseType: 'blob' }),
    )
  })

  it('sendet leere names-Liste für Export aller Icons', async () => {
    mockApi.post.mockReturnValueOnce(RESOLVED(new Blob()))
    await iconsApi.export([])
    expect(mockApi.post).toHaveBeenCalledWith(
      '/icons/export',
      { names: [] },
      expect.objectContaining({ responseType: 'blob' }),
    )
  })

  it('sendet POST auch ohne Argument (default = alle)', async () => {
    mockApi.post.mockReturnValueOnce(RESOLVED(new Blob()))
    await iconsApi.export()
    expect(mockApi.post).toHaveBeenCalledWith(
      '/icons/export',
      { names: [] },
      expect.objectContaining({ responseType: 'blob' }),
    )
  })
})

describe('iconsApi.importFa', () => {
  it('sendet POST /icons/fontawesome mit payload', async () => {
    mockApi.post.mockReturnValueOnce(RESOLVED({ imported: 1, skipped: 0, names: ['home'] }))
    const payload = { icons: ['home'], style: 'solid' }
    await iconsApi.importFa(payload)
    expect(mockApi.post).toHaveBeenCalledWith('/icons/fontawesome', payload)
  })

  it('sendet api_key wenn angegeben', async () => {
    mockApi.post.mockReturnValueOnce(RESOLVED({ imported: 1, skipped: 0, names: ['star'] }))
    const payload = { icons: ['star'], style: 'solid', api_key: 'my-pro-key' }
    await iconsApi.importFa(payload)
    expect(mockApi.post).toHaveBeenCalledWith(
      '/icons/fontawesome',
      expect.objectContaining({ api_key: 'my-pro-key' }),
    )
  })
})
