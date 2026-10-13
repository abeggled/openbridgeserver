// Auto-save of connection changes in the logic editor (issue #1333): creating,
// removing and re-wiring an edge is persisted after a short debounce, the same
// way block configuration changes already were.
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
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
  vi.doMock('@vue-flow/core', () => ({
    VueFlow: {
      name: 'VueFlow',
      props: ['snapToGrid', 'snapGrid'],
      emits: ['connect', 'edgeUpdate', 'edgesChange', 'nodesChange'],
      template: '<div data-testid="vue-flow"><slot /></div>',
    },
    Handle: { template: '<span />' },
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    useVueFlow: () => ({ project: (point) => point }),
    // Mirrors the real addEdge: it pushes into the array it is given and
    // returns that same array unchanged for an already existing connection.
    addEdge: (edge, edges) => {
      const exists = edges.some(e => e.source === edge.source && e.target === edge.target
        && e.sourceHandle === edge.sourceHandle && e.targetHandle === edge.targetHandle)
      if (!exists) edges.push({ id: `e-${edge.source}-${edge.target}-${edge.targetHandle}`, ...edge })
      return edges
    },
  }))
  vi.doMock('@vue-flow/background', () => ({ Background: { name: 'Background', props: ['gap', 'offset'], template: '<div />' } }))
  vi.doMock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))
  vi.doMock('@vue-flow/minimap', () => ({ MiniMap: { template: '<div />' } }))
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

function makeGraph(id, edges = [{ id: 'e1', source: 'n1', target: 'n2', sourceHandle: 'out', targetHandle: 'a' }]) {
  return {
    id,
    name: `Graph ${id}`,
    description: '',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'n1', type: 'const_value', position: { x: 0, y: 0 }, data: { value: 1 } },
        { id: 'n2', type: 'const_value', position: { x: 100, y: 0 }, data: { value: 2 } },
        { id: 'n3', type: 'const_value', position: { x: 200, y: 0 }, data: { value: 3 } },
      ],
      edges,
    },
  }
}

const GRAPH_A = makeGraph('graph-a')
const GRAPH_B = makeGraph('graph-b', [])

async function mountLogicView({ isAdmin = true, saveGraph } = {}) {
  vi.doMock('vue-router', () => ({
    useRoute: () => ({ query: { graph: 'graph-a' } }),
    useRouter: () => ({ push: vi.fn() }),
  }))
  const details = { 'graph-a': GRAPH_A, 'graph-b': GRAPH_B }
  const logicApi = {
    nodeTypes: vi.fn().mockResolvedValue({ data: [{ type: 'const_value', config_schema: {} }] }),
    listGraphs: vi.fn().mockResolvedValue({ data: [GRAPH_A, GRAPH_B] }),
    getGraph: vi.fn().mockImplementation(id => Promise.resolve({ data: structuredClone(details[id]) })),
    saveGraph: saveGraph ?? vi.fn().mockImplementation((id, payload) => Promise.resolve({ data: { id, ...payload } })),
    deleteGraph: vi.fn().mockResolvedValue({}),
    // Stateful like the server: a rename must not undo an earlier toggle.
    patchGraph: vi.fn().mockImplementation((id, payload) => {
      details[id] = { ...details[id], ...payload }
      return Promise.resolve({ data: structuredClone(details[id]) })
    }),
    exportGraph: vi.fn().mockResolvedValue({ data: {} }),
    duplicateGraph: vi.fn().mockResolvedValue({ data: makeGraph('graph-copy') }),
  }
  const logicRunAuthzApi = { preflight: vi.fn().mockRejectedValue(new Error('not stubbed')) }
  vi.doMock('@/api/client', () => ({ logicApi, hierarchyApi: {} }))
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
        ActionPreflightDialog: true,
        GraphPickerModal: true,
        Modal: { template: '<div><slot /><slot name="footer" /></div>' },
        ConfirmDialog: true,
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  const { useLogicStore } = await import('@/stores/logic')
  return { wrapper, logicApi, logicRunAuthzApi, store: useLogicStore() }
}

const savedEdges = (logicApi, call = 0) => logicApi.saveGraph.mock.calls[call][1].flow_data.edges

async function elapse(ms) {
  vi.advanceTimersByTime(ms)
  await flushPromises()
}

describe('LogicView auto-saves connection changes (#1333)', () => {
  it('saves a new connection once the debounce elapsed, not before', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.findComponent({ name: 'VueFlow' }).vm.$emit('connect', { source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(499)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()

    await elapse(1)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(logicApi.saveGraph.mock.calls[0][0]).toBe('graph-a')
    expect(savedEdges(logicApi)).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'n2', target: 'n3', targetHandle: 'a' }),
    ]))
    expect(wrapper.vm.statusMsg).toEqual(expect.objectContaining({ ok: true }))
  })

  it('bundles changes made in quick succession into one save', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(300)
    wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' })
    await elapse(300)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()

    await elapse(200)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(savedEdges(logicApi)).toHaveLength(3)
  })

  it('neither keeps nor saves a connection the graph validation rejects', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    // n2 → n1 closes a cycle with the existing n1 → n2.
    wrapper.vm.onConnect({ source: 'n2', target: 'n1', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(1000)

    expect(wrapper.vm.edges).toEqual(GRAPH_A.flow_data.edges)
    expect(wrapper.vm.statusMsg).toEqual(expect.objectContaining({ ok: false }))
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('does not save when the connection already exists', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.vm.onConnect({ source: 'n1', target: 'n2', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(1000)

    expect(wrapper.vm.edges).toHaveLength(1)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('saves after an edge was removed, but not after a mere selection change', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    const flow = wrapper.findComponent({ name: 'VueFlow' })

    flow.vm.$emit('edgesChange', [{ type: 'select', id: 'e1', selected: true }])
    await elapse(1000)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()

    // Vue Flow applies the removal to the v-model itself before the save fires.
    wrapper.vm.edges = []
    flow.vm.$emit('edgesChange', [{ type: 'remove', id: 'e1' }])
    await elapse(500)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(savedEdges(logicApi)).toEqual([])
  })

  it('re-wires and saves an edge whose end was dragged to another handle', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    const [edge, other] = wrapper.vm.edges

    wrapper.findComponent({ name: 'VueFlow' }).vm.$emit('edgeUpdate', {
      edge,
      connection: { source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' },
    })
    // The id follows the new connection, as with Vue Flow's own updateEdge.
    expect(wrapper.vm.edges).toEqual([
      expect.objectContaining({ id: 'vueflow__edge-n1out-n3b', source: 'n1', target: 'n3', targetHandle: 'b' }),
      other,
    ])

    await elapse(500)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(savedEdges(logicApi)).toEqual([
      expect.objectContaining({ id: 'vueflow__edge-n1out-n3b', target: 'n3', targetHandle: 'b' }),
      expect.objectContaining({ source: 'n2', target: 'n3', targetHandle: 'a' }),
    ])
  })

  it.each([
    ['source', { source: 'n3' }],
    ['target', { target: 'n3' }],
    ['sourceHandle', { sourceHandle: 'out2' }],
    ['targetHandle', { targetHandle: 'b' }],
  ])('treats a changed %s as a re-wire', async (_field, change) => {
    const { wrapper, logicApi } = await mountLogicView()
    const edge = wrapper.vm.edges[0]
    const connection = { source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle, targetHandle: edge.targetHandle, ...change }

    wrapper.vm.onEdgeUpdate({ edge, connection })
    await elapse(500)

    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(savedEdges(logicApi)[0]).toEqual(expect.objectContaining(change))
  })

  it('ignores re-wires that change nothing, lack an end, or fail validation', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    const edge = wrapper.vm.edges[0]

    wrapper.vm.onEdgeUpdate({ edge, connection: { source: 'n1', target: 'n2', sourceHandle: 'out', targetHandle: 'a' } })
    wrapper.vm.onEdgeUpdate({ edge })
    wrapper.vm.onEdgeUpdate({ edge, connection: { source: 'n1', target: null } })
    wrapper.vm.onEdgeUpdate({ edge, connection: { source: null, target: 'n2' } })
    // n2 → n2 would be a self loop.
    wrapper.vm.onEdgeUpdate({ edge, connection: { source: 'n2', target: 'n2', sourceHandle: 'out', targetHandle: 'a' } })
    await elapse(1000)

    expect(wrapper.vm.edges).toEqual(GRAPH_A.flow_data.edges)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('never saves for a read-only user', async () => {
    const { wrapper, logicApi } = await mountLogicView({ isAdmin: false })
    const edge = wrapper.vm.edges[0]

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    wrapper.vm.onEdgeUpdate({ edge, connection: { source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'a' } })
    wrapper.vm.onElementsChange([{ type: 'remove', id: 'e1' }])
    wrapper.unmount()
    await elapse(1000)

    expect(wrapper.vm.edges).toEqual(GRAPH_A.flow_data.edges)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('reports a failed auto-save instead of showing it as saved', async () => {
    const saveGraph = vi.fn()
      .mockRejectedValueOnce({ response: { data: { detail: 'disk full' } } })
      .mockRejectedValueOnce(new Error('network'))
    const { wrapper } = await mountLogicView({ saveGraph })

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(500)
    expect(wrapper.vm.statusMsg).toEqual({ ok: false, text: 'disk full' })

    wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' })
    await elapse(500)
    expect(wrapper.vm.statusMsg.ok).toBe(false)
    expect(wrapper.vm.statusMsg.text).not.toBe('disk full')
    expect(saveGraph).toHaveBeenCalledTimes(2)
  })

  it('saves a pending change into the previous sheet when switching sheets', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    wrapper.vm.onGraphPicked('graph-b')
    await flushPromises()

    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(logicApi.saveGraph.mock.calls[0][0]).toBe('graph-a')
    expect(savedEdges(logicApi)).toHaveLength(2)
    expect(wrapper.vm.edges).toEqual([])

    await elapse(1000)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
  })

  it('does not auto-save while a sheet switch is still loading', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    let resolveLoad
    logicApi.getGraph.mockImplementationOnce(() => new Promise(resolve => { resolveLoad = resolve }))

    wrapper.vm.onGraphPicked('graph-b')
    expect(wrapper.vm.graphLoading).toBe(true)
    // The canvas still shows graph-a while activeGraphId already names graph-b.
    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(1000)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()

    resolveLoad({ data: structuredClone(GRAPH_B) })
    await flushPromises()
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('drops a pending auto-save of a sheet that is deleted', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await wrapper.vm.doDeleteGraph()
    await elapse(1000)

    expect(logicApi.deleteGraph).toHaveBeenCalledWith('graph-a')
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('drops a pending auto-save of a sheet deleted from the graph picker', async () => {
    const { wrapper, logicApi, store } = await mountLogicView()

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    store.graphs = store.graphs.filter(g => g.id !== 'graph-a')
    wrapper.vm.onGraphDeletedFromPicker('graph-a')
    await elapse(1000)

    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('reports a save into a sheet that vanished from the list', async () => {
    const { wrapper, logicApi, store } = await mountLogicView()

    store.graphs = store.graphs.filter(g => g.id !== 'graph-a')
    await wrapper.vm.saveGraph()

    expect(logicApi.saveGraph).not.toHaveBeenCalled()
    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('saves a pending change when the editor is left, and nothing otherwise', async () => {
    const first = await mountLogicView()
    first.wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    first.wrapper.unmount()
    await flushPromises()
    expect(first.logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(savedEdges(first.logicApi)).toHaveLength(2)

    vi.resetModules()
    const second = await mountLogicView()
    second.wrapper.unmount()
    await elapse(1000)
    expect(second.logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('lets a manual save supersede a pending auto-save', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await wrapper.find('[data-testid="btn-save"]').trigger('click')
    await elapse(1000)

    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(savedEdges(logicApi)).toHaveLength(2)
  })

  it('runs saves one after another so an older payload cannot land last', async () => {
    const pending = []
    const saveGraph = vi.fn().mockImplementation((id, payload) => new Promise(resolve => {
      pending.push(() => resolve({ data: { id, ...payload } }))
    }))
    const { wrapper } = await mountLogicView({ saveGraph })

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(500)
    expect(saveGraph).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.saving).toBe(true)

    wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' })
    await elapse(500)
    expect(saveGraph).toHaveBeenCalledTimes(1)

    pending[0]()
    await flushPromises()
    expect(saveGraph).toHaveBeenCalledTimes(2)
    expect(saveGraph.mock.calls[1][1].flow_data.edges).toHaveLength(3)

    pending[1]()
    await flushPromises()
    expect(wrapper.vm.saving).toBe(false)
  })

  it('auto-saves block configuration changes through the same debounce', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.vm.onNodeClick({ node: wrapper.vm.nodes[0] })
    wrapper.vm.onNodeDataUpdate({ value: 7 })
    await elapse(300)
    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(300)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()

    await elapse(200)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    const flowData = logicApi.saveGraph.mock.calls[0][1].flow_data
    expect(flowData.nodes[0].data.value).toBe(7)
    expect(flowData.edges).toHaveLength(2)
  })

  it('gives a re-wired edge a fresh id, so drawing the old connection again does not collide', async () => {
    const { wrapper } = await mountLogicView()

    wrapper.vm.onEdgeUpdate({ edge: wrapper.vm.edges[0], connection: { source: 'n1', target: 'n3', sourceHandle: null, targetHandle: null } })
    expect(wrapper.vm.edges[0].id).toBe('vueflow__edge-n1-n3')

    wrapper.vm.onConnect({ source: 'n1', target: 'n2', sourceHandle: 'out', targetHandle: 'a' })
    const ids = wrapper.vm.edges.map(e => e.id)
    expect(ids).toHaveLength(2)
    expect(new Set(ids).size).toBe(2)
  })

  it('treats a missing handle and null as the same port', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.edges = [{ id: 'e1', source: 'n1', target: 'n2' }]

    wrapper.vm.onEdgeUpdate({ edge: wrapper.vm.edges[0], connection: { source: 'n1', target: 'n2', sourceHandle: null, targetHandle: null } })
    wrapper.vm.onConnect({ source: 'n1', target: 'n2', sourceHandle: null, targetHandle: null })
    await elapse(1000)

    expect(wrapper.vm.edges).toEqual([{ id: 'e1', source: 'n1', target: 'n2' }])
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('saves after a block was removed, but not after it was merely moved', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    const flow = wrapper.findComponent({ name: 'VueFlow' })

    flow.vm.$emit('nodesChange', [{ type: 'position', id: 'n3', position: { x: 1, y: 1 } }])
    await elapse(1000)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()

    wrapper.vm.nodes = wrapper.vm.nodes.filter(n => n.id !== 'n3')
    flow.vm.$emit('nodesChange', [{ type: 'remove', id: 'n3' }])
    await elapse(500)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(logicApi.saveGraph.mock.calls[0][1].flow_data.nodes).toHaveLength(2)
  })

  it('saves blocks dropped from the palette or pasted', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }) }
    wrapper.vm.onDrop({ dataTransfer: { getData: () => 'const_value' }, clientX: 5, clientY: 5 })
    await elapse(500)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(logicApi.saveGraph.mock.calls[0][1].flow_data.nodes).toHaveLength(4)

    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: ['n1', 'n2'].includes(n.id) }))
    wrapper.vm.copySelection()
    wrapper.vm.pasteClipboard()
    await elapse(500)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(2)
    const flowData = logicApi.saveGraph.mock.calls[1][1].flow_data
    expect(flowData.nodes).toHaveLength(6)
    expect(flowData.edges).toHaveLength(2)
  })

  it('persists a pending change before the sheet is run, exported or duplicated', async () => {
    const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView()
    const order = []
    logicApi.saveGraph.mockImplementation((id, payload) => { order.push('save'); return Promise.resolve({ data: { id, ...payload } }) })
    logicRunAuthzApi.preflight.mockImplementation(() => { order.push('run'); return Promise.reject(new Error('stop')) })
    logicApi.exportGraph.mockImplementation(() => { order.push('export'); return Promise.reject(new Error('stop')) })
    logicApi.duplicateGraph.mockImplementation(() => { order.push('duplicate'); return Promise.reject(new Error('stop')) })

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await wrapper.vm.requestGraphRun()
    wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' })
    await wrapper.vm.doExportGraph()
    wrapper.vm.onElementsChange([{ type: 'remove', id: 'e1' }])
    wrapper.vm.duplicateName = 'Copy'
    await wrapper.vm.doDuplicate()

    expect(order).toEqual(['save', 'run', 'save', 'export', 'save', 'duplicate'])
  })

  it('persists a pending change before re-loading the sheet that is already open', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    const order = []
    logicApi.saveGraph.mockImplementation((id, payload) => { order.push(`save:${id}`); return Promise.resolve({ data: { id, ...payload } }) })
    logicApi.getGraph.mockImplementation(id => { order.push(`get:${id}`); return Promise.resolve({ data: structuredClone(GRAPH_A) }) })

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    wrapper.vm.onGraphPicked('graph-a')
    await flushPromises()

    expect(order).toEqual(['save:graph-a', 'get:graph-a'])
  })

  it('keeps the pending change if deleting the sheet fails', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    logicApi.deleteGraph.mockRejectedValue(new Error('forbidden'))

    await expect(wrapper.vm.doDeleteGraph()).rejects.toThrow('forbidden')
    await elapse(1000)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await expect(wrapper.vm.doDeleteGraph()).rejects.toThrow('forbidden')
    await elapse(500)
    expect(wrapper.vm.activeGraphId).toBe('graph-a')
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(savedEdges(logicApi)).toHaveLength(2)
  })

  it('queues toggle and rename behind an in-flight save', async () => {
    let releaseSave
    const saveGraph = vi.fn().mockImplementation((id, payload) => new Promise(resolve => {
      releaseSave = () => resolve({ data: { id, ...payload } })
    }))
    const { wrapper, logicApi } = await mountLogicView({ saveGraph })

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await elapse(500)
    wrapper.vm.doToggleEnabled()
    wrapper.vm.renameGraphName = 'Renamed'
    wrapper.vm.doRenameGraph()
    await flushPromises()
    expect(logicApi.patchGraph).not.toHaveBeenCalled()

    releaseSave()
    await flushPromises()
    expect(logicApi.patchGraph).toHaveBeenCalledTimes(2)
    expect(logicApi.patchGraph).toHaveBeenNthCalledWith(1, 'graph-a', { enabled: false })

    // Toggling back reads the state the first toggle left in the store.
    await wrapper.vm.doToggleEnabled()
    expect(logicApi.patchGraph).toHaveBeenLastCalledWith('graph-a', { enabled: true })
    expect(wrapper.vm.statusMsg.ok).toBe(true)
  })
})
