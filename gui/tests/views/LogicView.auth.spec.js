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
  vi.doUnmock('@/api/logicAuthz')
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
        { id: 'n1', type: 'const_value', position: { x: 10, y: 20 }, data: { value: 1, _dbg: 'old', _dbg_title: 'old title' } },
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
  }
  const logicRunAuthzApi = {
    preflight: vi.fn().mockResolvedValue({
      data: {
        graph_id: 'graph-1',
        allowed: true,
        checks: [
          { target_type: 'logic_graph', target_id: 'graph-1', node_ids: [], allowed: true, reason: 'allowed' },
          { target_type: 'logic_graph_state', target_id: 'enabled', node_ids: [], allowed: true, reason: 'enabled' },
        ],
      },
    }),
  }
  Object.assign(logicApi, {
    runGraph: vi.fn().mockResolvedValue({ data: { outputs: { n1: { value: 42, changed: true } } } }),
    patchGraph: vi.fn().mockImplementation((id, payload) => Promise.resolve({ data: { ...defaultGraph, id, ...payload } })),
    deleteGraph: vi.fn().mockResolvedValue({}),
    duplicateGraph: vi.fn().mockResolvedValue({ data: makeGraph('graph-copy', { name: 'Main Graph Copy' }) }),
    exportGraph: vi.fn().mockResolvedValue({ data: { export_type: 'logic_graph', name: 'Main Graph' } }),
    importGraph: vi.fn().mockResolvedValue({ data: makeGraph('graph-imported', { name: 'Imported Graph' }) }),
  })
  vi.doMock('@/api/client', () => ({ logicApi }))
  vi.doMock('@/api/logicAuthz', () => ({ logicRunAuthzApi }))

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
        ActionPreflightDialog: {
          props: ['modelValue', 'items', 'loading', 'error'],
          emits: ['update:modelValue', 'confirm'],
          template: '<div v-if="modelValue" data-testid="run-preflight"><button data-testid="preflight-confirm" :disabled="loading || !!error || items.some(item => !item.allowed)" @click="$emit(\'confirm\')">confirm</button></div>',
        },
        Modal: { template: '<div><slot /><slot name="footer" /></div>' },
        ConfirmDialog: true,
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, logicApi, logicRunAuthzApi }
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
    expect(wrapper.find('[data-testid="btn-run"]').exists()).toBe(true)
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

  it('preflights a delegated graph run and executes only after confirmation', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView({
      isAdmin: false,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    await wrapper.get('[data-testid="btn-run"]').trigger('click')
    await flushPromises()
    expect(logicRunAuthzApi.preflight).toHaveBeenCalledWith('graph-1')
    expect(logicApi.runGraph).not.toHaveBeenCalled()

    await wrapper.get('[data-testid="preflight-confirm"]').trigger('click')
    await flushPromises()
    expect(logicApi.runGraph).toHaveBeenCalledWith('graph-1')
  })

  it('keeps a denied preflight from executing the graph', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView({
      isAdmin: false,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicRunAuthzApi.preflight.mockResolvedValueOnce({
      data: {
        graph_id: 'graph-1',
        allowed: false,
        checks: [{ target_type: 'logic_capability', target_id: 'sms', node_ids: ['n2'], allowed: false, reason: 'missing_allow' }],
      },
    })

    await wrapper.get('[data-testid="btn-run"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="preflight-confirm"]').attributes('disabled')).toBeDefined()
    expect(logicApi.runGraph).not.toHaveBeenCalled()
  })

  it('discards a preflight when graph selection changes before confirmation', async () => {
    const graphOne = makeGraph('graph-1')
    const graphTwo = makeGraph('graph-2', { name: 'Second Graph' })
    const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView({
      isAdmin: false,
      graphs: [graphOne, graphTwo],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graphOne, 'graph-2': graphTwo },
    })

    await wrapper.get('[data-testid="btn-run"]').trigger('click')
    await flushPromises()
    expect(wrapper.vm.preflightGraphId).toBe('graph-1')

    wrapper.vm.activeGraphId = 'graph-2'
    await flushPromises()
    await wrapper.vm.confirmGraphRun()

    expect(wrapper.vm.showRunPreflight).toBe(false)
    expect(wrapper.vm.preflightGraphId).toBe('')
    expect(logicApi.runGraph).not.toHaveBeenCalled()
  })

  it('ignores a stale preflight response after selection changed', async () => {
    const graphOne = makeGraph('graph-1')
    const graphTwo = makeGraph('graph-2', { name: 'Second Graph' })
    let resolvePreflight
    const pendingPreflight = new Promise(resolve => { resolvePreflight = resolve })
    const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView({
      isAdmin: false,
      graphs: [graphOne, graphTwo],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graphOne, 'graph-2': graphTwo },
    })
    logicRunAuthzApi.preflight.mockReturnValueOnce(pendingPreflight)

    const request = wrapper.vm.requestGraphRun()
    wrapper.vm.activeGraphId = 'graph-2'
    await flushPromises()
    resolvePreflight({ data: { graph_id: 'graph-1', allowed: true, checks: [] } })
    await request

    expect(wrapper.vm.preflightGraphId).toBe('')
    expect(wrapper.vm.runPreflightItems).toEqual([])
    expect(logicApi.runGraph).not.toHaveBeenCalled()
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
    expect(wrapper.vm.nodes[0].data).not.toHaveProperty('_dbg')
    expect(wrapper.vm.nodes[0].data).not.toHaveProperty('_dbg_title')

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
    expect(wrapper.vm.nodes[0].data._dbg_title).toBe('= 42')

    await wrapper.vm.saveGraph()
    const savedPayload = logicApi.saveGraph.mock.calls.at(-1)[1]
    expect(savedPayload.flow_data.nodes[0].data).not.toHaveProperty('_dbg')
    expect(savedPayload.flow_data.nodes[0].data).not.toHaveProperty('_dbg_title')

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

describe('LogicView fmtDebugVal branches', () => {
  async function mountWithActiveGraph() {
    const graph = makeGraph('graph-1')
    return mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
  }

  it('formats __error__ output prominently before other key handling', async () => {
    const { wrapper } = await mountWithActiveGraph()
    wrapper.vm.applyDebugValues({ n1: { __error__: 'Division by zero' } })
    expect(wrapper.vm.nodes[0].data._dbg).toMatch(/Division by zero/)
  })

  it('marks graph cycle diagnostics even when debug mode is off', async () => {
    const { wrapper, logicApi } = await mountWithActiveGraph()
    logicApi.runGraph.mockResolvedValueOnce({
      data: {
        outputs: {
          n1: {
            __error__: 'Graph cycle detected; node was not executed.',
            __diagnostic__: 'graph_cycle',
            __cycle_nodes__: ['n1'],
          },
        },
      },
    })

    await wrapper.vm.runGraph()

    expect(wrapper.vm.debugMode).toBe(false)
    expect(wrapper.vm.nodes[0].data._dbg).toMatch(/Graph cycle detected/)

    logicApi.runGraph.mockResolvedValueOnce({ data: { outputs: { n1: { value: 42, changed: true } } } })
    await wrapper.vm.runGraph()
    expect(wrapper.vm.nodes[0].data._dbg).toBeUndefined()
  })

  it('formats _message output for notify nodes', async () => {
    const { wrapper } = await mountWithActiveGraph()

    wrapper.vm.applyDebugValues({ n1: { _message: 'Alert!', sent: true } })
    expect(wrapper.vm.nodes[0].data._dbg).toContain('"Alert!"')
    expect(wrapper.vm.nodes[0].data._dbg).toContain('sent=✓')

    wrapper.vm.applyDebugValues({ n1: { _message: null } })
    expect(wrapper.vm.nodes[0].data._dbg).toContain('—')

    wrapper.vm.applyDebugValues({ n1: { _message: 'hi' } })
    expect(wrapper.vm.nodes[0].data._dbg).toBe('"hi"')
  })

  it('formats _write_value output for datapoint_write nodes', async () => {
    const { wrapper } = await mountWithActiveGraph()
    wrapper.vm.applyDebugValues({ n1: { _write_value: 99 } })
    expect(wrapper.vm.nodes[0].data._dbg).toBe('→ 99')
  })

  it('formats api_client responses with short strip text and full tooltip text', async () => {
    const { wrapper } = await mountWithActiveGraph()
    const longResponse = 'x'.repeat(1200)

    wrapper.vm.applyDebugValues({ n1: { response: longResponse, status: 500, success: false } })

    expect(wrapper.vm.nodes[0].data._dbg).toContain(`response=${'x'.repeat(80)}…`)
    expect(wrapper.vm.nodes[0].data._dbg).toContain('status=500')
    expect(wrapper.vm.nodes[0].data._dbg).toContain('success=✗')
    expect(wrapper.vm.nodes[0].data._dbg_title).toContain(`response=${'x'.repeat(1000)}…`)
  })

  it('formats generic public-key pairs as fallback', async () => {
    const { wrapper } = await mountWithActiveGraph()

    wrapper.vm.applyDebugValues({ n1: { active: true } })
    expect(wrapper.vm.nodes[0].data._dbg).toContain('active=✓')

    wrapper.vm.applyDebugValues({ n1: { active: false } })
    expect(wrapper.vm.nodes[0].data._dbg).toContain('active=✗')

    wrapper.vm.applyDebugValues({ n1: { state: null } })
    expect(wrapper.vm.nodes[0].data._dbg).toContain('state=—')

    wrapper.vm.applyDebugValues({ n1: { label: 'hello' } })
    expect(wrapper.vm.nodes[0].data._dbg).toContain('label=hello')
  })

  it('returns undefined _dbg when output is null or non-object', async () => {
    const { wrapper } = await mountWithActiveGraph()

    wrapper.vm.applyDebugValues({ n1: null })
    expect(wrapper.vm.nodes[0].data._dbg).toBeUndefined()

    wrapper.vm.applyDebugValues({ n1: 'string' })
    expect(wrapper.vm.nodes[0].data._dbg).toBeUndefined()
  })

  it('returns undefined _dbg when all keys are private', async () => {
    const { wrapper } = await mountWithActiveGraph()
    wrapper.vm.applyDebugValues({ n1: { _internal: 42 } })
    expect(wrapper.vm.nodes[0].data._dbg).toBeUndefined()
  })

  it('clears _dbg from all nodes when debug mode is toggled off', async () => {
    const { wrapper } = await mountWithActiveGraph()

    wrapper.vm.toggleDebug() // false → true
    wrapper.vm.applyDebugValues({ n1: { value: 1, changed: false } })
    expect(wrapper.vm.nodes[0].data._dbg).toBeDefined()
    expect(wrapper.vm.nodes[0].data._dbg_title).toBeDefined()

    wrapper.vm.toggleDebug() // true → false, triggers clearDebugValues
    expect(wrapper.vm.debugMode).toBe(false)
    expect(wrapper.vm.nodes[0].data).not.toHaveProperty('_dbg')
    expect(wrapper.vm.nodes[0].data).not.toHaveProperty('_dbg_title')
  })
})

describe('LogicView WebSocket', () => {
  let savedWebSocket
  beforeEach(() => { savedWebSocket = global.WebSocket })
  afterEach(() => { global.WebSocket = savedWebSocket })

  function overrideStorage(overrides = {}) {
    const storage = {
      getItem: vi.fn().mockImplementation(k => overrides[k] ?? null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
    }
    Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
    Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
  }

  it('skips WebSocket when no access_token in storage', async () => {
    let wsCreated = false
    global.WebSocket = class { constructor() { wsCreated = true } }
    const { wrapper } = await mountLogicView({ isAdmin: true })
    expect(wsCreated).toBe(false)
    wrapper.unmount()
  })

  it('connects WebSocket on mount and closes it on unmount', async () => {
    let wsInstance = null
    global.WebSocket = class { constructor() { wsInstance = this; this.close = vi.fn() } }
    overrideStorage({ access_token: 'tok' })

    const { wrapper } = await mountLogicView({ isAdmin: true })
    expect(wsInstance).toBeTruthy()

    wrapper.unmount()
    expect(wsInstance.close).toHaveBeenCalled()
  })

  it('applies debug values from a logic_run WebSocket message when debug mode is on', async () => {
    let wsInstance = null
    global.WebSocket = class { constructor() { wsInstance = this; this.close = vi.fn() } }
    overrideStorage({ access_token: 'tok', logic_debug_mode: '1' })

    const graph = makeGraph('graph-1')
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wsInstance.onmessage({ data: JSON.stringify({ action: 'logic_run', graph_id: 'graph-1', outputs: { n1: { value: 77, changed: true } } }) })
    expect(wrapper.vm.nodes[0].data._dbg).toBe('= 77')
  })

  it('ignores logic_run message for a different graph_id', async () => {
    let wsInstance = null
    global.WebSocket = class { constructor() { wsInstance = this; this.close = vi.fn() } }
    overrideStorage({ access_token: 'tok', logic_debug_mode: '1' })

    const graph = makeGraph('graph-1')
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wsInstance.onmessage({ data: JSON.stringify({ action: 'logic_run', graph_id: 'OTHER', outputs: { n1: { value: 99, changed: true } } }) })
    expect(wrapper.vm.nodes[0].data._dbg).toBeUndefined()
  })

  it('ignores logic_run message when debug mode is off', async () => {
    let wsInstance = null
    global.WebSocket = class { constructor() { wsInstance = this; this.close = vi.fn() } }
    overrideStorage({ access_token: 'tok' })

    const graph = makeGraph('graph-1')
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wsInstance.onmessage({ data: JSON.stringify({ action: 'logic_run', graph_id: 'graph-1', outputs: { n1: { value: 99, changed: true } } }) })
    expect(wrapper.vm.nodes[0].data._dbg).toBeUndefined()
  })

  it('does not reconnect after close code 4001', async () => {
    let wsInstance = null
    let wsCreatedCount = 0
    global.WebSocket = class { constructor() { wsInstance = this; this.close = vi.fn(); wsCreatedCount++ } }
    overrideStorage({ access_token: 'tok' })

    const { wrapper } = await mountLogicView({ isAdmin: true })
    expect(wsCreatedCount).toBe(1)

    wsInstance.onclose({ code: 4001 })
    vi.advanceTimersByTime(4100)
    expect(wsCreatedCount).toBe(1)

    wrapper.unmount()
  })

  it('reconnects automatically after an abnormal close', async () => {
    let wsInstance = null
    let wsCreatedCount = 0
    global.WebSocket = class { constructor() { wsInstance = this; this.close = vi.fn(); wsCreatedCount++ } }
    overrideStorage({ access_token: 'tok' })

    const { wrapper } = await mountLogicView({ isAdmin: true })
    expect(wsCreatedCount).toBe(1)

    wsInstance.onclose({ code: 1006 })
    vi.advanceTimersByTime(4100)
    expect(wsCreatedCount).toBe(2)

    wrapper.unmount()
  })

  it('handles WebSocket constructor error gracefully', async () => {
    global.WebSocket = class { constructor() { throw new Error('blocked by browser') } }
    overrideStorage({ access_token: 'tok' })

    const { wrapper } = await mountLogicView({ isAdmin: true })
    expect(wrapper.vm).toBeTruthy()
    wrapper.unmount()
  })
})

describe('LogicView graph cycle validation', () => {
  it('blocks direct cycle connections', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'a', type: 'not', position: { x: 0, y: 0 }, data: {} },
          { id: 'b', type: 'not', position: { x: 160, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'a-b', source: 'a', target: 'b', sourceHandle: 'out', targetHandle: 'in1' },
        ],
      },
    })
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wrapper.vm.onConnect({ source: 'b', target: 'a', sourceHandle: 'out', targetHandle: 'in1' })

    expect(wrapper.vm.edges).toHaveLength(1)
    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('allows feedback connections through memory nodes', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'mem', type: 'memory', position: { x: 0, y: 0 }, data: { initial_value: 'false', data_type: 'bool' } },
          { id: 'not', type: 'not', position: { x: 160, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'mem-not', source: 'mem', target: 'not', sourceHandle: 'out', targetHandle: 'in1' },
        ],
      },
    })
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wrapper.vm.onConnect({ source: 'not', target: 'mem', sourceHandle: 'out', targetHandle: 'in' })

    expect(wrapper.vm.edges).toHaveLength(2)
    expect(wrapper.vm.validationWarnings).toEqual([])
  })

  it('blocks saving graphs with direct cycles and marks the nodes', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'a', type: 'not', position: { x: 0, y: 0 }, data: {} },
          { id: 'b', type: 'not', position: { x: 160, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'a-b', source: 'a', target: 'b', sourceHandle: 'out', targetHandle: 'in1' },
          { id: 'b-a', source: 'b', target: 'a', sourceHandle: 'out', targetHandle: 'in1' },
        ],
      },
    })
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    await wrapper.vm.saveGraph()

    expect(logicApi.saveGraph).not.toHaveBeenCalled()
    expect(wrapper.vm.statusMsg.ok).toBe(false)
    expect(wrapper.vm.validationWarnings).toHaveLength(2)
    expect(wrapper.vm.nodes.map(n => n.data._dbg)).toEqual([
      expect.stringContaining('Zyklus'),
      expect.stringContaining('Zyklus'),
    ])
  })

  it('uses API warning counts from runGraph responses', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicApi.runGraph.mockResolvedValueOnce({
      data: {
        outputs: {
          n1: {
            __error__: 'Graph cycle detected; node was not executed.',
            __diagnostic__: 'graph_cycle',
          },
        },
        warnings: [{ node_id: 'n1', code: 'graph_cycle', message: 'cycle' }],
      },
    })

    await wrapper.vm.runGraph()

    expect(wrapper.vm.statusMsg.ok).toBe(false)
    expect(wrapper.vm.statusMsg.text).toContain('Warnungen')
    expect(wrapper.vm.nodes[0].data._dbg).toContain('Graph cycle detected')
  })
})

describe('LogicView operation error handling', () => {
  it('shows error status when doDuplicateGraph fails', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicApi.duplicateGraph.mockRejectedValue({ response: { data: { detail: 'Duplicate failed' } } })

    await wrapper.vm.doDuplicateGraph()

    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('shows error status when doExportGraph fails', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicApi.exportGraph.mockRejectedValue({ response: { data: { detail: 'Export failed' } } })

    await wrapper.vm.doExportGraph()

    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('shows error status when doRenameGraph fails', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicApi.patchGraph.mockRejectedValue({ response: { data: { detail: 'Rename failed' } } })

    wrapper.vm.openRenameGraph()
    wrapper.vm.renameGraphName = 'Updated Name'
    await wrapper.vm.doRenameGraph()

    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('shows error status when saveGraph fails', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicApi.saveGraph.mockRejectedValue({ response: { data: { detail: 'Save failed' } } })

    await wrapper.vm.saveGraph()

    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('shows error status when runGraph fails', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicApi.runGraph.mockRejectedValue({ response: { data: { detail: 'Run failed' } } })

    await wrapper.vm.runGraph()

    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('shows error status when doToggleEnabled fails', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicApi.patchGraph.mockRejectedValue({ response: { data: { detail: 'Toggle failed' } } })

    await wrapper.vm.doToggleEnabled()

    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })
})

describe('LogicView palette collapse', () => {
  it('initialises paletteCollapsed from localStorage and persists changes', async () => {
    const store = { logic_palette_collapsed: '1' }
    const storage = {
      getItem: vi.fn(k => store[k] ?? null),
      setItem: vi.fn((k, v) => { store[k] = v }),
      removeItem: vi.fn(),
      clear: vi.fn(),
    }
    Object.defineProperty(window,     'localStorage', { value: storage, configurable: true })
    Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })

    const { wrapper } = await mountLogicView({ isAdmin: true })

    expect(wrapper.vm.paletteCollapsed).toBe(true)

    wrapper.vm.paletteCollapsed = false
    await flushPromises()
    expect(storage.setItem).toHaveBeenCalledWith('logic_palette_collapsed', '0')
  })
})

describe('LogicView import edge cases', () => {
  it('opens rename dialog when imported graph name already exists', async () => {
    const graph = makeGraph('graph-1')
    const dup = makeGraph('graph-dup', { name: 'Main Graph' })
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      graphDetails: { 'graph-1': graph, 'graph-dup': dup },
    })

    logicApi.importGraph.mockResolvedValue({ data: dup })

    const file = new File([JSON.stringify({})], 'logic.json', { type: 'application/json' })
    await wrapper.vm.onImportFile({ target: { files: [file], value: 'logic.json' } })
    await flushPromises()

    expect(wrapper.vm.showRenameGraph).toBe(true)
    expect(wrapper.vm.renameGraphName).toBe('Main Graph')
  })

  it('shows error status when the import file contains invalid JSON', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: true })

    const badFile = new File(['not valid json {{'], 'logic.json', { type: 'application/json' })
    await wrapper.vm.onImportFile({ target: { files: [badFile], value: 'logic.json' } })
    await flushPromises()

    expect(wrapper.vm.statusMsg).toBeTruthy()
    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })
})
