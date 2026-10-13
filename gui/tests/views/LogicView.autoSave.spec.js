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

// Mounted views keep window listeners (beforeunload) until unmounted.
const mounted = []

afterEach(() => {
  // Some tests unmount on purpose; a second unmount is harmless to skip.
  for (const wrapper of mounted.splice(0)) {
    try { wrapper.unmount() } catch { /* already unmounted */ }
  }
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

async function mountLogicView({ isAdmin = true, saveGraph, pinia = createPinia() } = {}) {
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
    createGraph: vi.fn().mockResolvedValue({ data: { id: 'graph-new', name: 'New', description: '', enabled: true, flow_data: { nodes: [], edges: [] } } }),
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
  mounted.push(wrapper)
  await flushPromises()
  const { useLogicStore } = await import('@/stores/logic')
  return { wrapper, logicApi, logicRunAuthzApi, store: useLogicStore(), pinia }
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
    await wrapper.vm.doCreateGraph()
    expect(logicApi.createGraph).not.toHaveBeenCalled()
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

  it('keeps the canvas read-only while a sheet loads', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    let resolveLoad
    logicApi.getGraph.mockImplementationOnce(() => new Promise(resolve => { resolveLoad = resolve }))
    const flow = wrapper.findComponent({ name: 'VueFlow' })
    expect(flow.attributes('nodes-connectable')).toBe('true')

    wrapper.vm.onGraphPicked('graph-b')
    await flushPromises()
    expect(wrapper.vm.canvasEditable).toBe(false)
    expect(flow.attributes('nodes-connectable')).toBe('false')
    expect(flow.attributes('edges-updatable')).toBe('false')
    expect(flow.attributes('nodes-draggable')).toBe('false')
    expect(flow.attributes('delete-key-code')).toBe('')
    // The canvas still shows graph-a; none of these may change it.
    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    wrapper.vm.onEdgeUpdate({ edge: wrapper.vm.edges[0], connection: { source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'a' } })
    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ left: 0, top: 0 }) }
    wrapper.vm.onDrop({ dataTransfer: { getData: () => 'const_value' }, clientX: 5, clientY: 5 })
    wrapper.vm.selectedNode = wrapper.vm.nodes[0]
    wrapper.vm.onNodeDataUpdate({ value: 9 })
    expect(wrapper.vm.edges).toEqual(GRAPH_A.flow_data.edges)
    expect(wrapper.vm.nodes).toHaveLength(3)
    expect(wrapper.vm.nodes[0].data.value).toBe(1)

    resolveLoad({ data: structuredClone(GRAPH_B) })
    await flushPromises()
    await elapse(1000)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
    expect(wrapper.vm.canvasEditable).toBe(true)
  })

  it('saves the outgoing sheet\'s edit before loading the next one', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    const order = []
    logicApi.saveGraph.mockImplementation((id, payload) => { order.push(`save:${id}`); return Promise.resolve({ data: { id, ...payload } }) })
    logicApi.getGraph.mockImplementation(id => { order.push(`get:${id}`); return Promise.resolve({ data: structuredClone(GRAPH_B) }) })

    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ left: 0, top: 0 }) }
    wrapper.vm.onDrop({ dataTransfer: { getData: () => 'const_value' }, clientX: 5, clientY: 5 })
    wrapper.vm.onGraphPicked('graph-b')
    await flushPromises()

    expect(order).toEqual(['save:graph-a', 'get:graph-b'])
    expect(logicApi.saveGraph.mock.calls[0][1].flow_data.nodes).toHaveLength(4)
    expect(wrapper.vm.nodes).toHaveLength(3)
    await elapse(1000)
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
  })

  it('saves a pending change before deleting the sheet, then closes it', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    const order = []
    logicApi.saveGraph.mockImplementation((id, payload) => { order.push('save'); return Promise.resolve({ data: { id, ...payload } }) })
    logicApi.deleteGraph.mockImplementation(() => { order.push('delete'); return Promise.resolve({}) })

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await wrapper.vm.doDeleteGraph()
    await elapse(1000)

    expect(order).toEqual(['save', 'delete'])
    expect(wrapper.vm.activeGraphId).toBe('')
    expect(wrapper.vm.edges).toEqual([])
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

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await expect(wrapper.vm.doDeleteGraph()).rejects.toThrow('forbidden')
    await elapse(1000)

    expect(wrapper.vm.activeGraphId).toBe('graph-a')
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(savedEdges(logicApi)).toHaveLength(2)
  })

  it('leaves a sheet opened while the delete was in flight open', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    let finishDelete
    logicApi.deleteGraph.mockReturnValue(new Promise(resolve => { finishDelete = resolve }))

    const deleting = wrapper.vm.doDeleteGraph()
    await flushPromises()
    wrapper.vm.onGraphPicked('graph-b')
    await flushPromises()
    finishDelete({})
    await deleting

    expect(wrapper.vm.activeGraphId).toBe('graph-b')
    expect(wrapper.vm.nodes).toHaveLength(3)
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

  describe('ordering against in-flight saves', () => {
    // A save that stays in flight until the test releases it.
    function heldSaves() {
      const release = []
      const saveGraph = vi.fn().mockImplementation((id, payload) => new Promise((resolve, reject) => {
        release.push({ ok: () => resolve({ data: { id, ...payload } }), fail: () => reject(new Error('down')) })
      }))
      return { saveGraph, release }
    }

    it('toggles and renames the sheet that was open when clicked', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)
      wrapper.vm.doToggleEnabled()
      wrapper.vm.renameGraphName = 'Renamed'
      wrapper.vm.renameGraphDesc = 'Desc'
      wrapper.vm.doRenameGraph()
      wrapper.vm.onGraphPicked('graph-b')
      wrapper.vm.renameGraphName = 'Other'
      await flushPromises()

      release[0].ok()
      await flushPromises()
      expect(logicApi.getGraph).toHaveBeenLastCalledWith('graph-b')
      expect(logicApi.patchGraph.mock.calls).toEqual([
        ['graph-a', { enabled: false }],
        ['graph-a', { name: 'Renamed', description: 'Desc' }],
      ])
    })

    it('loads the next sheet only once the outgoing sheet\'s save went through', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })
      const loads = logicApi.getGraph.mock.calls.length

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      wrapper.vm.onGraphPicked('graph-b')
      await flushPromises()
      expect(saveGraph).toHaveBeenCalledWith('graph-a', expect.anything())
      expect(logicApi.getGraph).toHaveBeenCalledTimes(loads)
      // Nothing has switched yet — not even the selection.
      expect(wrapper.vm.activeGraphId).toBe('graph-a')

      release[0].ok()
      await flushPromises()
      expect(wrapper.vm.activeGraphId).toBe('graph-b')
      expect(logicApi.getGraph).toHaveBeenLastCalledWith('graph-b')
      expect(wrapper.vm.graphLoading).toBe(false)
    })

    it('stays on the sheet when its save fails while switching away', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })
      const loads = logicApi.getGraph.mock.calls.length

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      wrapper.vm.onGraphPicked('graph-b')
      await flushPromises()
      release[0].fail()
      await flushPromises()

      expect(wrapper.vm.activeGraphId).toBe('graph-a')
      expect(logicApi.getGraph).toHaveBeenCalledTimes(loads)
      expect(wrapper.vm.edges).toHaveLength(2)
      expect(wrapper.vm.graphLoading).toBe(false)
      expect(wrapper.vm.statusMsg.ok).toBe(false)
      const event = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(event)
      expect(event.defaultPrevented).toBe(true)
    })

    it('does not jump back when another switch took over meanwhile', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      wrapper.vm.onGraphPicked('graph-b')
      await flushPromises()
      wrapper.vm.activeGraphId = ''
      release[0].fail()
      await flushPromises()

      expect(wrapper.vm.activeGraphId).toBe('')
    })

    it('skips the GET of a load superseded while it waited for a save', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })
      const loads = logicApi.getGraph.mock.calls.length

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      wrapper.vm.onGraphPicked('graph-a')
      await flushPromises()
      wrapper.vm.onGraphPicked('graph-b')
      await flushPromises()
      release[0].ok()
      await flushPromises()

      expect(logicApi.getGraph.mock.calls.slice(loads)).toEqual([['graph-b']])
    })

    it('disables the Save button while a sheet is loading', async () => {
      const { wrapper, logicApi } = await mountLogicView()
      let finishLoad
      logicApi.getGraph.mockReturnValueOnce(new Promise(resolve => { finishLoad = resolve }))

      wrapper.vm.onGraphPicked('graph-b')
      await flushPromises()
      expect(wrapper.find('[data-testid="btn-save"]').attributes('disabled')).toBeDefined()

      finishLoad({ data: structuredClone(GRAPH_B) })
      await flushPromises()
      expect(wrapper.find('[data-testid="btn-save"]').attributes('disabled')).toBeUndefined()
    })

    it('stops run, export and duplicate when the pending change could not be saved', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView({ saveGraph })
      wrapper.vm.duplicateName = 'Copy'

      for (const action of ['requestGraphRun', 'doExportGraph', 'doDuplicate']) {
        wrapper.vm.onElementsChange([{ type: 'remove', id: 'x' }])
        const done = wrapper.vm[action]()
        await flushPromises()
        release.at(-1).fail()
        await done
      }

      expect(saveGraph).toHaveBeenCalledTimes(3)
      expect(logicRunAuthzApi.preflight).not.toHaveBeenCalled()
      expect(logicApi.exportGraph).not.toHaveBeenCalled()
      expect(logicApi.duplicateGraph).not.toHaveBeenCalled()
    })

    it('keeps legacy sheets with validation warnings exportable', async () => {
      const { wrapper, logicApi } = await mountLogicView()

      // A legacy cycle: the save is refused before any request is sent, so
      // there is nothing to wait for — export works as before auto-save.
      wrapper.vm.edges = [...wrapper.vm.edges, { id: 'back', source: 'n2', target: 'n1', sourceHandle: 'out', targetHandle: 'a' }]
      wrapper.vm.onElementsChange([{ type: 'remove', id: 'x' }])
      await wrapper.vm.doExportGraph()

      expect(logicApi.saveGraph).not.toHaveBeenCalled()
      expect(logicApi.exportGraph).toHaveBeenCalledWith('graph-a')
    })

    it('runs and exports the sheet that was open when clicked', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)
      const run = wrapper.vm.requestGraphRun()
      const exported = wrapper.vm.doExportGraph()
      wrapper.vm.onGraphPicked('graph-b')
      release[0].ok()
      await run
      await exported

      // The switch waits for the same save, so both still act on graph-a.
      expect(logicRunAuthzApi.preflight).toHaveBeenCalledWith('graph-a')
      expect(logicApi.exportGraph).toHaveBeenCalledWith('graph-a')
      expect(wrapper.vm.activeGraphId).toBe('graph-b')
    })

    it('waits for an in-flight save before deleting the sheet', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)
      const deleting = wrapper.vm.doDeleteGraph()
      await flushPromises()
      expect(logicApi.deleteGraph).not.toHaveBeenCalled()

      release[0].ok()
      await deleting
      expect(logicApi.deleteGraph).toHaveBeenCalledWith('graph-a')
    })

    it('asks before unloading the page only while a change is not yet sent', async () => {
      const { saveGraph, release } = heldSaves()
      const { wrapper } = await mountLogicView({ saveGraph })
      const unload = () => {
        const event = new Event('beforeunload', { cancelable: true })
        window.dispatchEvent(event)
        return event.defaultPrevented
      }

      expect(unload()).toBe(false)

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      expect(unload()).toBe(true)
      await flushPromises()
      expect(saveGraph).toHaveBeenCalledTimes(1)
      // Already sent: no prompt, which would also block a forced /login
      // redirect after that save got a 401.
      expect(unload()).toBe(false)

      release[0].ok()
      await flushPromises()
      expect(unload()).toBe(false)

      mounted.splice(mounted.indexOf(wrapper), 1)
      wrapper.unmount()
      wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' })
      expect(unload()).toBe(false)
    })
  })

  it('treats an empty handle like a missing one', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.edges = [{ id: 'e1', source: 'n1', target: 'n2', sourceHandle: '', targetHandle: '' }]

    wrapper.vm.onConnect({ source: 'n1', target: 'n2', sourceHandle: null, targetHandle: null })
    await elapse(1000)

    expect(wrapper.vm.edges).toHaveLength(1)
    expect(logicApi.saveGraph).not.toHaveBeenCalled()
  })

  it('exports under a fallback name once the sheet left the list, and not without a sheet', async () => {
    const { wrapper, logicApi, store } = await mountLogicView()
    const createObjectURL = vi.fn().mockReturnValue('blob:x')
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      document.body.dataset.lastDownload = this.download
    })
    try {
      store.graphs = store.graphs.filter(g => g.id !== 'graph-a')
      await wrapper.vm.doExportGraph()
      expect(logicApi.exportGraph).toHaveBeenCalledWith('graph-a')
      expect(document.body.dataset.lastDownload).toBe('logic_graph.json')

      wrapper.vm.activeGraphId = ''
      await wrapper.vm.doExportGraph()
      expect(logicApi.exportGraph).toHaveBeenCalledTimes(1)
    } finally {
      click.mockRestore()
      vi.unstubAllGlobals()
      delete document.body.dataset.lastDownload
    }
  })

  describe('a load whose sheet was closed without a newer load', () => {
    it('skips the GET when the sheet was deleted while waiting for its save', async () => {
      let releaseSave
      const saveGraph = vi.fn().mockImplementation((id, payload) => new Promise(resolve => {
        releaseSave = () => resolve({ data: { id, ...payload } })
      }))
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })
      const loads = logicApi.getGraph.mock.calls.length

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      const loading = wrapper.vm.loadGraph()
      await flushPromises()
      wrapper.vm.onGraphDeletedFromPicker('graph-a')
      releaseSave()
      await loading

      expect(logicApi.getGraph).toHaveBeenCalledTimes(loads)
      expect(wrapper.vm.activeGraphId).toBe('')
      expect(wrapper.vm.nodes).toEqual([])
    })

    it('drops a response arriving after the sheet was closed', async () => {
      const { wrapper, logicApi } = await mountLogicView()
      let finishLoad
      logicApi.getGraph.mockReturnValueOnce(new Promise(resolve => { finishLoad = resolve }))

      const loading = wrapper.vm.loadGraph()
      await flushPromises()
      wrapper.vm.onGraphDeletedFromPicker('graph-a')
      finishLoad({ data: structuredClone(GRAPH_A) })
      await loading

      expect(wrapper.vm.activeGraphId).toBe('')
      expect(wrapper.vm.nodes).toEqual([])
      expect(wrapper.vm.graphLoading).toBe(false)
    })

    it('reports no load error for a sheet that was replaced meanwhile', async () => {
      const { wrapper, logicApi } = await mountLogicView()
      let failLoad
      logicApi.getGraph.mockReturnValueOnce(new Promise((_resolve, reject) => { failLoad = reject }))

      const loading = wrapper.vm.loadGraph()
      await flushPromises()
      wrapper.vm.activeGraphId = 'graph-b'
      failLoad({ response: { status: 404, data: { detail: 'gone' } } })
      await loading

      expect(wrapper.vm.activeGraphId).toBe('graph-b')
      expect(wrapper.vm.statusMsg).toBe(null)
    })
  })

  describe('an edit whose save failed stays unsaved', () => {
    const unloadPrevented = () => {
      const event = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(event)
      return event.defaultPrevented
    }

    it('retries before a run and stops the run while it keeps failing', async () => {
      const saveGraph = vi.fn().mockRejectedValue(new Error('down'))
      const { wrapper, logicRunAuthzApi } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)
      expect(saveGraph).toHaveBeenCalledTimes(1)
      await elapse(5000)

      await wrapper.vm.requestGraphRun()
      expect(saveGraph).toHaveBeenCalledTimes(2)
      expect(logicRunAuthzApi.preflight).not.toHaveBeenCalled()

      saveGraph.mockImplementation((id, payload) => Promise.resolve({ data: { id, ...payload } }))
      await wrapper.vm.requestGraphRun()
      expect(saveGraph).toHaveBeenCalledTimes(3)
      expect(logicRunAuthzApi.preflight).toHaveBeenCalledWith('graph-a')
    })

    it('is retried on a sheet switch', async () => {
      const saveGraph = vi.fn().mockRejectedValueOnce(new Error('down'))
        .mockImplementation((id, payload) => Promise.resolve({ data: { id, ...payload } }))
      const { wrapper } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)

      wrapper.vm.onGraphPicked('graph-b')
      await flushPromises()
      expect(saveGraph).toHaveBeenCalledTimes(2)
      expect(saveGraph.mock.calls[1][0]).toBe('graph-a')
      expect(saveGraph.mock.calls[1][1].flow_data.edges).toHaveLength(2)
      expect(unloadPrevented()).toBe(false)
    })

    it('prompts before unloading and retries the save right away', async () => {
      const saveGraph = vi.fn().mockRejectedValueOnce(new Error('down'))
        .mockImplementation((id, payload) => Promise.resolve({ data: { id, ...payload } }))
      const { wrapper } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)
      expect(unloadPrevented()).toBe(true)
      await flushPromises()
      expect(saveGraph).toHaveBeenCalledTimes(2)
      expect(unloadPrevented()).toBe(false)
    })

    it('keeps the canvas of a re-picked sheet whose save failed', async () => {
      const saveGraph = vi.fn().mockRejectedValue(new Error('down'))
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })
      const loads = logicApi.getGraph.mock.calls.length

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      wrapper.vm.onGraphPicked('graph-a')
      await flushPromises()

      expect(logicApi.getGraph).toHaveBeenCalledTimes(loads)
      expect(wrapper.vm.edges).toHaveLength(2)
      expect(wrapper.vm.graphLoading).toBe(false)
      expect(unloadPrevented()).toBe(true)
    })


    it('leaves a newer pending edit in charge', async () => {
      let failSave
      const saveGraph = vi.fn().mockImplementationOnce(() => new Promise((_resolve, reject) => { failSave = reject }))
        .mockImplementation((id, payload) => Promise.resolve({ data: { id, ...payload } }))
      const { wrapper } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)
      wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' })
      failSave(new Error('down'))
      await flushPromises()
      await elapse(500)

      expect(saveGraph).toHaveBeenCalledTimes(2)
      expect(saveGraph.mock.calls[1][1].flow_data.edges).toHaveLength(3)
      expect(unloadPrevented()).toBe(false)
    })
  })

  describe('unsaved state follows the canvas, not the save timer', () => {
    const unloadPrevented = () => {
      const event = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(event)
      return event.defaultPrevented
    }

    it('keeps the canvas when a re-pick waited for an in-flight save that failed', async () => {
      let failSave
      const saveGraph = vi.fn().mockImplementationOnce(() => new Promise((_resolve, reject) => { failSave = reject }))
        .mockRejectedValue(new Error('still down'))
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })
      const loads = logicApi.getGraph.mock.calls.length

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)
      wrapper.vm.onGraphPicked('graph-a')
      await flushPromises()
      failSave(new Error('down'))
      await flushPromises()

      expect(logicApi.getGraph).toHaveBeenCalledTimes(loads)
      expect(wrapper.vm.edges).toHaveLength(2)
      expect(unloadPrevented()).toBe(true)
    })

    it('lets run and export go on after a permanent 4xx rejection without re-trying it', async () => {
      const saveGraph = vi.fn().mockRejectedValue({ response: { status: 422, data: { detail: 'timer too long' } } })
      const { wrapper, logicApi, logicRunAuthzApi } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await wrapper.vm.doExportGraph()
      await wrapper.vm.requestGraphRun()

      expect(logicApi.exportGraph).toHaveBeenCalledWith('graph-a')
      expect(logicRunAuthzApi.preflight).toHaveBeenCalledWith('graph-a')
      // Retrying a refused payload cannot help: no second PUT, no prompt.
      expect(saveGraph).toHaveBeenCalledTimes(1)
      expect(unloadPrevented()).toBe(false)
    })

    it('stops run on a 5xx failure', async () => {
      const saveGraph = vi.fn().mockRejectedValue({ response: { status: 503, data: { detail: 'busy' } } })
      const { wrapper, logicRunAuthzApi } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await wrapper.vm.requestGraphRun()

      expect(logicRunAuthzApi.preflight).not.toHaveBeenCalled()
      expect(wrapper.vm.statusMsg).toEqual({ ok: false, text: 'busy' })
    })

    it('lets a re-pick discard a validation-blocked edit', async () => {
      const { wrapper, logicApi } = await mountLogicView()
      const loads = logicApi.getGraph.mock.calls.length

      wrapper.vm.edges = [...wrapper.vm.edges, { id: 'back', source: 'n2', target: 'n1', sourceHandle: 'out', targetHandle: 'a' }]
      wrapper.vm.onElementsChange([{ type: 'remove', id: 'x' }])
      await elapse(500)
      // It can never be saved, so a reload prompt could not help.
      expect(unloadPrevented()).toBe(false)
      await wrapper.vm.onGraphPicked('graph-a')
      await flushPromises()

      expect(logicApi.saveGraph).not.toHaveBeenCalled()
      expect(logicApi.getGraph).toHaveBeenCalledTimes(loads + 1)
      expect(wrapper.vm.edges).toEqual(GRAPH_A.flow_data.edges)
    })

    it('ignores the late result of a save for a canvas replaced since', async () => {
      const pending = []
      const saveGraph = vi.fn().mockImplementation(() => new Promise((resolve, reject) => {
        pending.push({ ok: () => resolve({ data: {} }), fail: () => reject(new Error('down')) })
      }))
      const unloadPrevented = () => {
        const event = new Event('beforeunload', { cancelable: true })
        window.dispatchEvent(event)
        return event.defaultPrevented
      }

      for (const outcome of ['fail', 'ok']) {
        const { wrapper, store } = await mountLogicView({ saveGraph })
        wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
        await elapse(500)
        store.graphs = store.graphs.filter(g => g.id !== 'graph-a')
        wrapper.vm.onGraphDeletedFromPicker('graph-a')
        // A new sheet with an edit of its own must not inherit the result.
        wrapper.vm.activeGraphId = 'graph-b'
        await wrapper.vm.loadGraph()
        pending.at(-1)[outcome]()
        await flushPromises()

        expect(wrapper.vm.edges).toEqual([])
        expect(unloadPrevented()).toBe(false)
        mounted.splice(mounted.indexOf(wrapper), 1)
        wrapper.unmount()
        vi.resetModules()
      }
    })

    it('leaves a newer save already queued in charge when an older one fails', async () => {
      const pending = []
      const saveGraph = vi.fn().mockImplementation((id, payload) => new Promise((resolve, reject) => {
        pending.push({ ok: () => resolve({ data: { id, ...payload } }), fail: () => reject(new Error('down')) })
      }))
      const { wrapper } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await elapse(500)
      wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' })
      wrapper.vm.saveGraph()
      pending[0].fail()
      await flushPromises()
      // The newer save is queued, so nothing counts as unsent.
      expect(unloadPrevented()).toBe(false)

      pending[1].ok()
      await flushPromises()
      expect(saveGraph).toHaveBeenCalledTimes(2)
      expect(unloadPrevented()).toBe(false)
    })
  })

  it('clears the canvas when loading with no sheet selected, saving its unsent edit first', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    wrapper.vm.activeGraphId = ''
    await wrapper.vm.loadGraph()
    await flushPromises()

    expect(wrapper.vm.nodes).toEqual([])
    expect(wrapper.vm.edges).toEqual([])
    expect(logicApi.saveGraph).toHaveBeenCalledTimes(1)
    expect(logicApi.saveGraph.mock.calls[0][0]).toBe('graph-a')
  })

  describe('leaving a sheet', () => {
    it('also saves edits made while the outgoing save was in flight', async () => {
      const pending = []
      const saveGraph = vi.fn().mockImplementation((id, payload) => new Promise(resolve => {
        pending.push(() => resolve({ data: { id, ...payload } }))
      }))
      const { wrapper } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      const picking = wrapper.vm.onGraphPicked('graph-b')
      await flushPromises()
      wrapper.vm.onConnect({ source: 'n1', target: 'n3', sourceHandle: 'out', targetHandle: 'b' })
      pending[0]()
      await flushPromises()
      expect(saveGraph).toHaveBeenCalledTimes(2)
      expect(wrapper.vm.activeGraphId).toBe('graph-a')

      pending[1]()
      await picking
      expect(saveGraph.mock.calls[1][1].flow_data.edges).toHaveLength(3)
      expect(wrapper.vm.activeGraphId).toBe('graph-b')
    })

    it('creates and imports nothing while the open sheet cannot be saved', async () => {
      const saveGraph = vi.fn().mockRejectedValue(new Error('down'))
      const { wrapper, logicApi } = await mountLogicView({ saveGraph })
      logicApi.importGraph = vi.fn()

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      wrapper.vm.newGraphName = 'Fresh'
      await wrapper.vm.doCreateGraph()
      await wrapper.vm.onImportFile({ target: { files: [new File(['{}'], 'logic.json')], value: 'logic.json' } })

      expect(logicApi.createGraph).not.toHaveBeenCalled()
      expect(logicApi.importGraph).not.toHaveBeenCalled()
      expect(wrapper.vm.activeGraphId).toBe('graph-a')
      expect(wrapper.vm.edges).toHaveLength(2)
    })

    it.each([
      [409, false],
      [429, false],
      [404, true],
      [400, true],
      [401, true],
    ])('treats a %i answer as %s permanent', async (status, permanent) => {
      const saveGraph = vi.fn().mockRejectedValue({ response: { status, data: { detail: 'no' } } })
      const { wrapper } = await mountLogicView({ saveGraph })

      wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
      await wrapper.vm.onGraphPicked('graph-b')

      expect(wrapper.vm.activeGraphId).toBe(permanent ? 'graph-b' : 'graph-a')
    })

    it('closes the config panel while a sheet loads', async () => {
      const { wrapper } = await mountLogicView()

      wrapper.vm.onNodeClick({ node: wrapper.vm.nodes[0] })
      expect(wrapper.vm.selectedNode).not.toBe(null)
      const picking = wrapper.vm.onGraphPicked('graph-b')
      await flushPromises()
      await picking

      expect(wrapper.vm.selectedNode).toBe(null)
    })
  })

  it('makes a remounted editor wait for the save its predecessor started', async () => {
    let finishSave
    const saveGraph = vi.fn().mockImplementation((id, payload) => new Promise(resolve => {
      finishSave = () => resolve({ data: { id, ...payload } })
    }))
    const first = await mountLogicView({ saveGraph })
    first.wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    mounted.splice(mounted.indexOf(first.wrapper), 1)
    first.wrapper.unmount()
    await flushPromises()
    expect(saveGraph).toHaveBeenCalledTimes(1)

    const loads = first.logicApi.getGraph.mock.calls.length

    // Same pinia (and cached modules) as the app keeps across route changes.
    await mountLogicView({ saveGraph, pinia: first.pinia })
    expect(first.logicApi.getGraph).toHaveBeenCalledTimes(loads)

    finishSave()
    await flushPromises()
    expect(first.logicApi.getGraph).toHaveBeenCalledTimes(loads + 1)
    expect(first.logicApi.getGraph).toHaveBeenLastCalledWith('graph-a')
  })

  it('keeps a canvas whose save failed even when reloaded directly', async () => {
    const saveGraph = vi.fn().mockRejectedValue(new Error('down'))
    const { wrapper, logicApi } = await mountLogicView({ saveGraph })
    const loads = logicApi.getGraph.mock.calls.length

    wrapper.vm.onConnect({ source: 'n2', target: 'n3', sourceHandle: 'out', targetHandle: 'a' })
    await wrapper.vm.loadGraph()

    expect(saveGraph).toHaveBeenCalledTimes(1)
    expect(logicApi.getGraph).toHaveBeenCalledTimes(loads)
    expect(wrapper.vm.edges).toHaveLength(2)
    expect(wrapper.vm.graphLoading).toBe(false)
  })
})
