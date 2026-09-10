/**
 * Tests for linking/unlinking logic graphs directly in the hierarchy tree
 * view via drag-and-drop (#1217).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { LOGIC_GRAPH_DRAG_MIME } from '@/utils/hierarchyLogicGraphDrag.js'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client.js')
})

const NODES = [
  { id: 'a', name: 'Alpha', description: '', children: [] },
]

function dropEvent(graphId) {
  return {
    preventDefault: () => {},
    dataTransfer: { getData: (type) => (type === LOGIC_GRAPH_DRAG_MIME ? graphId : '') },
  }
}

async function mountTree({ linkedGraphs = [], createFails = false, deleteFails = false } = {}) {
  const hierarchyApi = {
    getNodeLogicGraphs: vi.fn().mockResolvedValue({ data: linkedGraphs }),
    createLogicGraphLink: createFails ? vi.fn().mockRejectedValue(new Error('fail')) : vi.fn().mockResolvedValue({}),
    deleteLogicGraphLink: deleteFails ? vi.fn().mockRejectedValue(new Error('fail')) : vi.fn().mockResolvedValue({}),
  }
  vi.doMock('@/api/client.js', () => ({ hierarchyApi }))
  const mod = await import('@/components/HierarchyNodeTree.vue')
  const wrapper = mount(mod.default, {
    props: { nodes: NODES, treeId: 'tree-1', depth: 0, selectedNode: null },
  })
  await flushPromises()
  return { wrapper, hierarchyApi }
}

describe('HierarchyNodeTree — linked logic graphs (#1217)', () => {
  it('fetches linked logic graphs for every visible node on mount', async () => {
    const { hierarchyApi } = await mountTree()
    expect(hierarchyApi.getNodeLogicGraphs).toHaveBeenCalledWith('a')
  })

  it('renders a chip for each linked logic graph', async () => {
    const { wrapper } = await mountTree({ linkedGraphs: [{ id: 'g1', name: 'Fenster A', enabled: true, link_id: 'l1' }] })
    expect(wrapper.find('[data-testid="linked-graph-a-g1"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Fenster A')
  })

  it('shows no chip row when nothing is linked', async () => {
    const { wrapper } = await mountTree({ linkedGraphs: [] })
    expect(wrapper.find('[data-testid^="linked-graph-"]').exists()).toBe(false)
  })

  it('dropping a dragged graph id onto a node creates a link and refetches', async () => {
    const { wrapper, hierarchyApi } = await mountTree()
    hierarchyApi.getNodeLogicGraphs.mockResolvedValueOnce({ data: [] })
    hierarchyApi.getNodeLogicGraphs.mockResolvedValueOnce({ data: [{ id: 'g2', name: 'Neu Verlinkt', enabled: true, link_id: 'l2' }] })

    // vue-test-utils' trigger() does not let us pass a custom dataTransfer
    // through the synthetic event easily — dispatch a real DOM event instead.
    const el = wrapper.find('[data-testid="node-a"]').element
    el.dispatchEvent(Object.assign(new Event('drop', { bubbles: true, cancelable: true }), dropEvent('g2')))
    await flushPromises()

    expect(hierarchyApi.createLogicGraphLink).toHaveBeenCalledWith({ node_id: 'a', graph_id: 'g2' })
  })

  it('ignores a drop that carries no logic-graph payload', async () => {
    const { wrapper, hierarchyApi } = await mountTree()
    const el = wrapper.find('[data-testid="node-a"]').element
    el.dispatchEvent(Object.assign(new Event('drop', { bubbles: true, cancelable: true }), dropEvent('')))
    await flushPromises()
    expect(hierarchyApi.createLogicGraphLink).not.toHaveBeenCalled()
  })

  it('emits link-graph-error when the link request fails', async () => {
    const { wrapper } = await mountTree({ createFails: true })
    const el = wrapper.find('[data-testid="node-a"]').element
    el.dispatchEvent(Object.assign(new Event('drop', { bubbles: true, cancelable: true }), dropEvent('g3')))
    await flushPromises()
    expect(wrapper.emitted('link-graph-error')).toBeTruthy()
  })

  it('emits load-graphs-error when the initial fetch fails', async () => {
    const hierarchyApi = {
      getNodeLogicGraphs: vi.fn().mockRejectedValue(new Error('fail')),
      createLogicGraphLink: vi.fn(),
      deleteLogicGraphLink: vi.fn(),
    }
    vi.doMock('@/api/client.js', () => ({ hierarchyApi }))
    const mod = await import('@/components/HierarchyNodeTree.vue')
    const wrapper = mount(mod.default, {
      props: { nodes: NODES, treeId: 'tree-1', depth: 0, selectedNode: null },
    })
    await flushPromises()
    expect(wrapper.emitted('load-graphs-error')).toBeTruthy()
  })

  it('clicking the unlink button removes the link and refetches', async () => {
    const { wrapper, hierarchyApi } = await mountTree({ linkedGraphs: [{ id: 'g1', name: 'Fenster A', enabled: true, link_id: 'l1' }] })
    hierarchyApi.getNodeLogicGraphs.mockResolvedValueOnce({ data: [] })

    await wrapper.find('[data-testid="btn-unlink-graph-a-g1"]').trigger('click')
    await flushPromises()

    expect(hierarchyApi.deleteLogicGraphLink).toHaveBeenCalledWith('a', 'g1')
  })

  it('emits unlink-graph-error when the unlink request fails', async () => {
    const { wrapper } = await mountTree({ linkedGraphs: [{ id: 'g1', name: 'Fenster A', enabled: true, link_id: 'l1' }], deleteFails: true })
    await wrapper.find('[data-testid="btn-unlink-graph-a-g1"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('unlink-graph-error')).toBeTruthy()
  })

  it('shows a ring highlight on dragover and clears it on dragleave', async () => {
    const { wrapper } = await mountTree()
    const row = wrapper.find('[data-testid="node-a"]')
    await row.trigger('dragover')
    expect(row.classes()).toContain('ring-2')
    await row.trigger('dragleave')
    expect(row.classes()).not.toContain('ring-2')
  })
})
