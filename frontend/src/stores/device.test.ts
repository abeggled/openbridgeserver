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

  it('keeps working without profiles when loading fails', async () => {
    list.mockRejectedValue(new Error('offline'))
    const store = useDeviceStore()
    store.select('panel')
    await store.load()

    expect(store.devices).toEqual([])
    expect(store.startRoute).toEqual({ name: 'tree' })
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
