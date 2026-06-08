import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
})

afterEach(() => {
  vi.doUnmock('@/api/client.js')
})

async function mountCard({ linked = [], results = [] } = {}) {
  const hierarchyApi = {
    getDatapointNodes: vi.fn().mockResolvedValue({ data: linked }),
    searchNodes: vi.fn().mockResolvedValue({ data: results }),
    createLink: vi.fn().mockResolvedValue({}),
    deleteLink: vi.fn().mockResolvedValue({}),
  }
  vi.doMock('@/api/client.js', () => ({ hierarchyApi }))
  const mod = await import('@/components/datapoints/DataPointHierarchyCard.vue')
  const wrapper = mount(mod.default, {
    props: { dpId: 'dp-1' },
    attachTo: document.body,
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, hierarchyApi }
}

describe('DataPointHierarchyCard', () => {
  it('keeps the full hierarchy path as title on linked-node chips', async () => {
    const { wrapper } = await mountCard({
      linked: [
        {
          link_id: 1,
          node_id: 12,
          tree_name: 'Haus',
          node_name: 'Küche',
          display_depth: 2,
          node_path: [{ node_name: 'Gebäude' }, { node_name: 'EG' }],
        },
      ],
    })

    const chip = wrapper.find('[title="Haus › Gebäude › EG › Küche"]')
    expect(chip.exists()).toBe(true)
    expect(chip.text()).toContain('EG')
    expect(chip.text()).toContain('Küche')
  })

  it('does not duplicate the tree name in search results when it is already in the display path', async () => {
    const { wrapper, hierarchyApi } = await mountCard({
      results: [
        {
          node_id: 12,
          tree_name: 'Haus',
          node_name: 'Küche',
          display_depth: 0,
          path: ['Gebäude', 'EG', 'Küche'],
        },
      ],
    })

    await wrapper.find('input').setValue('Küche')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()

    expect(hierarchyApi.searchNodes).toHaveBeenCalledWith('Küche', 40)
    const result = wrapper.find('button:not([disabled])')
    expect(result.exists()).toBe(true)
    expect((result.text().match(/Haus/g) || []).length).toBe(1)
    expect(result.text()).toContain('Gebäude')
    expect(result.text()).toContain('EG')
    expect(result.text()).toContain('Küche')
  })

  it('keeps full path context available on assignment search results', async () => {
    const { wrapper } = await mountCard({
      results: [
        {
          node_id: 12,
          tree_name: 'Haus',
          node_name: 'Küche',
          display_depth: 2,
          path: ['Gebäude A', 'EG', 'Küche'],
        },
        {
          node_id: 13,
          tree_name: 'Haus',
          node_name: 'Küche',
          display_depth: 2,
          path: ['Gebäude B', 'EG', 'Küche'],
        },
      ],
    })

    await wrapper.find('input').setValue('Küche')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()

    const buttons = wrapper.findAll('button:not([disabled])')
    expect(buttons.some((button) => button.text().includes('Gebäude A'))).toBe(false)
    expect(buttons.some((button) => button.text().includes('Gebäude B'))).toBe(false)
    expect(wrapper.find('[title="Haus › Gebäude A › EG › Küche"]').exists()).toBe(true)
    expect(wrapper.find('[title="Haus › Gebäude B › EG › Küche"]').exists()).toBe(true)
  })
})
