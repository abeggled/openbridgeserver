/**
 * Tests for the drag-source logic-graph palette in Settings → Hierarchy
 * (#1217) — dragging a graph onto a node links it there.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { LOGIC_GRAPH_DRAG_MIME } from '@/utils/hierarchyLogicGraphDrag.js'

const TREE_NODE_STUB = {
  name: 'HierarchyNodeTree',
  template: '<div class="node-tree" />',
  props: ['nodes', 'treeId', 'depth', 'selectedNode'],
  emits: ['add-child', 'edit', 'delete', 'reorder', 'link-graph-error', 'unlink-graph-error', 'load-graphs-error'],
}

const GRAPHS = [
  { id: 'g1', name: 'Fenster A', enabled: true },
  { id: 'g2', name: 'Fenster B', enabled: false },
]

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client.js', () => ({
    hierarchyApi: {
      listTrees:    vi.fn().mockResolvedValue({ data: [] }),
      createTree:   vi.fn(),
      updateTree:   vi.fn(),
      deleteTree:   vi.fn(),
      getTreeNodes: vi.fn(),
      createNode:   vi.fn(),
      updateNode:   vi.fn(),
      deleteNode:   vi.fn(),
      importFromEts: vi.fn(),
    },
    logicApi: {
      listGraphs: vi.fn().mockResolvedValue({ data: GRAPHS }),
    },
  }))
  vi.doMock('@/components/HierarchyNodeTree.vue', () => ({ default: TREE_NODE_STUB }))
  vi.doMock('@/utils/hierarchyDepthOptions.js', () => ({
    buildDepthOptions: () => [{ value: 0, label: 'Alle', disabled: false }],
  }))
})

async function mountHM() {
  const { default: HierarchyManager } = await import('@/components/HierarchyManager.vue')
  const w = mount(HierarchyManager)
  await flushPromises()
  return w
}

describe('HierarchyManager — logic graph palette (#1217)', () => {
  it('fetches the graph list on mount', async () => {
    await mountHM()
    const { logicApi } = await import('@/api/client.js')
    expect(logicApi.listGraphs).toHaveBeenCalled()
  })

  it('renders a draggable entry per graph', async () => {
    const w = await mountHM()
    const a = w.find('[data-testid="palette-graph-g1"]')
    const b = w.find('[data-testid="palette-graph-g2"]')
    expect(a.exists()).toBe(true)
    expect(b.exists()).toBe(true)
    expect(a.attributes('draggable')).toBe('true')
    expect(a.text()).toContain('Fenster A')
  })

  it('shows the empty state when there are no graphs', async () => {
    vi.doMock('@/api/client.js', () => ({
      hierarchyApi: {
        listTrees: vi.fn().mockResolvedValue({ data: [] }),
        createTree: vi.fn(), updateTree: vi.fn(), deleteTree: vi.fn(),
        getTreeNodes: vi.fn(), createNode: vi.fn(), updateNode: vi.fn(), deleteNode: vi.fn(), importFromEts: vi.fn(),
      },
      logicApi: { listGraphs: vi.fn().mockResolvedValue({ data: [] }) },
    }))
    const w = await mountHM()
    expect(w.find('[data-testid="palette-graph-g1"]').exists()).toBe(false)
  })

  it('dragstart on a palette entry sets the drag payload to the graph id', async () => {
    const w = await mountHM()
    const setData = vi.fn()
    await w.find('[data-testid="palette-graph-g1"]').trigger('dragstart', {
      dataTransfer: { setData, effectAllowed: '' },
    })
    expect(setData).toHaveBeenCalledWith(LOGIC_GRAPH_DRAG_MIME, 'g1')
  })
})
