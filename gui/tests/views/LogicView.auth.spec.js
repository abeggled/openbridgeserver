import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { AUTH_TOKEN_REFRESHED_EVENT } from '@/utils/authEvents'

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
    VueFlow: {
      name: 'VueFlow',
      props: ['snapToGrid', 'snapGrid'],
      template: '<div data-testid="vue-flow"><slot /></div>',
    },
    Handle: { template: '<span />' },
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    useVueFlow: () => ({ project: (point) => point }),
    addEdge: (edge, edges) => [...edges, edge],
  }))
  vi.doMock('@vue-flow/background', () => ({
    Background: {
      name: 'Background',
      props: ['gap', 'offset'],
      template: '<div />',
    },
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
    expect(wrapper.find('[data-testid="btn-debug"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-toggle-enabled"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-rename"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-duplicate"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-import"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-delete"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-export"]').exists()).toBe(true)

    wrapper.vm.toggleDebug()
    expect(wrapper.vm.debugMode).toBe(false)

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

  it('uses a monochrome bug symbol for the debug control', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    const icon = wrapper.find('[data-testid="icon-debug-bug"]')
    expect(icon.exists()).toBe(true)
    expect(icon.attributes('stroke')).toBe('currentColor')
    expect(wrapper.find('[data-testid="btn-debug"]').text()).not.toContain('🐞')
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

  it('runs a graph immediately when the preflight is fully allowed, without showing the popup', async () => {
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
    expect(logicApi.runGraph).toHaveBeenCalledWith('graph-1')
    expect(wrapper.find('[data-testid="run-preflight"]').exists()).toBe(false)
  })

  it('shows the popup and reports an error when the preflight request itself fails', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView({
      isAdmin: false,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicRunAuthzApi.preflight.mockRejectedValueOnce({ response: { data: { detail: 'preflight down' } } })

    await wrapper.get('[data-testid="btn-run"]').trigger('click')
    await flushPromises()

    expect(logicApi.runGraph).not.toHaveBeenCalled()
    expect(wrapper.vm.runPreflightError).toBe('preflight down')
    expect(wrapper.find('[data-testid="run-preflight"]').exists()).toBe(true)
  })

  it('runs the graph once a preflight already open in the dialog is confirmed', async () => {
    // Reachable defensively (component API / future partial-allow call
    // sites) rather than through today's UI — the dialog now only opens on
    // a denial, which also disables its confirm button — but confirmGraphRun
    // must still do the right thing if invoked with an approved snapshot.
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: false,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    wrapper.vm.preflightGraphId = 'graph-1'
    wrapper.vm.runPreflightItems = [{ id: 'x', label: 'x', allowed: true }]

    await wrapper.vm.confirmGraphRun()

    expect(logicApi.runGraph).toHaveBeenCalledWith('graph-1')
    expect(wrapper.vm.showRunPreflight).toBe(false)
    expect(wrapper.vm.preflightGraphId).toBe('')
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
    // Force the denied path so the popup stays open pending confirmation —
    // a fully-allowed preflight now runs immediately (see the fast-path
    // test above) and never leaves anything pending to discard.
    logicRunAuthzApi.preflight.mockResolvedValueOnce({
      data: {
        graph_id: 'graph-1',
        allowed: false,
        checks: [{ target_type: 'logic_capability', target_id: 'sms', node_ids: ['n2'], allowed: false, reason: 'missing_allow' }],
      },
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
    expect(wrapper.vm.lastRunOutputs.n1.value).toBe(42)
    expect(wrapper.vm.lastRunDebugOutputs.n1.value).toBe(42)
    expect(wrapper.vm.nodes[0].data._dbg).toBe('= 42')

    await wrapper.vm.saveGraph()
    const savedPayload = logicApi.saveGraph.mock.calls.at(-1)[1]
    expect(savedPayload.flow_data.nodes[0].data).not.toHaveProperty('_dbg')
    expect(savedPayload.flow_data.nodes[0].data).not.toHaveProperty('_dbg_title')

    await wrapper.vm.doToggleEnabled()
    expect(logicApi.patchGraph).toHaveBeenCalledWith('graph-1', { enabled: false })

    await wrapper.vm.doDuplicateGraph()
    expect(logicApi.duplicateGraph).toHaveBeenCalledWith('graph-1')
    expect(wrapper.vm.activeGraphId).toBe('graph-copy')
    expect(wrapper.vm.lastRunOutputs).toEqual({})

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

  it('snaps blocks to an adjustable grid and persists the browser preference', async () => {
    const graph = makeGraph('graph-1')
    localStorage.getItem.mockImplementation(key => ({
      'obs-logic-snap-to-grid': '1',
      'obs-logic-snap-grid-size': '35',
    })[key] ?? null)
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    expect(wrapper.findComponent({ name: 'VueFlow' }).props()).toMatchObject({
      snapToGrid: true,
      snapGrid: [35, 35],
    })
    expect(wrapper.findComponent({ name: 'Background' }).props()).toMatchObject({
      gap: 35,
      offset: 0.5,
    })

    await wrapper.find('[data-testid="btn-snap-to-grid"]').trigger('click')
    expect(localStorage.setItem).toHaveBeenCalledWith('obs-logic-snap-to-grid', '0')

    await wrapper.find('[data-testid="btn-snap-to-grid"]').trigger('click')
    await wrapper.find('[data-testid="input-snap-grid-size"]').setValue('45')
    await wrapper.find('[data-testid="input-snap-grid-size"]').trigger('change')

    expect(wrapper.findComponent({ name: 'VueFlow' }).props('snapGrid')).toEqual([45, 45])
    expect(wrapper.findComponent({ name: 'Background' }).props('gap')).toBe(45)
    expect(localStorage.setItem).toHaveBeenCalledWith('obs-logic-snap-grid-size', '45')
  })
})

// ── Grid visibility, independent of snapping (#1075) ───────────────────────

describe('LogicView grid visibility', () => {
  async function mountWithStorage(storage = {}, { isAdmin = true } = {}) {
    const graph = makeGraph('graph-1')
    localStorage.getItem.mockImplementation(key => storage[key] ?? null)
    return mountLogicView({
      isAdmin,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
  }

  it('shows the raster by default', async () => {
    const { wrapper } = await mountWithStorage()

    expect(wrapper.findComponent({ name: 'Background' }).exists()).toBe(true)
    expect(wrapper.find('[data-testid="btn-grid-visible"]').attributes('aria-pressed')).toBe('true')
  })

  it('keeps the raster hidden when the browser preference says so', async () => {
    const { wrapper } = await mountWithStorage({ 'obs-logic-grid-visible': '0' })

    expect(wrapper.findComponent({ name: 'Background' }).exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-grid-visible"]').attributes('aria-pressed')).toBe('false')
  })

  it('toggles the raster and persists the preference per browser', async () => {
    const { wrapper } = await mountWithStorage()

    await wrapper.find('[data-testid="btn-grid-visible"]').trigger('click')
    expect(wrapper.findComponent({ name: 'Background' }).exists()).toBe(false)
    expect(localStorage.setItem).toHaveBeenCalledWith('obs-logic-grid-visible', '0')

    await wrapper.find('[data-testid="btn-grid-visible"]').trigger('click')
    expect(wrapper.findComponent({ name: 'Background' }).exists()).toBe(true)
    expect(localStorage.setItem).toHaveBeenCalledWith('obs-logic-grid-visible', '1')
  })

  it('hides the raster without touching graph data or node coordinates', async () => {
    const { wrapper, logicApi } = await mountWithStorage()
    const before = JSON.parse(JSON.stringify(wrapper.vm.nodes))

    await wrapper.find('[data-testid="btn-grid-visible"]').trigger('click')

    expect(JSON.parse(JSON.stringify(wrapper.vm.nodes))).toEqual(before)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('leaves snapping untouched when the raster is hidden', async () => {
    const { wrapper } = await mountWithStorage({ 'obs-logic-snap-to-grid': '1' })

    await wrapper.find('[data-testid="btn-grid-visible"]').trigger('click')

    expect(wrapper.findComponent({ name: 'Background' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'VueFlow' }).props('snapToGrid')).toBe(true)
  })

  it('keeps the raster visible when snapping is switched off', async () => {
    const { wrapper } = await mountWithStorage({ 'obs-logic-snap-to-grid': '1' })

    await wrapper.find('[data-testid="btn-snap-to-grid"]').trigger('click')

    expect(wrapper.findComponent({ name: 'VueFlow' }).props('snapToGrid')).toBe(false)
    expect(wrapper.findComponent({ name: 'Background' }).exists()).toBe(true)
    expect(wrapper.find('[data-testid="input-snap-grid-size"]').exists()).toBe(true)
  })

  it('hides the grid size input once neither snapping nor the raster needs it', async () => {
    const { wrapper } = await mountWithStorage({ 'obs-logic-grid-visible': '0' })

    expect(wrapper.find('[data-testid="input-snap-grid-size"]').exists()).toBe(false)
  })

  it('offers the raster toggle without edit permissions but keeps snapping admin-only', async () => {
    const { wrapper } = await mountWithStorage({}, { isAdmin: false })

    expect(wrapper.find('[data-testid="btn-grid-visible"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="btn-snap-to-grid"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="input-snap-grid-size"]').exists()).toBe(false)

    await wrapper.find('[data-testid="btn-grid-visible"]').trigger('click')
    expect(wrapper.findComponent({ name: 'Background' }).exists()).toBe(false)
    expect(localStorage.setItem).toHaveBeenCalledWith('obs-logic-grid-visible', '0')
  })

  it('offers no raster toggle before a logic sheet is opened', async () => {
    localStorage.getItem.mockImplementation(() => null)
    const { wrapper } = await mountLogicView({ isAdmin: true })

    expect(wrapper.find('[data-testid="btn-grid-visible"]').exists()).toBe(false)
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

  it('applies inspector values from a logic_run WebSocket message when debug mode is on', async () => {
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

    wrapper.vm.toggleDebug()

    wsInstance.onmessage({ data: JSON.stringify({
      action: 'logic_run',
      graph_id: 'graph-1',
      outputs: { n1: { value: { nested: true }, changed: true } },
      inputs: { n1: { value: { incoming: 12, effective: 12, overridden: false } } },
    }) })
    expect(wrapper.vm.lastRunOutputs.n1.value).toEqual({ nested: true })
    expect(wrapper.vm.lastRunDebugOutputs.n1.value).toEqual({ nested: true })
    expect(wrapper.vm.lastRunInputs.n1.value.incoming).toBe(12)
    expect(wrapper.vm.nodes[0].data._dbg).toContain('[object Object]')
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

  it('reconnects with a refreshed token after close code 4001', async () => {
    let wsInstance = null
    let wsCreatedCount = 0
    const protocols = []
    global.WebSocket = class {
      constructor(_url, wsProtocols) {
        wsInstance = this
        this.close = vi.fn()
        wsCreatedCount++
        protocols.push(wsProtocols)
      }
    }
    overrideStorage({ access_token: 'tok' })

    const { wrapper } = await mountLogicView({ isAdmin: true })
    expect(wsCreatedCount).toBe(1)

    wsInstance.onclose({ code: 4001 })
    vi.advanceTimersByTime(4100)
    expect(wsCreatedCount).toBe(1)

    window.localStorage.getItem.mockImplementation(key => key === 'access_token' ? 'fresh-token' : null)
    const connectionsBeforeRefresh = wsCreatedCount
    window.dispatchEvent(new Event(AUTH_TOKEN_REFRESHED_EVENT))
    expect(wsCreatedCount).toBeGreaterThan(connectionsBeforeRefresh)
    expect(protocols.at(-1)).toEqual(['obs.jwt.fresh-token'])

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

  it('subscribes and unsubscribes the active graph over an open socket', async () => {
    let wsInstance = null
    global.WebSocket = class {
      static OPEN = 1
      constructor() { wsInstance = this; this.readyState = 1; this.send = vi.fn(); this.close = vi.fn() }
    }
    overrideStorage({ access_token: 'tok' })
    const graph = makeGraph('graph-1')
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wsInstance.onopen()
    wrapper.vm.toggleDebug()
    wrapper.vm.toggleDebug()

    expect(wsInstance.send).toHaveBeenCalledWith(JSON.stringify({ action: 'logic_debug', graph_id: 'graph-1', enabled: true }))
    expect(wsInstance.send).toHaveBeenCalledWith(JSON.stringify({ action: 'logic_debug', graph_id: 'graph-1', enabled: false }))
  })
})

describe('LogicView inspector inputs', () => {
  it('formats compact and full debug values across output types', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: true })
    const longError = 'x'.repeat(60)

    expect(wrapper.vm.fmtDebugVal({ __error__: null })).toContain('—')
    expect(wrapper.vm.fmtDebugVal({ __error__: 'short' })).toContain('short')
    expect(wrapper.vm.fmtDebugVal({ __error__: longError })).toContain('…')
    expect(wrapper.vm.fmtDebugVal({ __error__: longError }, { full: true, maxChars: 20 })).toContain(`${'x'.repeat(20)}…`)
    expect(wrapper.vm.fmtDebugVal({ value: null, changed: true })).toBe('= —')
    expect(wrapper.vm.fmtDebugVal({ value: true, changed: true })).toBe('= ✓')
    expect(wrapper.vm.fmtDebugVal({ value: false, changed: true })).toBe('= ✗')
    expect(wrapper.vm.fmtDebugVal({ _message: null })).toBe('—')
    expect(wrapper.vm.fmtDebugVal({ _message: 'sent', sent: true })).toBe('"sent"  sent=✓')
    expect(wrapper.vm.fmtDebugVal({ _write_value: 12 })).toBe('→ 12')
  })

  it('expands dynamic ports and keeps connected custom handles', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wrapper.vm.selectedNode = { id: 'gate', type: 'and', data: { input_count: 3 } }
    expect(wrapper.vm.debugInputs.map(input => input.id)).toEqual(['in1', 'in2', 'in3'])

    wrapper.vm.selectedNode = { id: 'average', type: 'avg_multi', data: { input_count: 4 } }
    expect(wrapper.vm.debugInputs.map(input => input.id)).toEqual(['in_1', 'in_2', 'in_3', 'in_4'])

    wrapper.vm.selectedNode = { id: 'concat', type: 'string_concat', data: { count: 3 } }
    wrapper.vm.edges = [{ id: 'custom', source: 'n1', target: 'concat', sourceHandle: 'value', targetHandle: 'in_4' }]
    expect(wrapper.vm.debugInputs.map(input => input.id)).toEqual(['in_1', 'in_2', 'in_3', 'in_4'])

    // Without a configured count the block falls back to its two inputs, and an
    // edge without an explicit handle targets the default 'in' port.
    wrapper.vm.selectedNode = { id: 'concat', type: 'string_concat', data: {} }
    wrapper.vm.edges = [{ id: 'default-handle', source: 'n1', target: 'concat', sourceHandle: 'out' }]
    wrapper.vm.lastRunDebugOutputs = { n1: { out: 'from default handle' } }
    expect(wrapper.vm.debugInputs.map(input => input.id)).toEqual(['in_1', 'in_2', 'in'])
    expect(wrapper.vm.debugInputs.find(input => input.id === 'in').incoming).toBe('from default handle')
    wrapper.vm.lastRunDebugOutputs = {}

    wrapper.vm.selectedNode = { id: 'source', type: 'datapoint_read', data: {} }
    wrapper.vm.edges = []
    wrapper.vm.lastRunInputs = {
      source: { value: { incoming: 23, effective: 99, overridden: true } },
    }
    expect(wrapper.vm.debugInputs).toEqual([
      expect.objectContaining({
        id: 'value',
        incoming: 23,
        effective: 99,
        capturedOverridden: true,
        locallyOverridden: false,
        overridden: true,
      }),
    ])

    wrapper.vm.selectedNode = { id: 'script', type: 'python_script', data: {} }
    wrapper.vm.lastRunInputs = {}
    expect(wrapper.vm.debugInputs.map(input => input.id)).toEqual(['a', 'b', 'c'])

    wrapper.vm.selectedNode = { id: 'gate', type: 'and', data: { input_count: 2 } }
    wrapper.vm.edges = [{ id: 'stale', source: 'source', target: 'gate', sourceHandle: 'out', targetHandle: 'in1' }]
    wrapper.vm.lastRunOutputs = { source: { out: 'ordinary-run' } }
    wrapper.vm.lastRunDebugOutputs = {}
    expect(wrapper.vm.debugInputs.find(input => input.id === 'in1').incoming).toBeUndefined()
    wrapper.vm.lastRunDebugOutputs = { source: { out: 'debug-run' } }
    expect(wrapper.vm.debugInputs.find(input => input.id === 'in1').incoming).toBe('debug-run')

    wrapper.vm.selectedNode = null
    expect(wrapper.vm.debugInputs).toEqual([])
  })

  it('edits, parses, runs, and clears temporary input overrides', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    logicApi.runGraph.mockResolvedValueOnce({
      data: {
        outputs: { n1: { value: 7 } },
        debug: { inputs: { n1: { value: { incoming: null, effective: 7, overridden: true } } } },
      },
    })

    wrapper.vm.toggleDebug()
    wrapper.vm.onNodeClick({ node: wrapper.vm.nodes[0] })
    expect(wrapper.vm.selectedNode.id).toBe('n1')

    wrapper.vm.setDebugOverride('value', '{"nested":true}')
    wrapper.vm.setDebugOverride('label', 'plain text')
    await wrapper.vm.runGraph()
    expect(logicApi.runGraph).toHaveBeenCalledWith('graph-1', {
      debug: true,
      input_overrides: { n1: { value: { nested: true }, label: 'plain text' } },
    })
    expect(wrapper.vm.lastRunInputs.n1.value.incoming).toBe(null)
    expect(wrapper.vm.lastRunDebugOutputs.n1.value).toBe(7)

    wrapper.vm.clearDebugOverride('value')
    expect(wrapper.vm.debugOverrides.n1.value).toBeUndefined()
    wrapper.vm.setDebugOverride('label', '   ')
    wrapper.vm.clearAllDebugOverrides()
    wrapper.vm.toggleDebug()
    expect(wrapper.vm.lastRunMetadata).toBe(null)
    expect(wrapper.vm.lastRunDebugOutputs).toEqual({})
    expect(wrapper.vm.lastRunOutputs.n1.value).toBe(7)

    wrapper.vm.toggleDebug()
    wrapper.vm.onNodeClick({ node: wrapper.vm.nodes[0] })
    await wrapper.vm.$nextTick()
    const panel = wrapper.findComponent({ name: 'NodeConfigPanel' })
    expect(panel.props('debugMode')).toBe(true)
    expect(panel.props('debugOutputs')).toEqual({})
  })

  it('ignores override edits without edit permission', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: false })

    wrapper.vm.setDebugOverride('value', '42')

    expect(wrapper.vm.debugOverrides).toEqual({})
  })

  it('keeps the selected block editable while debug mode is on (issue #1128)', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wrapper.vm.toggleDebug()
    wrapper.vm.onNodeClick({ node: wrapper.vm.nodes[0] })
    await wrapper.vm.$nextTick()

    // One panel serves both tabs: it stays open on the same block and carries
    // that block's debug data alongside its settings.
    const panel = wrapper.findComponent({ name: 'NodeConfigPanel' })
    expect(panel.exists()).toBe(true)
    expect(panel.props('node').id).toBe('n1')
    expect(panel.props('debugMode')).toBe(true)
    wrapper.vm.lastRunInputs = { n1: { value: { incoming: 5, effective: 5, overridden: false } } }
    await wrapper.vm.$nextTick()
    expect(panel.props('debugInputs').map(input => input.id)).toEqual(['value'])

    wrapper.vm.onNodeDataUpdate({ label: 'renamed in debug mode' })
    vi.advanceTimersByTime(500)
    await flushPromises()

    expect(wrapper.vm.nodes[0].data.label).toBe('renamed in debug mode')
    expect(logicApi.saveGraph).toHaveBeenCalled()
    expect(wrapper.vm.debugMode).toBe(true)
  })

  it('ignores debug state from a run completed after debug mode is disabled', async () => {
    const graph = makeGraph('graph-1')
    const { wrapper, logicApi } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })
    let resolveRun
    logicApi.runGraph.mockReturnValueOnce(new Promise(resolve => { resolveRun = resolve }))

    wrapper.vm.toggleDebug()
    const pendingRun = wrapper.vm.runGraph()
    wrapper.vm.toggleDebug()
    wrapper.vm.toggleDebug()
    resolveRun({
      data: {
        outputs: { n1: { value: 9 } },
        debug: {
          inputs: { n1: { value: { incoming: 1, effective: 9, overridden: true } } },
          timestamp: '2026-07-29T05:00:00Z',
          used_overrides: true,
        },
      },
    })
    await pendingRun

    expect(wrapper.vm.lastRunInputs).toEqual({})
    expect(wrapper.vm.lastRunMetadata).toBe(null)
    expect(wrapper.vm.lastRunDebugOutputs).toEqual({})
    expect(wrapper.vm.lastRunOutputs.n1.value).toBe(9)
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
    expect(wrapper.vm.lastRunOutputs.a.__diagnostic__).toBe('graph_cycle')
    expect(wrapper.vm.lastRunOutputs.b.__diagnostic__).toBe('graph_cycle')
    expect(wrapper.vm.nodes.find(node => node.id === 'a').data._dbg).toContain(wrapper.vm.lastRunOutputs.a.__error__.slice(0, 20))
    expect(wrapper.vm.nodes.find(node => node.id === 'b').data._dbg).toContain(wrapper.vm.lastRunOutputs.b.__error__.slice(0, 20))
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
    expect(wrapper.vm.lastRunOutputs.n1.__diagnostic__).toBe('graph_cycle')
    expect(wrapper.vm.nodes[0].data._dbg).toContain('Graph cycle detected')
  })
})

describe('LogicView duplicate target handle validation (#1116)', () => {
  it('blocks connecting a second edge onto an already-connected input handle', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'a', type: 'const_value', position: { x: 0, y: 0 }, data: {} },
          { id: 'b', type: 'const_value', position: { x: 0, y: 160 }, data: {} },
          { id: 'c', type: 'datapoint_write', position: { x: 160, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'a-c', source: 'a', target: 'c', sourceHandle: 'value', targetHandle: 'value' },
        ],
      },
    })
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wrapper.vm.onConnect({ source: 'b', target: 'c', sourceHandle: 'value', targetHandle: 'value' })

    expect(wrapper.vm.edges).toHaveLength(1)
    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('allows two edges that target different handles of the same node', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'a', type: 'const_value', position: { x: 0, y: 0 }, data: {} },
          { id: 'b', type: 'const_value', position: { x: 0, y: 160 }, data: {} },
          { id: 'c', type: 'compare', position: { x: 160, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'a-c', source: 'a', target: 'c', sourceHandle: 'value', targetHandle: 'in1' },
        ],
      },
    })
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    wrapper.vm.onConnect({ source: 'b', target: 'c', sourceHandle: 'value', targetHandle: 'in2' })

    expect(wrapper.vm.edges).toHaveLength(2)
    expect(wrapper.vm.validationWarnings).toEqual([])
  })

  it('detects a duplicate on the default "in" handle when edges omit targetHandle', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'a', type: 'const_value', position: { x: 0, y: 0 }, data: {} },
          { id: 'b', type: 'const_value', position: { x: 0, y: 160 }, data: {} },
          { id: 'c', type: 'not', position: { x: 160, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'a-c', source: 'a', target: 'c', sourceHandle: 'value' },
          { id: 'b-c', source: 'b', target: 'c', sourceHandle: 'value' },
        ],
      },
    })
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    expect(wrapper.vm.validationWarnings).toEqual([
      { node_id: 'c', code: 'duplicate_target_handle', message: expect.stringContaining('c.in') },
    ])
  })

  it('blocks saving a graph with two edges on the same input handle and marks the target node', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'a', type: 'const_value', position: { x: 0, y: 0 }, data: {} },
          { id: 'b', type: 'const_value', position: { x: 0, y: 160 }, data: {} },
          { id: 'c', type: 'datapoint_write', position: { x: 160, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'a-c', source: 'a', target: 'c', sourceHandle: 'value', targetHandle: 'value' },
          { id: 'b-c', source: 'b', target: 'c', sourceHandle: 'value', targetHandle: 'value' },
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
    expect(wrapper.vm.validationWarnings).toEqual([
      { node_id: 'c', code: 'duplicate_target_handle', message: expect.stringContaining('c.value') },
    ])
    expect(wrapper.vm.lastRunOutputs.c.__diagnostic__).toBe('duplicate_target_handle')
    expect(wrapper.vm.nodes.find(n => n.id === 'c').data._dbg).toBeTruthy()
  })

  it('reports the duplicate-handle warning on the live status bar for an already-saved graph', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'a', type: 'const_value', position: { x: 0, y: 0 }, data: {} },
          { id: 'b', type: 'const_value', position: { x: 0, y: 160 }, data: {} },
          { id: 'c', type: 'datapoint_write', position: { x: 160, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'a-c', source: 'a', target: 'c', sourceHandle: 'value', targetHandle: 'value' },
          { id: 'b-c', source: 'b', target: 'c', sourceHandle: 'value', targetHandle: 'value' },
        ],
      },
    })
    const { wrapper } = await mountLogicView({
      isAdmin: true,
      graphs: [graph],
      routeQuery: { graph: 'graph-1' },
      graphDetails: { 'graph-1': graph },
    })

    expect(wrapper.vm.validationWarnings).toHaveLength(1)
    expect(wrapper.find('[data-testid="status-msg"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Mehrfachverbindung')
  })

  it('scopes the reported count to duplicate-handle warnings when a cycle warning is also present', async () => {
    const graph = makeGraph('graph-1', {
      flow_data: {
        nodes: [
          { id: 'a', type: 'not', position: { x: 0, y: 0 }, data: {} },
          { id: 'b', type: 'not', position: { x: 160, y: 0 }, data: {} },
          { id: 'c', type: 'const_value', position: { x: 0, y: 160 }, data: {} },
          { id: 'd', type: 'const_value', position: { x: 0, y: 320 }, data: {} },
          { id: 'e', type: 'datapoint_write', position: { x: 320, y: 160 }, data: {} },
        ],
        edges: [
          { id: 'a-b', source: 'a', target: 'b', sourceHandle: 'out', targetHandle: 'in1' },
          { id: 'b-a', source: 'b', target: 'a', sourceHandle: 'out', targetHandle: 'in1' },
          { id: 'c-e', source: 'c', target: 'e', sourceHandle: 'value', targetHandle: 'value' },
          { id: 'd-e', source: 'd', target: 'e', sourceHandle: 'value', targetHandle: 'value' },
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
    // Two cycle warnings (a, b) plus one duplicate-handle warning (e) — the
    // duplicate-handle message must report "1", not the combined total "3".
    expect(wrapper.vm.validationWarnings).toHaveLength(3)
    expect(wrapper.vm.statusMsg.text).toContain('1 Eingang')
    expect(wrapper.vm.statusMsg.text).not.toContain('3 Eingang')
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

  it('titleSpacerClass matches the expanded NodePalette width for admins', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: true })
    wrapper.vm.paletteCollapsed = false
    await flushPromises()
    expect(wrapper.vm.titleSpacerClass).toBe('w-52')
  })

  it('titleSpacerClass shrinks to the collapsed NodePalette width for admins', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: true })
    wrapper.vm.paletteCollapsed = true
    await flushPromises()
    expect(wrapper.vm.titleSpacerClass).toBe('w-4')
  })

  it('titleSpacerClass reserves no space for non-admins, who have no NodePalette', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: false })
    expect(wrapper.vm.titleSpacerClass).toBe('w-0')
  })

  it('clips the title text instead of letting it overflow the narrowed spacer', async () => {
    // Regression: shrinking titleSpacerClass to w-4/w-0 only narrows the
    // box — without overflow-hidden the title text still paints its full
    // width by default and visually overlaps the graph-select dropdown
    // laid out right after it.
    const { wrapper } = await mountLogicView({ isAdmin: true })
    wrapper.vm.paletteCollapsed = true
    await flushPromises()
    const classes = wrapper.find('h2').classes()
    expect(classes).toContain('overflow-hidden')
    expect(classes).toContain('whitespace-nowrap')
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
