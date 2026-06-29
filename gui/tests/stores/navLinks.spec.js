import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

let navLinksApiMock

beforeEach(() => {
  vi.resetModules()
  navLinksApiMock = {
    list:   vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  }
  vi.doMock('@/api/client', () => ({ navLinksApi: navLinksApiMock }))
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

describe('useNavLinksStore', () => {
  it('load fetches links and manages loading flag', async () => {
    navLinksApiMock.list.mockResolvedValue({ data: [{ id: 1, label: 'Home', sort_order: 1 }] })
    const { useNavLinksStore } = await import('@/stores/navLinks')
    const store = useNavLinksStore()

    expect(store.loading).toBe(false)
    const p = store.load()
    expect(store.loading).toBe(true)
    await p
    expect(store.loading).toBe(false)
    expect(store.links).toEqual([{ id: 1, label: 'Home', sort_order: 1 }])
  })

  it('load resets loading to false on API error (non-critical swallowed)', async () => {
    navLinksApiMock.list.mockRejectedValue(new Error('Network error'))
    const { useNavLinksStore } = await import('@/stores/navLinks')
    const store = useNavLinksStore()

    await store.load()

    expect(store.loading).toBe(false)
    expect(store.links).toEqual([])
  })

  it('create appends new link and sorts by sort_order', async () => {
    navLinksApiMock.create.mockResolvedValue({ data: { id: 2, label: 'Admin', sort_order: 0 } })
    const { useNavLinksStore } = await import('@/stores/navLinks')
    const store = useNavLinksStore()
    store.links = [{ id: 1, label: 'Home', sort_order: 1 }]

    const result = await store.create({ label: 'Admin', sort_order: 0 })

    expect(result).toEqual({ id: 2, label: 'Admin', sort_order: 0 })
    expect(store.links[0]).toEqual({ id: 2, label: 'Admin', sort_order: 0 })
    expect(store.links[1]).toEqual({ id: 1, label: 'Home', sort_order: 1 })
  })

  it('update replaces the matching link and re-sorts', async () => {
    navLinksApiMock.update.mockResolvedValue({ data: { id: 1, label: 'Updated', sort_order: 5 } })
    const { useNavLinksStore } = await import('@/stores/navLinks')
    const store = useNavLinksStore()
    store.links = [
      { id: 1, label: 'Old',   sort_order: 1 },
      { id: 2, label: 'Other', sort_order: 2 },
    ]

    const result = await store.update(1, { label: 'Updated', sort_order: 5 })

    expect(result).toEqual({ id: 1, label: 'Updated', sort_order: 5 })
    expect(store.links[0]).toEqual({ id: 2, label: 'Other', sort_order: 2 })
    expect(store.links[1]).toEqual({ id: 1, label: 'Updated', sort_order: 5 })
  })

  it('remove filters out the link with the given id', async () => {
    navLinksApiMock.delete.mockResolvedValue({})
    const { useNavLinksStore } = await import('@/stores/navLinks')
    const store = useNavLinksStore()
    store.links = [{ id: 1 }, { id: 2 }]

    await store.remove(1)

    expect(store.links).toEqual([{ id: 2 }])
    expect(navLinksApiMock.delete).toHaveBeenCalledWith(1)
  })

  it('links starts as empty array', async () => {
    const { useNavLinksStore } = await import('@/stores/navLinks')
    const store = useNavLinksStore()
    expect(store.links).toEqual([])
  })
})
