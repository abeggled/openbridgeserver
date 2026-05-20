/**
 * Tests for HierarchyCombobox.vue.
 *
 * Builds paths from listTrees() + getTreeNodes(treeId) and disambiguates
 * same-name nodes across siblings via their full path.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
  // Provide a no-op ResizeObserver because PathLabel uses it.
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountHierarchyCombobox(props = {}, scenario = {}) {
  const trees = scenario.trees ?? [{ id: 1, name: 'Gebäude' }]
  const nodesByTree =
    scenario.nodesByTree ?? {
      1: [
        { id: 10, tree_id: 1, parent_id: null, name: 'Gebäude' },
        { id: 11, tree_id: 1, parent_id: 10, name: 'EG' },
        { id: 12, tree_id: 1, parent_id: 11, name: 'Küche' },
      ],
    }
  const hierarchyApi = {
    listTrees: vi.fn().mockResolvedValue({ data: trees }),
    getTreeNodes: vi
      .fn()
      .mockImplementation((tid) => Promise.resolve({ data: nodesByTree[tid] ?? [] })),
  }
  vi.doMock('@/api/client', () => ({ hierarchyApi }))
  const mod = await import('@/components/ui/HierarchyCombobox.vue')
  const wrapper = mount(mod.default, { props, attachTo: document.body })
  await flushPromises()
  await flushPromises()
  return { wrapper, hierarchyApi }
}

describe('HierarchyCombobox', () => {
  it('loads trees and node paths on mount', async () => {
    const { wrapper, hierarchyApi } = await mountHierarchyCombobox({ modelValue: [] })
    expect(hierarchyApi.listTrees).toHaveBeenCalled()
    expect(hierarchyApi.getTreeNodes).toHaveBeenCalledWith(1)
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    const items = wrapper.findAll('[data-testid^="combobox-item-"]')
    expect(items.length).toBeGreaterThan(0)
  })

  it('disambiguates same-named nodes across siblings via full path', async () => {
    const { wrapper } = await mountHierarchyCombobox(
      { modelValue: [] },
      {
        trees: [{ id: 1, name: 'Haus' }],
        nodesByTree: {
          1: [
            { id: 1, tree_id: 1, parent_id: null, name: 'Gebäude' },
            { id: 2, tree_id: 1, parent_id: 1, name: 'EG' },
            { id: 3, tree_id: 1, parent_id: 1, name: 'OG' },
            { id: 4, tree_id: 1, parent_id: 2, name: 'Küche' },
            { id: 5, tree_id: 1, parent_id: 3, name: 'Küche' },
          ],
        },
      },
    )
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    const text = wrapper.text()
    // Both 'EG' and 'OG' must be visible so the user can disambiguate the two 'Küche' nodes.
    expect(text).toContain('EG')
    expect(text).toContain('OG')
    expect((text.match(/Küche/g) || []).length).toBe(2)
  })

  it('filters nodes by query', async () => {
    const { wrapper } = await mountHierarchyCombobox({ modelValue: [] })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    input.element.value = 'Küche'
    await input.trigger('input')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()
    const items = wrapper.findAll('[data-testid^="combobox-item-"]')
    expect(items.length).toBe(1)
  })

  it('emits string[] of composite ids on selection', async () => {
    const { wrapper } = await mountHierarchyCombobox({ modelValue: [] })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    const last = events[events.length - 1][0]
    expect(Array.isArray(last)).toBe(true)
    expect(last.length).toBe(1)
    expect(last[0]).toMatch(/^1:/)
  })

  it('renders chips with the full path for selected nodes', async () => {
    const { wrapper } = await mountHierarchyCombobox(
      { modelValue: ['1:12'] }, // Gebäude > EG > Küche
    )
    const chips = wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])')
    expect(chips.length).toBe(1)
    const html = chips[0].html()
    expect(html).toContain('Küche')
  })
})
