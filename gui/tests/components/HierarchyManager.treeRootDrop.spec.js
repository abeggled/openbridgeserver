/**
 * Tests for dropping a logic graph directly onto a tree's card header
 * (#1217 follow-up) — links it to the tree's hidden root node, no visible
 * sub-folder required.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const TREE_NODE_STUB = {
  name: 'HierarchyNodeTree',
  template: '<div class="node-tree" />',
  props: ['nodes', 'treeId', 'depth', 'selectedNode'],
  emits: ['add-child', 'edit', 'delete', 'reorder', 'link-graph-error', 'unlink-graph-error', 'load-graphs-error'],
}

const TREES = [{ id: 't1', name: 'Beschattung', description: '', root_node_id: 'r1' }]

function makeHierarchyApi(overrides = {}) {
  return {
    listTrees:    vi.fn().mockResolvedValue({ data: TREES }),
    createTree:   vi.fn(),
    updateTree:   vi.fn(),
    deleteTree:   vi.fn(),
    getTreeNodes: vi.fn().mockResolvedValue({ data: [] }),
    createNode:   vi.fn(),
    updateNode:   vi.fn(),
    deleteNode:   vi.fn(),
    importFromEts: vi.fn(),
    getNodeLogicGraphs:   vi.fn().mockResolvedValue({ data: [] }),
    createLogicGraphLink: vi.fn().mockResolvedValue({}),
    deleteLogicGraphLink: vi.fn().mockResolvedValue({}),
    ...overrides,
  }
}

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/components/HierarchyNodeTree.vue', () => ({ default: TREE_NODE_STUB }))
  vi.doMock('@/utils/hierarchyDepthOptions.js', () => ({
    buildDepthOptions: () => [{ value: 0, label: 'Alle', disabled: false }],
  }))
})

async function mountHM(hierarchyApiOverrides = {}) {
  const hierarchyApi = makeHierarchyApi(hierarchyApiOverrides)
  vi.doMock('@/api/client.js', () => ({
    hierarchyApi,
    logicApi: { listGraphs: vi.fn().mockResolvedValue({ data: [] }) },
  }))
  const { default: HierarchyManager } = await import('@/components/HierarchyManager.vue')
  const w = mount(HierarchyManager)
  await flushPromises()
  return { w, hierarchyApi }
}

describe('HierarchyManager — drop directly onto a tree card (#1217 follow-up)', () => {
  it('loads graphs linked to the tree root node on mount', async () => {
    const { hierarchyApi } = await mountHM()
    expect(hierarchyApi.getNodeLogicGraphs).toHaveBeenCalledWith('r1')
  })

  it('dropping a graph on the tree header links it to the root node', async () => {
    const { w, hierarchyApi } = await mountHM()
    const header = w.find('[data-testid="tree-t1"] .card-header')
    const getData = vi.fn().mockReturnValue('g1')

    await header.trigger('dragover')
    await header.trigger('drop', { dataTransfer: { getData } })
    await flushPromises()

    expect(hierarchyApi.createLogicGraphLink).toHaveBeenCalledWith({ node_id: 'r1', graph_id: 'g1' })
  })

  it('ignores a drop with no drag payload', async () => {
    const { w, hierarchyApi } = await mountHM()
    const header = w.find('[data-testid="tree-t1"] .card-header')
    await header.trigger('drop', { dataTransfer: { getData: vi.fn().mockReturnValue('') } })
    await flushPromises()
    expect(hierarchyApi.createLogicGraphLink).not.toHaveBeenCalled()
  })

  it('shows chips for graphs already linked to the tree root, with an unlink action', async () => {
    const { w } = await mountHM({
      getNodeLogicGraphs: vi.fn().mockResolvedValue({ data: [{ id: 'g1', name: 'Fenster A', enabled: true, link_id: 'l1' }] }),
    })
    const chip = w.find('[data-testid="linked-graph-tree-t1-g1"]')
    expect(chip.exists()).toBe(true)
    expect(chip.text()).toContain('Fenster A')
    expect(w.find('[data-testid="btn-unlink-graph-tree-t1-g1"]').exists()).toBe(true)
  })

  it('unlinking a tree-root graph calls deleteLogicGraphLink with the root node id', async () => {
    const { w, hierarchyApi } = await mountHM({
      getNodeLogicGraphs: vi.fn().mockResolvedValue({ data: [{ id: 'g1', name: 'Fenster A', enabled: true, link_id: 'l1' }] }),
    })
    await w.find('[data-testid="btn-unlink-graph-tree-t1-g1"]').trigger('click')
    await flushPromises()
    expect(hierarchyApi.deleteLogicGraphLink).toHaveBeenCalledWith('r1', 'g1')
  })
})
