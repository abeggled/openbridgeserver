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

  it('reads the release notes again after a check or channel switch', async () => {
    let finishCheck
    updatesApiMock.check.mockReturnValueOnce(new Promise(resolve => { finishCheck = resolve }))
    updatesApiMock.setChannel.mockResolvedValueOnce({ data: { channel: 'nightly' } })
    updatesApiMock.releaseNotes
      .mockResolvedValueOnce({ data: [{ version: '2026.10.0' }] })
      .mockResolvedValueOnce({ data: [{ version: '2026.10.0' }, { version: '2026.11.0' }] })
      .mockResolvedValueOnce({ data: [{ version: 'nightly-20261019' }] })
    const store = await useStore()

    // Notes opened while the check is still running answer the old result.
    const check = store.checkNow()
    await store.fetchReleaseNotes()
    expect(store.releaseNotes).toEqual([{ version: '2026.10.0' }])
    finishCheck({ data: { latest_version: '2026.11.0', release_notes_count: 2 } })
    await check
    await vi.waitFor(() => expect(store.releaseNotes).toHaveLength(2))

    await store.setChannel('nightly')
    await vi.waitFor(() => expect(store.releaseNotes).toEqual([{ version: 'nightly-20261019' }]))
    expect(updatesApiMock.releaseNotes).toHaveBeenCalledTimes(3)
  })

  it('does not load release notes nobody asked for', async () => {
    updatesApiMock.check.mockResolvedValueOnce({ data: { latest_version: '2026.11.0' } })
    const store = await useStore()
    await store.checkNow()
    expect(updatesApiMock.releaseNotes).not.toHaveBeenCalled()
  })

  it('drops a stale mutation answer and reads the final state back', async () => {
    let finishCheck
    updatesApiMock.check.mockReturnValueOnce(new Promise(resolve => { finishCheck = resolve }))
    updatesApiMock.setChannel.mockResolvedValueOnce({ data: { channel: 'nightly' } })
    updatesApiMock.status.mockResolvedValue({ data: { channel: 'nightly', latest_version: 'nightly-20261018' } })
    const store = await useStore()

    // Check started first, channel change answered first, check answers last.
    const check = store.checkNow()
    await store.setChannel('nightly')
    expect(store.status).toEqual({ channel: 'nightly' })
    expect(updatesApiMock.status).not.toHaveBeenCalled()
    finishCheck({ data: { channel: 'stable' } })
    await expect(check).resolves.toEqual({ channel: 'stable' })
    expect(store.checking).toBe(false)
    await vi.waitFor(() => expect(store.status).toEqual({ channel: 'nightly', latest_version: 'nightly-20261018' }))
    expect(updatesApiMock.status).toHaveBeenCalledTimes(1)
  })

  it('reads the final state back when the newer mutation fails', async () => {
    let finishCheck
    let failChannel
    updatesApiMock.check.mockReturnValueOnce(new Promise(resolve => { finishCheck = resolve }))
    updatesApiMock.setChannel.mockReturnValueOnce(new Promise((_, reject) => { failChannel = reject }))
    updatesApiMock.status.mockResolvedValue({ data: { latest_version: '2026.11.0' } })
    const store = await useStore()

    const check = store.checkNow()
    const channel = store.setChannel('nightly')
    finishCheck({ data: { latest_version: '2026.11.0', channel: 'stable' } })
    await check
    expect(store.status).toEqual({ latest_version: '2026.11.0', channel: 'stable' })
    failChannel(new Error('network'))
    await expect(channel).rejects.toThrow('network')
    await vi.waitFor(() => expect(store.status).toEqual({ latest_version: '2026.11.0' }))
    expect(updatesApiMock.status).toHaveBeenCalledTimes(1)
  })

  it('keeps the newest of overlapping status reads', async () => {
    const pending = []
    updatesApiMock.status.mockImplementation(() => new Promise((resolve, reject) => pending.push({ resolve, reject })))
    const store = await useStore()

    // An older read answering after a newer one is dropped, error or not.
    const older = store.fetchStatus()
    const olderFailing = store.fetchStatus()
    const newer = store.fetchStatus()
    pending[2].resolve({ data: { latest_version: '2026.11.0' } })
    await newer
    pending[0].resolve({ data: { latest_version: '2026.10.0' } })
    pending[1].reject(new Error('down'))
    await older
    await olderFailing
    expect(store.status).toEqual({ latest_version: '2026.11.0' })
    expect(store.statusError).toBe(false)

    // A newer read failing first still lets an older answer through.
    const before = store.fetchStatus()
    const failing = store.fetchStatus()
    pending[4].reject(new Error('down'))
    await failing
    expect(store.statusError).toBe(true)
    pending[3].resolve({ data: { latest_version: '2026.12.0' } })
    await before
    expect(store.status).toEqual({ latest_version: '2026.12.0' })
    expect(store.statusError).toBe(false)
  })

  it('drops a read that answered while a mutation was still running', async () => {
    let finishChannel
    updatesApiMock.setChannel.mockReturnValueOnce(new Promise(resolve => { finishChannel = resolve }))
    updatesApiMock.status.mockResolvedValueOnce({ data: { channel: 'stable' } })
    const store = await useStore()

    const channel = store.setChannel('nightly')
    await store.fetchStatus()
    expect(store.status).toBeNull()
    finishChannel({ data: { channel: 'nightly' } })
    await channel
    expect(store.status).toEqual({ channel: 'nightly' })
    expect(updatesApiMock.status).toHaveBeenCalledTimes(1)
  })
})
