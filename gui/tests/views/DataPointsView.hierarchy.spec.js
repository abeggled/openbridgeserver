import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let routeLeaveHandlers
let intersectionCallback

beforeEach(() => {
  vi.resetModules()
  routeLeaveHandlers = []
  intersectionCallback = null
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.IntersectionObserver = class {
    constructor(callback) {
      intersectionCallback = callback
    }
    observe() {}
    disconnect() {}
  }
  vi.doMock('vue-router', () => ({
    onBeforeRouteLeave: vi.fn((callback) => {
      routeLeaveHandlers.push(callback)
    }),
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('vue-router')
})

async function mountDataPointsView({ items = [], nodeResults = [], isAdmin = true, pages = 1 } = {}) {
  const searchApi = {
    search: vi.fn().mockResolvedValue({
      data: {
        items,
        total: items.length,
        pages,
      },
    }),
  }
  const dpApi = {
    tags: vi.fn().mockResolvedValue({ data: [] }),
    create: vi.fn().mockImplementation(payload => Promise.resolve({ data: { id: 'dp-created', ...payload } })),
    duplicate: vi.fn().mockImplementation((id, name) => Promise.resolve({
      data: { ...items.find(item => item.id === id), id: 'dp-duplicate', name },
    })),
    update: vi.fn().mockImplementation((id, payload) => Promise.resolve({ data: { id, ...payload } })),
    delete: vi.fn().mockResolvedValue({}),
  }
  const systemApi = {
    datatypes: vi.fn().mockResolvedValue({ data: [{ name: 'FLOAT' }] }),
  }
  const hierarchyApi = {
    searchNodes: vi.fn().mockResolvedValue({ data: nodeResults }),
  }
  vi.doMock('@/api/client', () => ({
    dpApi,
    hierarchyApi,
    searchApi,
    systemApi,
  }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'tester', is_admin: isAdmin }
  const { useWebSocketStore } = await import('@/stores/websocket')
  const websocketStore = useWebSocketStore()
  const subscribeSpy = vi.spyOn(websocketStore, 'subscribe')
  const mod = await import('@/views/DataPointsView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: {
        AdapterCombobox: { template: '<div />' },
        Badge: { template: '<span><slot /></span>' },
        ConfirmDialog: true,
        DataPointForm: true,
        Modal: {
          name: 'Modal',
          props: ['modelValue', 'title', 'dismissible'],
          template: '<div><slot /></div>',
        },
        RouterLink: { props: ['to'], template: '<a><slot /></a>' },
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, dpApi, hierarchyApi, searchApi, subscribeSpy }
}

describe('DataPointsView hierarchy rendering', () => {
  it('hides datapoint CRUD controls for non-admin users', async () => {
    const { wrapper, dpApi } = await mountDataPointsView({
      isAdmin: false,
      items: [
        {
          id: 'dp-readonly',
          name: 'Readonly DP',
          data_type: 'FLOAT',
          tags: [],
          value: 1,
          quality: 'good',
          hierarchy_nodes: [],
        },
      ],
    })

    expect(wrapper.find('[data-testid="btn-new-datapoint"]').exists()).toBe(false)
    expect(wrapper.find('[title="Bearbeiten"]').exists()).toBe(false)
    expect(wrapper.find('[title="Löschen"]').exists()).toBe(false)

    wrapper.vm.openDuplicate(wrapper.vm.store.items[0])
    wrapper.vm.duplicateTarget = wrapper.vm.store.items[0]
    wrapper.vm.duplicateName = 'Forbidden copy'
    await wrapper.vm.doDuplicate()
    expect(wrapper.vm.showDuplicate).toBe(false)
    expect(dpApi.duplicate).not.toHaveBeenCalled()
  })

  it('lets admins create, update and delete datapoints', async () => {
    const item = {
      id: 'dp-admin',
      name: 'Admin DP',
      data_type: 'FLOAT',
      tags: [],
      value: 1,
      quality: 'good',
      hierarchy_nodes: [],
    }
    const { wrapper, dpApi, searchApi, subscribeSpy } = await mountDataPointsView({ items: [item], pages: 2 })

    expect(wrapper.find('[data-testid="btn-new-datapoint"]').exists()).toBe(true)

    wrapper.vm.openCreate()
    expect(wrapper.vm.showForm).toBe(true)
    await wrapper.vm.onSave({ name: 'Created DP', data_type: 'FLOAT', tags: [] })
    expect(dpApi.create).toHaveBeenCalledWith({ name: 'Created DP', data_type: 'FLOAT', tags: [] })
    expect(wrapper.vm.showForm).toBe(false)

    wrapper.vm.openEdit(item)
    expect(wrapper.vm.editTarget.id).toBe('dp-admin')
    await wrapper.vm.onSave({ name: 'Updated DP', data_type: 'FLOAT', tags: [] })
    expect(dpApi.update).toHaveBeenCalledWith('dp-admin', { name: 'Updated DP', data_type: 'FLOAT', tags: [] })

    wrapper.vm.openDuplicate(item)
    expect(wrapper.vm.showDuplicate).toBe(true)
    expect(wrapper.vm.duplicateName).toBe('Kopie von Admin DP')
    expect(wrapper.find('[data-testid="btn-duplicate-dp-admin"]').exists()).toBe(true)
    wrapper.vm.openDuplicate({ ...item, name: 'A'.repeat(255) })
    expect(wrapper.vm.duplicateName).toHaveLength(255)
    expect(wrapper.vm.duplicateName).toBe(`Kopie von ${'A'.repeat(245)}`)
    wrapper.vm.openDuplicate({ ...item, name: '😀'.repeat(255) })
    expect(Array.from(wrapper.vm.duplicateName)).toHaveLength(255)
    expect(wrapper.vm.duplicateName).toBe(`Kopie von ${'😀'.repeat(245)}`)
    wrapper.vm.openDuplicate(item)
    wrapper.vm.filters.q = 'Admin'
    wrapper.vm.store.sortCol = 'name'
    wrapper.vm.store.sortDir = 'desc'
    await wrapper.vm.store.search(wrapper.vm.apiFilters(), false)
    const duplicatedItem = { ...item, id: 'dp-duplicate', name: 'Copied Admin DP' }
    searchApi.search.mockResolvedValueOnce({
      data: { items: [duplicatedItem, item], total: 2, pages: 1 },
    })
    searchApi.search.mockClear()
    subscribeSpy.mockClear()
    wrapper.vm.duplicateName = 'Copied Admin DP'
    await wrapper.vm.doDuplicate()
    await flushPromises()
    expect(dpApi.duplicate).toHaveBeenCalledWith('dp-admin', 'Copied Admin DP')
    expect(searchApi.search).toHaveBeenCalledWith({
      q: 'Admin',
      tag: '',
      adapter: '',
      quality: '',
      type: '',
      node_id: '',
      tree_id: '',
      sort: 'name',
      order: 'desc',
      page: 0,
      size: 50,
    })
    expect(wrapper.vm.store.items.map(dp => dp.id)).toEqual(['dp-duplicate', 'dp-admin'])
    expect(wrapper.vm.store.hasMore).toBe(false)
    expect(subscribeSpy).toHaveBeenCalledWith(['dp-duplicate', 'dp-admin'])
    expect(wrapper.vm.showDuplicate).toBe(false)

    searchApi.search.mockResolvedValueOnce({
      data: { items: [duplicatedItem, item], total: 3, pages: 2 },
    })
    await wrapper.vm.store.search(wrapper.vm.apiFilters(), false)
    let finishStaleLoadMore
    searchApi.search.mockReturnValueOnce(new Promise(resolve => {
      finishStaleLoadMore = resolve
    }))
    const staleLoadMore = wrapper.vm.store.loadMore()
    await flushPromises()
    const freshItem = { ...item, id: 'dp-fresh-copy', name: 'Fresh copy' }
    searchApi.search.mockResolvedValueOnce({
      data: { items: [freshItem, duplicatedItem, item], total: 3, pages: 1 },
    })
    dpApi.duplicate.mockResolvedValueOnce({ data: freshItem })
    wrapper.vm.openDuplicate(item)
    wrapper.vm.duplicateName = freshItem.name
    await wrapper.vm.doDuplicate()
    finishStaleLoadMore({
      data: {
        items: [{ ...item, id: 'dp-stale-page', name: 'Stale page item' }],
        total: 4,
        pages: 2,
      },
    })
    await staleLoadMore
    expect(wrapper.vm.store.items.map(dp => dp.id)).toEqual(['dp-fresh-copy', 'dp-duplicate', 'dp-admin'])
    expect(wrapper.vm.store.total).toBe(3)
    expect(wrapper.vm.store.hasMore).toBe(false)

    searchApi.search.mockResolvedValueOnce({
      data: { items: [duplicatedItem, item], total: 2, pages: 2 },
    })
    await wrapper.vm.store.search(wrapper.vm.apiFilters(), false)
    expect(wrapper.vm.store.hasMore).toBe(true)
    searchApi.search.mockRejectedValueOnce(new Error('Refresh failed'))
    dpApi.duplicate.mockResolvedValueOnce({
      data: { ...item, id: 'dp-refresh-failed', name: 'Copy despite refresh failure' },
    })
    wrapper.vm.openDuplicate(item)
    wrapper.vm.duplicateName = 'Copy despite refresh failure'
    await wrapper.vm.doDuplicate()
    expect(wrapper.vm.showDuplicate).toBe(false)
    expect(wrapper.vm.duplicateError).toBe('')
    expect(wrapper.vm.store.hasMore).toBe(false)
    searchApi.search.mockClear()
    await wrapper.vm.store.loadMore()
    expect(searchApi.search).not.toHaveBeenCalled()

    let rejectOldRefresh
    searchApi.search.mockReturnValueOnce(new Promise((_resolve, reject) => {
      rejectOldRefresh = reject
    }))
    dpApi.duplicate.mockResolvedValueOnce({
      data: { ...item, id: 'dp-old-refresh', name: 'Copy with superseded refresh' },
    })
    wrapper.vm.openDuplicate(item)
    wrapper.vm.duplicateName = 'Copy with superseded refresh'
    const duplicateWithOldRefresh = wrapper.vm.doDuplicate()
    await flushPromises()

    wrapper.vm.filters.q = 'Newer filter'
    searchApi.search.mockResolvedValueOnce({
      data: { items: [item], total: 2, pages: 2 },
    })
    await wrapper.vm.store.search(wrapper.vm.apiFilters(), false)
    rejectOldRefresh(new Error('Superseded refresh failed'))
    await duplicateWithOldRefresh
    expect(wrapper.vm.store.hasMore).toBe(true)

    searchApi.search.mockClear()
    searchApi.search.mockResolvedValueOnce({
      data: { items: [], total: 2, pages: 2 },
    })
    await wrapper.vm.store.loadMore()
    expect(searchApi.search).toHaveBeenCalledWith(expect.objectContaining({
      q: 'Newer filter',
      page: 1,
    }))

    dpApi.duplicate.mockRejectedValueOnce({ response: { data: { detail: 'Copy failed' } } })
    wrapper.vm.openDuplicate(item)
    await wrapper.vm.doDuplicate()
    expect(wrapper.vm.duplicateError).toBe('Copy failed')
    expect(wrapper.vm.duplicateSaving).toBe(false)
    expect(wrapper.vm.showDuplicate).toBe(true)

    dpApi.duplicate.mockRejectedValueOnce(new Error('Copy failed without detail'))
    await wrapper.vm.doDuplicate()
    expect(wrapper.vm.duplicateError).toBe('Das Objekt konnte nicht dupliziert werden.')

    let finishDuplicate
    dpApi.duplicate.mockReturnValueOnce(new Promise(resolve => {
      finishDuplicate = resolve
    }))
    wrapper.vm.openDuplicate(item)
    const pendingDuplicate = wrapper.vm.doDuplicate()
    await flushPromises()
    const duplicateModal = wrapper.findAllComponents({ name: 'Modal' })
      .find(modal => modal.props('title') === 'Objekt duplizieren')
    expect(wrapper.vm.duplicateSaving).toBe(true)
    expect(duplicateModal.props('dismissible')).toBe(false)
    finishDuplicate({ data: { ...item, id: 'dp-pending-copy' } })
    await pendingDuplicate
    expect(wrapper.vm.duplicateSaving).toBe(false)

    wrapper.vm.confirmDelete(item)
    expect(wrapper.vm.deleteTarget.id).toBe('dp-admin')
    expect(wrapper.vm.showConfirm).toBe(true)
    await wrapper.vm.doDelete()
    expect(dpApi.delete).toHaveBeenCalledWith('dp-admin')
  })

  it('applies and clears datapoint list filters', async () => {
    const { wrapper } = await mountDataPointsView()

    wrapper.vm.filters.q = 'temp'
    wrapper.vm.filters.tags = ['hvac']
    wrapper.vm.filters.adapters = ['knx']
    wrapper.vm.filters.quality = 'good'
    wrapper.vm.filters.type = 'FLOAT'
    wrapper.vm.filters.node_ids = [{ node_id: 12, node_name: 'Küche', tree_name: 'Haus' }]
    wrapper.vm.filters.tree_ids = [{ tree_id: 1, tree_name: 'Haus' }]

    expect(wrapper.vm.apiFilters()).toEqual({
      q: 'temp',
      tag: 'hvac',
      adapter: 'knx',
      quality: 'good',
      type: 'FLOAT',
      node_id: '12',
      tree_id: '1',
    })

    wrapper.vm.toggleQuality('bad')
    expect(wrapper.vm.filters.quality).toBe('bad')
    wrapper.vm.toggleQuality('bad')
    expect(wrapper.vm.filters.quality).toBe('')

    wrapper.vm.toggleTag('hvac')
    expect(wrapper.vm.filters.tags).toEqual([])
    wrapper.vm.toggleTag('hvac')
    wrapper.vm.setTagFilter('energy')
    expect(wrapper.vm.filters.tags).toEqual(['hvac', 'energy'])

    wrapper.vm.setAdapterFilter(['mqtt'])
    expect(wrapper.vm.filters.adapters).toEqual(['mqtt'])
    wrapper.vm.setAdapterFilter(null)
    expect(wrapper.vm.filters.adapters).toEqual([])

    wrapper.vm.toggleTreeFilter({ tree_id: 2, tree_name: 'Etage' })
    expect(wrapper.vm.isTreeSelected(2)).toBe(true)
    wrapper.vm.toggleTreeFilter({ tree_id: 2, tree_name: 'Etage' })
    expect(wrapper.vm.isTreeSelected(2)).toBe(false)

    wrapper.vm.toggleNode({ node_id: 44, node_name: 'Bad', tree_name: 'Haus', path: ['EG', 'Bad'], display_depth: 1 })
    expect(wrapper.vm.isNodeSelected(44)).toBe(true)
    wrapper.vm.toggleNode({ node_id: 44, node_name: 'Bad', tree_name: 'Haus', path: ['EG', 'Bad'], display_depth: 1 })
    expect(wrapper.vm.isNodeSelected(44)).toBe(false)

    wrapper.vm.clearFilter('node_ids')
    expect(wrapper.vm.filters.node_ids).toEqual([])
    wrapper.vm.clearFilter('tree_ids')
    expect(wrapper.vm.filters.tree_ids).toEqual([])
    wrapper.vm.clearFilter('tags')
    expect(wrapper.vm.filters.tags).toEqual([])
    wrapper.vm.clearFilter('adapters')
    expect(wrapper.vm.filters.adapters).toEqual([])
    wrapper.vm.clearFilter('type')
    expect(wrapper.vm.filters.type).toBe('')

    wrapper.vm.clearAllFilters()
    expect(wrapper.vm.filters).toEqual({ q: '', tags: [], adapters: [], quality: '', type: '', node_ids: [], tree_ids: [] })
    wrapper.vm.clearHierarchyFilters()
    expect(wrapper.vm.nodeResults).toEqual([])
  })

  it('keeps the full hierarchy path as title on datapoint row chips', async () => {
    const { wrapper } = await mountDataPointsView({
      items: [
        {
          id: 'dp-1',
          name: 'Temperatur Küche',
          data_type: 'FLOAT',
          tags: [],
          value: 21.5,
          quality: 'good',
          hierarchy_nodes: [
            {
              node_id: 12,
              node_name: 'Küche',
              tree_id: 1,
              tree_name: 'Haus',
              display_depth: 2,
              node_path: [{ node_id: 10, node_name: 'Gebäude' }, { node_id: 11, node_name: 'EG' }],
            },
          ],
        },
      ],
    })

    const row = wrapper.find('[data-testid="dp-row-dp-1"]')
    expect(row.exists()).toBe(true)
    const chip = row.find('[title="Haus › Gebäude › EG › Küche"]')
    expect(chip.exists()).toBe(true)
    expect(chip.text()).toContain('EG')
    expect(chip.text()).toContain('Küche')
  })

  it('does not duplicate the tree name in hierarchy filter search results', async () => {
    const { wrapper, hierarchyApi } = await mountDataPointsView({
      nodeResults: [
        {
          node_id: 12,
          node_name: 'Küche',
          tree_name: 'Haus',
          display_depth: 0,
          path: ['Gebäude', 'EG', 'Küche'],
        },
      ],
    })

    await wrapper.find('[data-testid="node-filter"] > button').trigger('click')
    await flushPromises()
    const input = wrapper.find('[data-testid="node-filter"] input')
    await input.setValue('Küche')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()

    expect(hierarchyApi.searchNodes).toHaveBeenCalledWith('Küche', 30)
    const result = wrapper.find('[data-testid="node-filter-result-item"]')
    expect(result.exists()).toBe(true)
    expect((result.text().match(/Haus/g) || []).length).toBe(1)
    expect(result.text()).toContain('Gebäude')
    expect(result.text()).toContain('EG')
    expect(result.text()).toContain('Küche')
  })

  it('uses the node name as selected hierarchy filter label when no path is returned', async () => {
    const { wrapper } = await mountDataPointsView({
      nodeResults: [
        {
          node_id: 12,
          node_name: 'Küche',
          tree_name: 'Haus',
          display_depth: 2,
        },
      ],
    })

    await wrapper.find('[data-testid="node-filter"] > button').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="node-filter"] input').setValue('Küche')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()
    await wrapper.find('[data-testid="node-filter-result-item"]').trigger('click')
    await flushPromises()

    const summary = wrapper.find('[data-testid="node-filter-summary"]')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toContain('Küche')
  })

  it('keeps hidden ancestors available on hierarchy filter search results', async () => {
    const { wrapper } = await mountDataPointsView({
      nodeResults: [
        {
          node_id: 12,
          node_name: 'Küche',
          tree_name: 'Haus',
          display_depth: 2,
          path: ['Gebäude A', 'EG', 'Küche'],
        },
        {
          node_id: 13,
          node_name: 'Küche',
          tree_name: 'Haus',
          display_depth: 2,
          path: ['Gebäude B', 'EG', 'Küche'],
        },
      ],
    })

    await wrapper.find('[data-testid="node-filter"] > button').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="node-filter"] input').setValue('Küche')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()

    const results = wrapper.findAll('[data-testid="node-filter-result-item"]')
    expect(results).toHaveLength(2)
    expect(results.some((item) => item.text().includes('Gebäude A'))).toBe(false)
    expect(results.some((item) => item.text().includes('Gebäude B'))).toBe(false)
    expect(wrapper.find('[title="Haus › Gebäude A › EG › Küche"]').exists()).toBe(true)
    expect(wrapper.find('[title="Haus › Gebäude B › EG › Küche"]').exists()).toBe(true)

    await wrapper.findAll('[data-testid="node-filter-result-item"]')[0].trigger('click')
    await flushPromises()
    await wrapper.findAll('[data-testid="node-filter-result-item"]')[1].trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="node-filter"] input').setValue('')
    await flushPromises()

    const selected = wrapper.findAll('[data-testid="node-filter-selected-item"]')
    expect(selected).toHaveLength(2)
    expect(wrapper.findAll('[title="Haus › Gebäude A › EG › Küche"]').length).toBeGreaterThanOrEqual(1)
    expect(wrapper.findAll('[title="Haus › Gebäude B › EG › Küche"]').length).toBeGreaterThanOrEqual(1)
  })

  it('shows a warning badge when a datapoint has a type mismatch diagnostic', async () => {
    const { wrapper } = await mountDataPointsView({
      items: [
        {
          id: 'dp-mismatch',
          name: 'Deye/Micro/Status',
          data_type: 'FLOAT',
          tags: [],
          value: 'online',
          quality: 'good',
          diagnostics: [
            {
              type: 'type_mismatch',
              expected: 'float',
              got: 'str',
              source_adapter: 'MQTT',
              count: 3,
            },
          ],
        },
      ],
    })

    const badge = wrapper.find('[data-testid="dp-type-mismatch-dp-mismatch"]')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('title')).toContain('float')
    expect(badge.attributes('title')).toContain('str')
  })

  it('renders live values in the regional format, with and without a unit (#1073)', async () => {
    const { wrapper } = await mountDataPointsView({
      items: [
        { id: 'dp-with-unit', name: 'Leistung', data_type: 'FLOAT', tags: [], value: 1234.5, unit: 'W', quality: 'good' },
        { id: 'dp-no-unit', name: 'Zahl', data_type: 'FLOAT', tags: [], value: 1234.5, quality: 'good' },
        { id: 'dp-string', name: 'Text', data_type: 'STRING', tags: [], value: 'online', quality: 'good' },
      ],
    })

    const text = wrapper.text()
    expect(text).toContain('1.234,5 W')
    expect(text).toContain('1.234,5')
    expect(text).toContain('online')
  })

  it('uses fallback text for incomplete type mismatch diagnostics', async () => {
    const { wrapper } = await mountDataPointsView({
      items: [
        {
          id: 'dp-without-diagnostic',
          name: 'Normal',
          data_type: 'FLOAT',
          tags: [],
          value: 21.5,
          quality: 'good',
        },
        {
          id: 'dp-incomplete-diagnostic',
          name: 'Incomplete',
          data_type: 'FLOAT',
          tags: [],
          value: 'online',
          quality: 'good',
          diagnostics: [{ type: 'type_mismatch' }],
        },
      ],
    })

    expect(wrapper.find('[data-testid="dp-type-mismatch-dp-without-diagnostic"]').exists()).toBe(false)
    const badge = wrapper.find('[data-testid="dp-type-mismatch-dp-incomplete-diagnostic"]')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('title')).toContain('—')
    expect(badge.attributes('title')).toContain('1')
  })

  it('persists scroll state before opening datapoint details', async () => {
    const { wrapper } = await mountDataPointsView({
      items: [
        {
          id: 'dp-scroll',
          name: 'Scrollable',
          data_type: 'FLOAT',
          tags: ['hvac'],
          value: 1,
          quality: 'good',
          hierarchy_nodes: [],
        },
      ],
    })
    const scrollSpy = vi.spyOn(window, 'scrollY', 'get').mockReturnValue(321)

    wrapper.vm.filters.q = 'scrollable'
    wrapper.vm.filters.tags = ['hvac']
    routeLeaveHandlers[0]({ name: 'DataPointDetail' })

    const saved = JSON.parse(sessionStorage.getItem('obs.dp.scroll'))
    expect(saved.scrollY).toBe(321)
    expect(saved.filters.q).toBe('scrollable')
    expect(saved.filters.tags).toEqual(['hvac'])

    scrollSpy.mockRestore()
  })

  it('loads another page when the infinite-scroll sentinel intersects', async () => {
    const { searchApi } = await mountDataPointsView({
      pages: 2,
      items: [
        {
          id: 'dp-page-1',
          name: 'Page 1',
          data_type: 'FLOAT',
          tags: [],
          value: 1,
          quality: 'good',
          hierarchy_nodes: [],
        },
      ],
    })

    await intersectionCallback([{ isIntersecting: true }])
    await flushPromises()

    expect(searchApi.search).toHaveBeenCalledWith(expect.objectContaining({ page: 1, size: 50 }))
  })
})
