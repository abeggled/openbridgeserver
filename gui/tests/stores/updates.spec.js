import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

let updatesApiMock

beforeEach(() => {
  vi.resetModules()
  updatesApiMock = {
    status:       vi.fn(),
    releaseNotes: vi.fn(),
    check:        vi.fn(),
    setChannel:   vi.fn(),
  }
  vi.doMock('@/api/client', () => ({ updatesApi: updatesApiMock }))
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function useStore() {
  const { useUpdatesStore } = await import('@/stores/updates')
  return useUpdatesStore()
}

describe('useUpdatesStore', () => {
  it('fetchStatus stores the status and derives updateAvailable', async () => {
    updatesApiMock.status.mockResolvedValue({ data: { update_available: true } })
    const store = await useStore()
    expect(store.updateAvailable).toBe(false)
    await expect(store.fetchStatus()).resolves.toEqual({ update_available: true })
    expect(store.updateAvailable).toBe(true)
  })

  it('updateAvailable is only true for an explicit true', async () => {
    updatesApiMock.status.mockResolvedValue({ data: { update_available: null } })
    const store = await useStore()
    await store.fetchStatus()
    expect(store.updateAvailable).toBe(false)
  })

  it('fetchStatus keeps the previous status on errors', async () => {
    updatesApiMock.status.mockResolvedValueOnce({ data: { update_available: false } }).mockRejectedValueOnce(new Error('down'))
    const store = await useStore()
    await store.fetchStatus()
    expect(store.statusError).toBe(false)
    await expect(store.fetchStatus()).resolves.toEqual({ update_available: false })
    expect(store.statusError).toBe(true)
  })

  it('fetchReleaseNotes loads notes and tracks loading', async () => {
    updatesApiMock.releaseNotes.mockResolvedValue({ data: [{ version: '2026.10.0' }] })
    const store = await useStore()
    const pending = store.fetchReleaseNotes()
    expect(store.notesLoading).toBe(true)
    await pending
    expect(store.notesLoading).toBe(false)
    expect(store.notesError).toBe(false)
    expect(store.releaseNotes).toEqual([{ version: '2026.10.0' }])
  })

  it('fetchReleaseNotes flags errors', async () => {
    updatesApiMock.releaseNotes.mockRejectedValue(new Error('down'))
    const store = await useStore()
    await expect(store.fetchReleaseNotes()).resolves.toEqual([])
    expect(store.notesError).toBe(true)
    expect(store.notesLoading).toBe(false)
  })

  it('checkNow updates the status and resets the flag even on errors', async () => {
    updatesApiMock.check.mockResolvedValueOnce({ data: { latest_version: '2026.10.0' } }).mockRejectedValueOnce(new Error('409'))
    const store = await useStore()
    const pending = store.checkNow()
    expect(store.checking).toBe(true)
    await pending
    expect(store.status).toEqual({ latest_version: '2026.10.0' })
    await expect(store.checkNow()).rejects.toThrow('409')
    expect(store.checking).toBe(false)
  })

  it('requestUpdatesTab counts requests', async () => {
    const store = await useStore()
    store.requestUpdatesTab()
    store.requestUpdatesTab()
    expect(store.updatesTabRequests).toBe(2)
  })

  it('setChannel saves and resets the flag even on errors', async () => {
    updatesApiMock.setChannel.mockResolvedValueOnce({ data: { channel: 'nightly' } }).mockRejectedValueOnce(new Error('403'))
    const store = await useStore()
    const pending = store.setChannel('nightly')
    expect(store.saving).toBe(true)
    await pending
    expect(updatesApiMock.setChannel).toHaveBeenCalledWith('nightly')
    expect(store.status).toEqual({ channel: 'nightly' })
    await expect(store.setChannel('stable')).rejects.toThrow('403')
    expect(store.saving).toBe(false)
  })

  it('ignores a status read that overlapped a channel change or check', async () => {
    let resolveRead
    let rejectRead
    updatesApiMock.status
      .mockReturnValueOnce(new Promise(resolve => { resolveRead = resolve }))
      .mockReturnValueOnce(new Promise((_, reject) => { rejectRead = reject }))
    updatesApiMock.setChannel.mockResolvedValue({ data: { channel: 'nightly' } })
    updatesApiMock.check.mockResolvedValue({ data: { channel: 'nightly', latest_version: 'nightly-20261018' } })
    const store = await useStore()

    const staleRead = store.fetchStatus()
    await store.setChannel('nightly')
    resolveRead({ data: { channel: 'stable' } })
    await expect(staleRead).resolves.toEqual({ channel: 'nightly' })
    expect(store.status).toEqual({ channel: 'nightly' })

    const failedRead = store.fetchStatus()
    await store.checkNow()
    rejectRead(new Error('down'))
    await failedRead
    expect(store.statusError).toBe(false)
    expect(store.status.latest_version).toBe('nightly-20261018')
  })

  it('fetchReleaseNotes keeps only the newest response', async () => {
    let resolveOld
    let rejectOld
    updatesApiMock.releaseNotes
      .mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce({ data: [{ version: 'nightly-20261018' }] })
      .mockReturnValueOnce(new Promise((_, reject) => { rejectOld = reject }))
      .mockResolvedValueOnce({ data: [{ version: '2026.10.0' }] })
    const store = await useStore()

    const older = store.fetchReleaseNotes()
    await store.fetchReleaseNotes()
    resolveOld({ data: [{ version: '2026.9.0' }] })
    await older
    expect(store.releaseNotes).toEqual([{ version: 'nightly-20261018' }])

    const failing = store.fetchReleaseNotes()
    await store.fetchReleaseNotes()
    rejectOld(new Error('down'))
    await failing
    expect(store.releaseNotes).toEqual([{ version: '2026.10.0' }])
    expect(store.notesError).toBe(false)
    expect(store.notesLoading).toBe(false)
  })
})
