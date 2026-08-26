/**
 * Issue #1157 — a block renamed inline on the logic sheet and the block
 * properties panel must not drift apart: the panel keeps its own copy of the
 * selected block, so LogicView syncs the name back into it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))
  vi.doMock('@vue-flow/core', () => ({
    VueFlow: { name: 'VueFlow', props: ['nodes', 'edges'], template: '<div data-testid="vue-flow"><slot /></div>' },
    Handle: { template: '<span />' },
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    useVueFlow: () => ({ project: (point) => point, getViewport: () => ({ x: 0, y: 0, zoom: 1 }) }),
    addEdge: (edge, edges) => [...edges, edge],
  }))
  vi.doMock('@vue-flow/background', () => ({ Background: { template: '<div />' } }))
  vi.doMock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))
  vi.doMock('@vue-flow/minimap', () => ({ MiniMap: { template: '<div />' } }))
})

const mountedWrappers = []

afterEach(() => {
  while (mountedWrappers.length) mountedWrappers.pop().unmount()
  vi.doUnmock('@/api/client')
  vi.doUnmock('vue-router')
  vi.doUnmock('@vue-flow/core')
  vi.doUnmock('@vue-flow/background')
  vi.doUnmock('@vue-flow/controls')
  vi.doUnmock('@vue-flow/minimap')
})

const GRAPH = {
  id: 'graph-1',
  name: 'Main Graph',
  description: '',
  enabled: true,
  flow_data: {
    nodes: [{ id: 'and-1', type: 'and', position: { x: 0, y: 0 }, data: { input_count: 2 } }],
    edges: [],
  },
}

async function mountLogicView() {
  const logicApi = {
    nodeTypes:  vi.fn().mockResolvedValue({ data: [{ type: 'and', label: 'AND', config_schema: {} }] }),
    listGraphs: vi.fn().mockResolvedValue({ data: [GRAPH] }),
    getGraph:   vi.fn().mockResolvedValue({ data: structuredClone(GRAPH) }),
    saveGraph:  vi.fn().mockResolvedValue({ data: GRAPH }),
  }
  vi.doMock('@/api/client', () => ({ logicApi }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/views/LogicView.vue')
  const wrapper = mount(mod.default, {
    global: { plugins: [pinia], stubs: { Modal: true, ConfirmDialog: true, NodePalette: true } },
    attachTo: document.body,
  })
  mountedWrappers.push(wrapper)
  await flushPromises()
  wrapper.vm.activeGraphId = GRAPH.id
  await wrapper.vm.loadGraph()
  await flushPromises()
  return wrapper
}

describe('LogicView — inline rename keeps the properties panel in sync (#1157)', () => {
  it('updates the panel heading when the block is renamed on the canvas', async () => {
    const w = await mountLogicView()
    const vm = w.vm

    vm.selectedNode = { ...vm.nodes[0] }
    await flushPromises()
    expect(w.get('[data-testid="node-label-input"]').element.value).toBe('')

    // VueFlow's `updateNodeData` assigns a fresh `data` object onto the
    // existing (reactive) node rather than replacing the array — the sync must
    // pick that up, not just a wholesale array swap.
    const node = vm.nodes.find(n => n.id === 'and-1')
    node.data = { ...node.data, label: 'Treppenhaus' }
    await flushPromises()

    expect(vm.selectedNode.data.label).toBe('Treppenhaus')
    expect(w.get('[data-testid="node-label-input"]').element.value).toBe('Treppenhaus')
  })

  it('leaves the selection alone when an unrelated block is renamed', async () => {
    const w = await mountLogicView()
    const vm = w.vm

    vm.nodes = [...vm.nodes, { id: 'or-1', type: 'or', position: { x: 50, y: 0 }, data: {} }]
    vm.selectedNode = { ...vm.nodes[0] }
    await flushPromises()
    const before = vm.selectedNode

    const other = vm.nodes.find(n => n.id === 'or-1')
    other.data = { label: 'Andere' }
    await flushPromises()

    expect(vm.selectedNode).toBe(before)
  })

  it('does not touch the selection when the renamed block is removed', async () => {
    const w = await mountLogicView()
    const vm = w.vm

    const node = vm.nodes.find(n => n.id === 'and-1')
    node.data = { ...node.data, label: 'Treppenhaus' }
    await flushPromises()
    vm.selectedNode = { ...vm.nodes[0] }
    await flushPromises()
    const selected = vm.selectedNode

    vm.nodes = []
    await flushPromises()

    expect(vm.selectedNode).toBe(selected)
    expect(vm.selectedNode.data.label).toBe('Treppenhaus')
  })

  it('ignores a block that never carried a name', async () => {
    const w = await mountLogicView()
    const vm = w.vm

    vm.selectedNode = { ...vm.nodes[0] }
    await flushPromises()
    const selected = vm.selectedNode

    const node = vm.nodes.find(n => n.id === 'and-1')
    node.data = { ...node.data, input_count: 3 }
    await flushPromises()

    expect(vm.selectedNode).toBe(selected)
  })

  it('does nothing while no block is selected', async () => {
    const w = await mountLogicView()
    const vm = w.vm

    const node = vm.nodes.find(n => n.id === 'and-1')
    node.data = { ...node.data, label: 'Treppenhaus' }
    await flushPromises()

    expect(vm.selectedNode).toBe(null)
  })
})
