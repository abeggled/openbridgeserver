import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

let adapterApiMock

beforeEach(() => {
  vi.resetModules()
  adapterApiMock = {
    listInstances:  vi.fn(),
    createInstance: vi.fn(),
    updateInstance: vi.fn(),
    deleteInstance: vi.fn(),
    testInstance:   vi.fn(),
    restartInstance: vi.fn(),
    schema: vi.fn(),
    list:   vi.fn(),
  }
  vi.doMock('@/api/client', () => ({ adapterApi: adapterApiMock }))
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

describe('useAdapterStore', () => {
  it('fetchAdapters sets instances and manages loading flag', async () => {
    adapterApiMock.listInstances.mockResolvedValue({ data: [{ id: 1, name: 'KNX' }] })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()

    expect(store.loading).toBe(false)
    const p = store.fetchAdapters()
    expect(store.loading).toBe(true)
    await p
    expect(store.loading).toBe(false)
    expect(store.instances).toEqual([{ id: 1, name: 'KNX' }])
  })

  it('fetchAdapters silent updates status fields of existing instances', async () => {
    adapterApiMock.listInstances.mockResolvedValue({
      data: [{ id: 1, connected: true, severity: 'warning', status_detail: 'overload', status_detail_code: 'c', status_detail_params: { x: 1 } }],
    })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()
    store.instances = [{ id: 1, connected: false, severity: 'ok', status_detail: '', status_detail_code: null, status_detail_params: {} }]

    await store.fetchAdapters({ silent: true })

    expect(store.loading).toBe(false)
    expect(store.instances[0].connected).toBe(true)
    expect(store.instances[0].severity).toBe('warning')
    expect(store.instances[0].status_detail).toBe('overload')
    expect(store.instances[0].status_detail_code).toBe('c')
  })

  it('fetchAdapters silent falls through to replace when instances is empty', async () => {
    adapterApiMock.listInstances.mockResolvedValue({ data: [{ id: 2, name: 'MQTT' }] })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()

    await store.fetchAdapters({ silent: true })

    expect(store.instances).toEqual([{ id: 2, name: 'MQTT' }])
  })

  it('fetchAdapters silent applies defaults for missing severity/detail fields', async () => {
    adapterApiMock.listInstances.mockResolvedValue({
      data: [{ id: 1, connected: true }],
    })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()
    store.instances = [{ id: 1, connected: false, severity: 'error', status_detail: 'old', status_detail_code: 'x', status_detail_params: { a: 1 } }]

    await store.fetchAdapters({ silent: true })

    expect(store.instances[0].severity).toBe('ok')
    expect(store.instances[0].status_detail).toBe('')
    expect(store.instances[0].status_detail_code).toBeNull()
    expect(store.instances[0].status_detail_params).toEqual({})
  })

  it('createInstance pushes new instance and returns it', async () => {
    adapterApiMock.createInstance.mockResolvedValue({ data: { id: 3, adapter_type: 'KNX' } })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()

    const result = await store.createInstance('KNX', 'test', {})

    expect(result).toEqual({ id: 3, adapter_type: 'KNX' })
    expect(store.instances).toContainEqual({ id: 3, adapter_type: 'KNX' })
  })

  it('updateInstance replaces the matching entry by id', async () => {
    adapterApiMock.updateInstance.mockResolvedValue({ data: { id: 1, name: 'updated' } })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()
    store.instances = [{ id: 1, name: 'old' }, { id: 2, name: 'other' }]

    const result = await store.updateInstance(1, { name: 'updated' })

    expect(result).toEqual({ id: 1, name: 'updated' })
    expect(store.instances[0]).toEqual({ id: 1, name: 'updated' })
    expect(store.instances[1]).toEqual({ id: 2, name: 'other' })
  })

  it('updateInstance with unknown id leaves instances unchanged', async () => {
    adapterApiMock.updateInstance.mockResolvedValue({ data: { id: 99, name: 'ghost' } })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()
    store.instances = [{ id: 1, name: 'existing' }]

    await store.updateInstance(99, { name: 'ghost' })

    expect(store.instances).toHaveLength(1)
    expect(store.instances[0].name).toBe('existing')
  })

  it('deleteInstance removes the entry with the given id', async () => {
    adapterApiMock.deleteInstance.mockResolvedValue({})
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()
    store.instances = [{ id: 1 }, { id: 2 }]

    await store.deleteInstance(1)

    expect(store.instances).toEqual([{ id: 2 }])
    expect(adapterApiMock.deleteInstance).toHaveBeenCalledWith(1)
  })

  it('testInstance returns the API response data', async () => {
    adapterApiMock.testInstance.mockResolvedValue({ data: { success: true, detail: 'ok' } })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()

    const result = await store.testInstance(1, { host: '127.0.0.1' })

    expect(result).toEqual({ success: true, detail: 'ok' })
    expect(adapterApiMock.testInstance).toHaveBeenCalledWith(1, { host: '127.0.0.1' })
  })

  it('restartInstance replaces the matching entry and returns it', async () => {
    adapterApiMock.restartInstance.mockResolvedValue({ data: { id: 1, connected: true } })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()
    store.instances = [{ id: 1, connected: false }]

    const result = await store.restartInstance(1)

    expect(result).toEqual({ id: 1, connected: true })
    expect(store.instances[0].connected).toBe(true)
  })

  it('getSchema returns the schema data from the API', async () => {
    adapterApiMock.schema.mockResolvedValue({ data: { type: 'object', properties: {} } })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()

    const result = await store.getSchema('KNX')

    expect(result).toEqual({ type: 'object', properties: {} })
    expect(adapterApiMock.schema).toHaveBeenCalledWith('KNX')
  })

  it('fetchTypes returns only non-hidden adapter types', async () => {
    adapterApiMock.list.mockResolvedValue({
      data: [
        { adapter_type: 'KNX',      hidden: false },
        { adapter_type: 'INTERNAL', hidden: true  },
        { adapter_type: 'MQTT',     hidden: false },
      ],
    })
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()

    const types = await store.fetchTypes()

    expect(types).toEqual(['KNX', 'MQTT'])
  })

  it('adapters is an alias that references the same array as instances', async () => {
    const { useAdapterStore } = await import('@/stores/adapters')
    const store = useAdapterStore()

    expect(store.adapters).toBe(store.instances)
  })
})
