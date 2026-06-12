import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  const storage = {
    getItem: vi.fn().mockReturnValue(null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  }
  Object.defineProperty(window, 'localStorage', {
    value: storage,
    configurable: true,
  })
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
  })
  vi.doMock('@vue-flow/core', () => ({
    VueFlow: { template: '<div data-testid="vue-flow"><slot /></div>' },
    Handle: { template: '<span />' },
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    useVueFlow: () => ({ project: (point) => point }),
    addEdge: (edge, edges) => [...edges, edge],
  }))
  vi.doMock('@vue-flow/background', () => ({
    Background: { template: '<div />' },
  }))
  vi.doMock('@vue-flow/controls', () => ({
    Controls: { template: '<div />' },
  }))
  vi.doMock('@vue-flow/minimap', () => ({
    MiniMap: { template: '<div />' },
  }))
})

afterEach(() => {
  vi.useRealTimers()
  vi.doUnmock('@/api/client')
  vi.doUnmock('vue-router')
  vi.doUnmock('@vue-flow/core')
  vi.doUnmock('@vue-flow/background')
  vi.doUnmock('@vue-flow/controls')
  vi.doUnmock('@vue-flow/minimap')
})

function makeGraph(id = 'graph-1', overrides = {}) {
  return {
    id,
    name: 'Main Graph',
    description: 'Main description',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'n1', type: 'const_value', position: { x: 10, y: 20 }, data: { value: 1, _dbg: 'old' } },
      ],
      edges: [
        { id: 'e1', source: 'n1', target: 'n2', sourceHandle: 'out', targetHandle: 'in' },
      ],
    },
    ...overrides,
  }
}

async function mountLogicView({ isAdmin, graphs = [], routeQuery = {}, graphDetails = {} }) {
  vi.doMock('vue-router', () => ({
    useRoute: () => ({ query: routeQuery }),
  }))
  const defaultGraph = graphs[0] ?? makeGraph()
  const logicApi = {
    nodeTypes: vi.fn().mockResolvedValue({ data: [{ type: 'const_value', config_schema: { value: { default: 0 } } }] }),
    listGraphs: vi.fn().mockResolvedValue({ data: graphs }),
    getGraph: vi.fn().mockImplementation(id => Promise.resolve({ data: graphDetails[id] ?? defaultGraph })),
    createGraph: vi.fn().mockResolvedValue({
      data: { id: 'graph-new', name: 'New', description: '', enabled: true, flow_data: { nodes: [], edges: [] } },
    }),
    saveGraph: vi.fn().mockImplementation((id, payload) => Promise.resolve({ data: { ...defaultGraph, id, ...payload } })),
    runGraph: vi.fn().mockResolvedValue({ data: { outputs: { n1: { value: 42, changed: true } } } }),
    patchGraph: vi.fn().mockImplementation((id, payload) => Promise.resolve({ data: { ...defaultGraph, id, ...payload } })),
    deleteGraph: vi.fn().mockResolvedValue({}),
    duplicateGraph: vi.fn().mockResolvedValue({ data: makeGraph('graph-copy', { name: 'Main Graph Copy' }) }),
    exportGraph: vi.fn().mockResolvedValue({ data: { export_type: 'logic_graph', name: 'Main Graph' } }),
    importGraph: vi.fn().mockResolvedValue({ data: makeGraph('graph-imported', { name: 'Imported Graph' }) }),
  }
  vi.doMock('@/api/client', () => ({ logicApi }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: isAdmin ? 'admin' : 'viewer', is_admin: isAdmin }

  const mod = await import('@/views/LogicView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: {
        NodePalette: true,
        NodeConfigPanel: true,
        Modal: { template: '<div><slot /><slot name="footer" /></div>' },
        ConfirmDialog: true,
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, logicApi }
}

describe('LogicView auth gates', () => {
  it('hides graph creation for non-admin users', async () => {
    const { wrapper, logicApi } = await mountLogicView({ isAdmin: false })

    expect(wrapper.text()).not.toContain('+ Neu')
    expect(logicApi.createGraph).not.toHaveBeenCalled()
  })

  it('keeps existing graphs read-only for non-admin users', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: false,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    expect(logicApi.getGraph).toHaveBeenCalledWith('graph-1')
    expect(wrapper.find('[data-testid="btn-run"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-toggle-enabled"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-rename"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-duplicate"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-import"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-delete"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-export"]').exists()).toBe(true)

    wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'in' })
    expect(wrapper.vm.edges).toEqual(graph.flow_data.edges)

    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ left: 10, top: 20 }) }
    wrapper.vm.onDrop({
      dataTransfer: { getData: () => 'const_value' },
      clientX: 30,
      clientY: 45,
    })
    expect(wrapper.vm.nodes).toHaveLength(1)

    wrapper.vm.onNodeClick({ node: wrapper.vm.nodes[0] })
    expect(wrapper.vm.selectedNode).toBe(null)

    await wrapper.vm.saveGraph()
    await wrapper.vm.runGraph()
    await wrapper.vm.doToggleEnabled()
    await wrapper.vm.doDuplicateGraph()
    wrapper.vm.openRenameGraph()
    await wrapper.vm.doRenameGraph()
    wrapper.vm.confirmDeleteGraph()
    await wrapper.vm.doDeleteGraph()
    await wrapper.vm.onImportFile({ target: { files: [new File(['{}'], 'logic.json')], value: 'logic.json' } })

    expect(logicApi.saveGraph).not.toHaveBeenCalled()
    expect(logicApi.runGraph).not.toHaveBeenCalled()
    expect(logicApi.patchGraph).not.toHaveBeenCalled()
    expect(logicApi.duplicateGraph).not.toHaveBeenCalled()
    expect(logicApi.importGraph).not.toHaveBeenCalled()
    expect(logicApi.deleteGraph).not.toHaveBeenCalled()
  })

  it('lets admins create a graph', async () => {
    const { wrapper, logicApi } = await mountLogicView({ isAdmin: true })

    await wrapper.find('.btn-primary').trigger('click')
    wrapper.vm.newGraphName = 'Automation'
    wrapper.vm.newGraphDesc = 'Created from test'
    await wrapper.vm.doCreateGraph()

    expect(logicApi.createGraph).toHaveBeenCalledWith({
      name: 'Automation',
      description: 'Created from test',
      enabled: true,
      flow_data: { nodes: [], edges: [] },
    })
    expect(wrapper.vm.activeGraphId).toBe('graph-new')
  })

  it('loads and operates an active graph', async () => {
    const graph = makeGraph('graph-1')
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:logic-export')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: {
        'graph-1': graph,
        'graph-copy': makeGraph('graph-copy', { name: 'Main Graph Copy' }),
        'graph-imported': makeGraph('graph-imported', { name: 'Imported Graph' }),
      },
    })

    expect(logicApi.getGraph).toHaveBeenCalledWith('graph-1')
    expect(wrapper.find('[data-testid="btn-run"]').exists()).toBe(true)

    wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'in' })
    expect(wrapper.vm.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'n1', target: 'n3', type: 'smoothstep' }),
    ]))

    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ left: 10, top: 20 }) }
    wrapper.vm.onDrop({
      dataTransfer: { getData: () => 'const_value' },
      clientX: 30,
      clientY: 45,
    })
    expect(wrapper.vm.nodes.some(node => node.id.startsWith('const_value-'))).toBe(true)

    wrapper.vm.onNodeClick({ node: wrapper.vm.nodes[0] })
    wrapper.vm.onNodeDataUpdate({ value: 7 })
    vi.advanceTimersByTime(500)
    await flushPromises()
    expect(wrapper.vm.nodes[0].data.value).toBe(7)

    await wrapper.vm.saveGraph()
    expect(logicApi.saveGraph).toHaveBeenCalledWith('graph-1', expect.objectContaining({ name: 'Main Graph' }))

    await wrapper.vm.runGraph()
    expect(logicApi.runGraph).toHaveBeenCalledWith('graph-1')
    expect(wrapper.vm.lastRunOutputs.n1.value).toBe(42)

    wrapper.vm.toggleDebug()
    await wrapper.vm.runGraph()
    expect(wrapper.vm.nodes[0].data._dbg).toBe('= 42')

    await wrapper.vm.doToggleEnabled()
    expect(logicApi.patchGraph).toHaveBeenCalledWith('graph-1', { enabled: false })

    await wrapper.vm.doDuplicateGraph()
    expect(logicApi.duplicateGraph).toHaveBeenCalledWith('graph-1')
    expect(wrapper.vm.activeGraphId).toBe('graph-copy')

    await wrapper.vm.doExportGraph()
    expect(logicApi.exportGraph).toHaveBeenCalledWith('graph-copy')
    expect(createObjectURL).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()

    wrapper.vm.openRenameGraph()
    wrapper.vm.renameGraphName = 'Renamed Graph'
    wrapper.vm.renameGraphDesc = 'Updated'
    await wrapper.vm.doRenameGraph()
    expect(logicApi.patchGraph).toHaveBeenCalledWith('graph-copy', { name: 'Renamed Graph', description: 'Updated' })

    const file = new File([JSON.stringify({ export_type: 'logic_graph' })], 'logic.json', { type: 'application/json' })
    await wrapper.vm.onImportFile({ target: { files: [file], value: 'logic.json' } })
    expect(logicApi.importGraph).toHaveBeenCalledWith({ export_type: 'logic_graph' })
    expect(wrapper.vm.activeGraphId).toBe('graph-imported')

    wrapper.vm.confirmDeleteGraph()
    expect(wrapper.vm.showDeleteConfirm).toBe(true)
    await wrapper.vm.doDeleteGraph()
    expect(logicApi.deleteGraph).toHaveBeenCalledWith('graph-imported')
    expect(wrapper.vm.activeGraphId).toBe('')

    clickSpy.mockRestore()
    createObjectURL.mockRestore()
    revokeObjectURL.mockRestore()
  })
})
