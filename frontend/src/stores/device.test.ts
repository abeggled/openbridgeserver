// @vitest-environment jsdom
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { VisuDevice } from '@/api/client'

const list = vi.fn<() => Promise<VisuDevice[]>>()
vi.mock('@/api/client', () => ({ visuDevices: { list: () => list() } }))

const { useDeviceStore, DEVICE_STORAGE_KEY } = await import('./device')

const DEVICES: VisuDevice[] = [
  { slug: 'panel', name: 'Wandpanel', start_node_id: 'page-1', return_after_s: 90 },
  { slug: 'handy', name: 'Handy', start_node_id: null, return_after_s: 0 },
  { slug: 'kiosk', name: 'Kiosk', start_node_id: 'deleted-page', return_after_s: 60, start_node_missing: true },
]

beforeEach(() => {
  localStorage.clear()
  list.mockReset()
  list.mockResolvedValue(DEVICES)
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useDeviceStore', () => {
  it('starts without a profile and points "start" at the overview', () => {
    const store = useDeviceStore()

    expect(store.slug).toBeNull()
    expect(store.profile).toBeNull()
    expect(store.startRoute).toEqual({ name: 'tree' })
    expect(store.returnAfterMs).toBe(0)
  })

  it('remembers the profile of this device across reloads', async () => {
    localStorage.setItem(DEVICE_STORAGE_KEY, 'panel')
    const store = useDeviceStore()
    await store.load()

    expect(store.profile?.name).toBe('Wandpanel')
    expect(store.startRoute).toEqual({ name: 'viewer', params: { id: 'page-1' } })
    expect(store.returnAfterMs).toBe(90_000)
  })

  it('uses the overview for a profile without a start page', async () => {
    const store = useDeviceStore()
    store.select('handy')
    await store.load()

    expect(store.profile?.slug).toBe('handy')
    expect(store.startRoute).toEqual({ name: 'tree' })
  })

  it('uses the overview when the start page was deleted', async () => {
    const store = useDeviceStore()
    store.select('kiosk')
    await store.load()

    expect(store.profile?.start_node_id).toBe('deleted-page')
    expect(store.startRoute).toEqual({ name: 'tree' })
    expect(store.returnAfterMs).toBe(60_000)
  })

  it('has no profile for a slug the server does not know', async () => {
    const store = useDeviceStore()
    store.select('weg')
    await store.load()

    expect(store.profile).toBeNull()
    expect(store.startRoute).toEqual({ name: 'tree' })
  })

  it('loads the profiles only once', async () => {
    const store = useDeviceStore()
    await Promise.all([store.load(), store.load()])
    await store.load()

    expect(list).toHaveBeenCalledTimes(1)
    expect(store.devices).toEqual(DEVICES)
  })

  it('keeps working without profiles when loading fails, and retries on the next load', async () => {
    list.mockRejectedValueOnce(new Error('offline'))
    const store = useDeviceStore()
    store.select('panel')
    await store.load()
    expect(store.devices).toEqual([])
    expect(store.loaded).toBe(false)
    expect(store.startRoute).toEqual({ name: 'tree' })

    // The failure is not cached: the next load() asks again and the profile applies.
    await store.load()
    expect(list).toHaveBeenCalledTimes(2)
    expect(store.loaded).toBe(true)
    expect(store.startRoute).toEqual({ name: 'viewer', params: { id: 'page-1' } })
  })

  it('keeps the last good list when a later reload fails', async () => {
    const store = useDeviceStore()
    await store.load()
    list.mockRejectedValueOnce(new Error('offline'))

    await store.reload()

    expect(list).toHaveBeenCalledTimes(2)
    expect(store.devices).toEqual(DEVICES)
  })

  it('reload() fetches again even after a successful load', async () => {
    const store = useDeviceStore()
    await store.load()
    list.mockResolvedValueOnce([{ slug: 'panel', name: 'Wandpanel', start_node_id: 'page-9', return_after_s: 5 }])

    await store.reload()

    expect(store.devices[0].start_node_id).toBe('page-9')
  })

  it('persists and clears the selection', () => {
    const store = useDeviceStore()

    store.select('panel')
    expect(localStorage.getItem(DEVICE_STORAGE_KEY)).toBe('panel')

    store.select('')
    expect(store.slug).toBeNull()
    expect(localStorage.getItem(DEVICE_STORAGE_KEY)).toBeNull()
  })

  it('survives a browser without usable storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    const store = useDeviceStore()

    expect(store.slug).toBeNull()
    store.select('panel')
    expect(store.slug).toBe('panel')
  })

  it.each([
    [{ device: 'panel' }, 'panel'],
    [{ device: ['handy', 'panel'] }, 'handy'],
    [{ device: 'none' }, null],
    [{ device: '' }, null],
    [{ device: null }, null],
  ])('takes the profile from the query %j', (query, expected) => {
    const store = useDeviceStore()
    store.select('previous')

    store.applyQuery(query)

    expect(store.slug).toBe(expected)
  })

  it('leaves the profile alone when the query has no device', () => {
    const store = useDeviceStore()
    store.select('panel')

    store.applyQuery({ tab: 'x' })

    expect(store.slug).toBe('panel')
  })
})

describe('useDeviceStore.keepFresh', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  async function flush() {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  }

  it('retries with growing delays until a list arrives, then refreshes on the interval', async () => {
    list.mockRejectedValue(new Error('offline'))
    const store = useDeviceStore()
    await store.load()
    const stop = store.keepFresh(300_000)
    expect(list).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(5_000)   // first retry after 5 s
    expect(list).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(10_000)  // then 10 s
    expect(list).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(20_000)  // then 20 s ...
    expect(list).toHaveBeenCalledTimes(4)
    await vi.advanceTimersByTimeAsync(40_000)
    expect(list).toHaveBeenCalledTimes(5)
    await vi.advanceTimersByTimeAsync(60_000)  // ... capped at 60 s
    expect(list).toHaveBeenCalledTimes(6)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(list).toHaveBeenCalledTimes(7)

    list.mockResolvedValue(DEVICES)
    await vi.advanceTimersByTimeAsync(60_000)  // the network is back: this attempt succeeds
    expect(list).toHaveBeenCalledTimes(8)
    expect(store.loaded).toBe(true)
    await vi.advanceTimersByTimeAsync(60_000)  // from now on every 5 min, not every minute
    expect(list).toHaveBeenCalledTimes(8)
    await vi.advanceTimersByTimeAsync(240_000)
    expect(list).toHaveBeenCalledTimes(9)

    stop()
    await vi.advanceTimersByTimeAsync(600_000)
    expect(list).toHaveBeenCalledTimes(9)
  })

  it('refreshes when the connection comes back or the page becomes visible again', async () => {
    const store = useDeviceStore()
    await store.load()
    const stop = store.keepFresh()
    expect(list).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new Event('online'))
    await flush()
    expect(list).toHaveBeenCalledTimes(2)

    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(list).toHaveBeenCalledTimes(2)  // hidden: nothing to refresh for

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(list).toHaveBeenCalledTimes(3)

    stop()
    window.dispatchEvent(new Event('online'))
    await flush()
    expect(list).toHaveBeenCalledTimes(3)
  })
})
