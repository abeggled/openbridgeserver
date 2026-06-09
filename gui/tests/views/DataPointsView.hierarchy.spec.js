import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.IntersectionObserver = class {
    observe() {}
    disconnect() {}
  }
  vi.doMock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('vue-router')
})

async function mountDataPointsView({ items = [], nodeResults = [] } = {}) {
  const searchApi = {
    search: vi.fn().mockResolvedValue({
      data: {
        items,
        total: items.length,
        pages: 1,
      },
    }),
  }
  const dpApi = {
    tags: vi.fn().mockResolvedValue({ data: [] }),
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
  const mod = await import('@/views/DataPointsView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: {
        AdapterCombobox: { template: '<div />' },
        Badge: { template: '<span><slot /></span>' },
        ConfirmDialog: true,
        DataPointForm: true,
        Modal: { template: '<div><slot /></div>' },
        RouterLink: { props: ['to'], template: '<a><slot /></a>' },
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, hierarchyApi, searchApi }
}

describe('DataPointsView hierarchy rendering', () => {
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
})
