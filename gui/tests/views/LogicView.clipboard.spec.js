/**
 * Tests for copy/paste of selected logic-editor nodes (#1084).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  const storage = {
    getItem: vi.fn().mockReturnValue(null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  }
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
  vi.doMock('vue-router', () => ({ useRoute: () => ({ query: {} }), useRouter: () => ({ push: vi.fn() }) }))
  vi.doMock('@vue-flow/core', () => ({
    VueFlow: { name: 'VueFlow', props: ['snapToGrid', 'snapGrid'], template: '<div data-testid="vue-flow"><slot /></div>' },
    Handle: { template: '<span />' },
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    useVueFlow: () => ({ project: (point) => point }),
    addEdge: (edge, edges) => [...edges, edge],
  }))
  vi.doMock('@vue-flow/background', () => ({ Background: { name: 'Background', props: ['gap', 'offset'], template: '<div />' } }))
  vi.doMock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))
  vi.doMock('@vue-flow/minimap', () => ({ MiniMap: { template: '<div />' } }))
})

const mountedWrappers = []

afterEach(() => {
  // LogicView registers a window-level keydown listener in onMounted; without
  // unmounting, it leaks across tests and every leftover instance also reacts
  // to keydown events dispatched by later tests in this file.
  while (mountedWrappers.length) mountedWrappers.pop().unmount()
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
    description: '',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'n1', type: 'and', position: { x: 0, y: 0 }, data: { input_count: 2 } },
        { id: 'n2', type: 'and', position: { x: 100, y: 0 }, data: { input_count: 2 } },
      ],
      edges: [
        { id: 'e1', source: 'n1', target: 'n2', sourceHandle: 'out', targetHandle: 'in1' },
      ],
    },
    ...overrides,
  }
}

async function mountLogicView({ isAdmin = true, graph = makeGraph() } = {}) {
  const logicApi = {
    nodeTypes: vi.fn().mockResolvedValue({ data: [{ type: 'and', config_schema: {} }] }),
    listGraphs: vi.fn().mockResolvedValue({ data: [graph] }),
    getGraph: vi.fn().mockResolvedValue({ data: graph }),
    saveGraph: vi.fn().mockResolvedValue({ data: graph }),
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
  wrapper.vm.activeGraphId = graph.id
  await wrapper.vm.loadGraph()
  mountedWrappers.push(wrapper)
  return { wrapper, logicApi }
}

describe('LogicView node copy/paste', () => {
  it('copySelection does nothing and reports empty when no node is selected', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.copySelection()
    expect(wrapper.vm.clipboard).toBeNull()
    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('copies the selected node(s) and their internal edge into the clipboard', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))

    wrapper.vm.copySelection()

    expect(wrapper.vm.clipboard.nodes).toHaveLength(2)
    expect(wrapper.vm.clipboard.edges).toHaveLength(1)
    expect(wrapper.vm.statusMsg.ok).toBe(true)
  })

  it('pasteClipboard is a no-op without a clipboard', async () => {
    const { wrapper } = await mountLogicView()
    const before = wrapper.vm.nodes.length
    wrapper.vm.pasteClipboard()
    expect(wrapper.vm.nodes).toHaveLength(before)
  })

  it('pastes new nodes with fresh ids and preserves settings/data', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    wrapper.vm.pasteClipboard()

    expect(wrapper.vm.nodes).toHaveLength(3)
    const pasted = wrapper.vm.nodes.at(-1)
    expect(pasted.id).not.toBe('n1')
    expect(pasted.data).toEqual({ input_count: 2 })
  })

  it('selects only the pasted nodes so they can be dragged as a group right away', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    wrapper.vm.pasteClipboard()

    const source = wrapper.vm.nodes.find(n => n.id === 'n1')
    const pasted = wrapper.vm.nodes.at(-1)
    expect(source.selected).toBe(false)
    expect(pasted.selected).toBe(true)
  })

  it('offsets repeated pastes of the same clipboard so blocks do not stack', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    wrapper.vm.pasteClipboard()
    wrapper.vm.pasteClipboard()

    const [first, second] = wrapper.vm.nodes.slice(-2)
    expect(second.position.x).toBeGreaterThan(first.position.x)
  })

  it('anchors a cross-sheet paste at the visible canvas center when the canvas has real layout dimensions', async () => {
    const otherGraph = makeGraph('graph-2', { name: 'Other Graph', flow_data: { nodes: [], edges: [] } })
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    logicApi.getGraph.mockResolvedValueOnce({ data: otherGraph })
    wrapper.vm.activeGraphId = 'graph-2'
    await wrapper.vm.loadGraph()

    // JSDOM's getBoundingClientRect() is all-zero by default (no real
    // layout); stub it with real dimensions so pasteClipboard's viewport
    // centering (otherwise silently skipped in every other test here) is
    // actually exercised.
    wrapper.vm.canvasWrapper.getBoundingClientRect = () => ({
      width: 800, height: 600, top: 0, left: 0, right: 800, bottom: 600, x: 0, y: 0, toJSON() {},
    })

    wrapper.vm.pasteClipboard()

    // The mocked project() is an identity function, so targetCenter is
    // (width/2, height/2) = (400, 300); n1's own position (0,0) is the
    // single-node group's bounding-box center, plus the usual pasteIndex=0
    // stack offset (40) on top, same as logicClipboard.spec.js's pure-utility
    // equivalent.
    const pasted = wrapper.vm.nodes.at(-1)
    expect(pasted.position).toEqual({ x: 440, y: 340 })
  })

  it('does not center a cross-sheet paste when the canvas ref is unavailable', async () => {
    const otherGraph = makeGraph('graph-2', { name: 'Other Graph', flow_data: { nodes: [], edges: [] } })
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    logicApi.getGraph.mockResolvedValueOnce({ data: otherGraph })
    wrapper.vm.activeGraphId = 'graph-2'
    await wrapper.vm.loadGraph()

    wrapper.vm.canvasWrapper = null

    wrapper.vm.pasteClipboard()

    // No rect at all (canvasWrapper.value is falsy) must fall back to the
    // plain stack offset, same as every other centering-skipped case.
    const pasted = wrapper.vm.nodes.at(-1)
    expect(pasted.position).toEqual({ x: 40, y: 40 })
  })

  it('does not center a cross-sheet paste when the canvas rectangle has a zero height', async () => {
    const otherGraph = makeGraph('graph-2', { name: 'Other Graph', flow_data: { nodes: [], edges: [] } })
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    logicApi.getGraph.mockResolvedValueOnce({ data: otherGraph })
    wrapper.vm.activeGraphId = 'graph-2'
    await wrapper.vm.loadGraph()

    wrapper.vm.canvasWrapper.getBoundingClientRect = () => ({
      width: 800, height: 0, top: 0, left: 0, right: 800, bottom: 0, x: 0, y: 0, toJSON() {},
    })

    wrapper.vm.pasteClipboard()

    const pasted = wrapper.vm.nodes.at(-1)
    expect(pasted.position).toEqual({ x: 40, y: 40 })
  })

  it('keeps a same-sheet paste near its source instead of jumping to the canvas center', async () => {
    // Regression: pasting back into the sheet the copy came from must
    // preserve the small relative offset, even when the canvas reports real
    // layout dimensions (which would otherwise make cross-sheet-style
    // viewport centering kick in, jumping the copy hundreds of pixels away
    // from the original it was copied from).
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    wrapper.vm.canvasWrapper.getBoundingClientRect = () => ({
      width: 800, height: 600, top: 0, left: 0, right: 800, bottom: 600, x: 0, y: 0, toJSON() {},
    })

    wrapper.vm.pasteClipboard()

    const pasted = wrapper.vm.nodes.at(-1)
    expect(pasted.position).toEqual({ x: 40, y: 40 })
  })

  it('survives switching to a different logic page — clipboard is not tied to the loaded graph', async () => {
    const otherGraph = makeGraph('graph-2', { name: 'Other Graph', flow_data: { nodes: [], edges: [] } })
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    logicApi.getGraph.mockResolvedValueOnce({ data: otherGraph })
    wrapper.vm.activeGraphId = 'graph-2'
    await wrapper.vm.loadGraph()
    expect(wrapper.vm.nodes).toHaveLength(0)

    wrapper.vm.pasteClipboard()
    expect(wrapper.vm.nodes).toHaveLength(1)
  })

  it('does not expose copy/paste actions for non-admin users', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: false })
    expect(wrapper.find('[data-testid="btn-copy-nodes"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-paste-nodes"]').exists()).toBe(false)
  })

  it('Ctrl+C / Ctrl+V keyboard shortcuts copy and paste the selection', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }))
    expect(wrapper.vm.clipboard).not.toBeNull()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', ctrlKey: true }))
    expect(wrapper.vm.nodes).toHaveLength(3)
  })

  it('ignores Ctrl+C / Ctrl+V while a text input is focused', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }))
    expect(wrapper.vm.clipboard).toBeNull()

    document.body.removeChild(input)
  })

  it('Ctrl+V still works right after focusing the graph-picker button', async () => {
    // Regression: BUTTON must not count as an "editable" target — the
    // graph-picker button is the documented way to switch sheets before
    // pasting (#1217), and it retains focus after opening/closing the popup.
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    const button = wrapper.find('[data-testid="btn-open-graph-picker"]').element
    button.focus()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', ctrlKey: true }))
    expect(wrapper.vm.nodes).toHaveLength(3)
  })

  it('clears the config-panel selection after paste so it cannot silently edit the wrong node', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()
    wrapper.vm.selectedNode = { id: 'n1', type: 'and', data: {} }

    wrapper.vm.pasteClipboard()

    expect(wrapper.vm.selectedNode).toBeNull()
  })

  it('ignores Ctrl+C / Ctrl+V while a modal is open', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()
    wrapper.vm.showNewGraph = true

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', ctrlKey: true }))

    expect(wrapper.vm.nodes).toHaveLength(2)
  })

  it('does not paste while the target sheet is still loading', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    let resolveGetGraph
    logicApi.getGraph.mockReturnValueOnce(new Promise(resolve => { resolveGetGraph = resolve }))
    wrapper.vm.activeGraphId = 'graph-1'
    const loadPromise = wrapper.vm.loadGraph()

    wrapper.vm.pasteClipboard()
    expect(wrapper.vm.graphLoading).toBe(true)

    resolveGetGraph({ data: { flow_data: { nodes: [], edges: [] } } })
    await loadPromise

    // The still-in-flight load must not have been clobbered by a paste that
    // ran while it was pending, and pasting is possible again once it settles.
    expect(wrapper.vm.nodes).toHaveLength(0)
    wrapper.vm.pasteClipboard()
    expect(wrapper.vm.nodes).toHaveLength(1)
  })

  it('ignores a stale loadGraph response when a newer sheet switch is already in flight', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    let resolveStale, resolveFresh
    logicApi.getGraph
      .mockReturnValueOnce(new Promise(resolve => { resolveStale = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { resolveFresh = resolve }))

    wrapper.vm.activeGraphId = 'graph-1'
    const stalePromise = wrapper.vm.loadGraph()
    wrapper.vm.activeGraphId = 'graph-2'
    const freshPromise = wrapper.vm.loadGraph()

    // The older request resolves last-in-first-served, but it must not apply
    // its (now stale) data or clear graphLoading — the newer request for
    // graph-2 is still pending, so the nodes loaded for graph-1 by
    // mountLogicView() must be left untouched.
    resolveStale({ data: { flow_data: { nodes: [{ id: 'stale' }], edges: [] } } })
    await stalePromise
    expect(wrapper.vm.graphLoading).toBe(true)
    expect(wrapper.vm.nodes.map(n => n.id)).toEqual(['n1', 'n2'])

    resolveFresh({ data: { flow_data: { nodes: [{ id: 'fresh' }], edges: [] } } })
    await freshPromise
    expect(wrapper.vm.graphLoading).toBe(false)
    expect(wrapper.vm.nodes).toEqual([{ id: 'fresh', position: { x: 100, y: 100 }, data: {} }])
  })

  it('ignores a stale loadGraph rejection when a newer sheet switch is already in flight', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    let rejectStale, resolveFresh
    logicApi.getGraph
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectStale = reject }))
      .mockReturnValueOnce(new Promise(resolve => { resolveFresh = resolve }))

    wrapper.vm.activeGraphId = 'graph-1'
    const stalePromise = wrapper.vm.loadGraph()
    wrapper.vm.activeGraphId = 'graph-2'
    const freshPromise = wrapper.vm.loadGraph()

    // The older request fails last-in-first-served, but its catch block
    // must not clear graphLoading or show an error status — the newer
    // request for graph-2 is still pending.
    rejectStale(new Error('stale network error'))
    await stalePromise
    expect(wrapper.vm.graphLoading).toBe(true)
    expect(wrapper.vm.statusMsg).toBeNull()
    expect(wrapper.vm.nodes.map(n => n.id)).toEqual(['n1', 'n2'])

    resolveFresh({ data: { flow_data: { nodes: [{ id: 'fresh' }], edges: [] } } })
    await freshPromise
    expect(wrapper.vm.graphLoading).toBe(false)
    expect(wrapper.vm.nodes).toEqual([{ id: 'fresh', position: { x: 100, y: 100 }, data: {} }])
  })

  it('clears any pre-existing edge selection when pasting so an unrelated edge is not also selected', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))
    wrapper.vm.copySelection()
    wrapper.vm.edges = wrapper.vm.edges.map(e => ({ ...e, selected: true }))

    wrapper.vm.pasteClipboard()

    const sourceEdge = wrapper.vm.edges.find(e => e.id === 'e1')
    const pastedEdge = wrapper.vm.edges.find(e => e.id !== 'e1')
    expect(sourceEdge.selected).toBe(false)
    expect(pastedEdge).toBeDefined()
    expect(pastedEdge.selected).not.toBe(true)
  })

  it('disables the paste button while the target sheet is still loading', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    let resolveGetGraph
    logicApi.getGraph.mockReturnValueOnce(new Promise(resolve => { resolveGetGraph = resolve }))
    wrapper.vm.activeGraphId = 'graph-1'
    const loadPromise = wrapper.vm.loadGraph()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="btn-paste-nodes"]').attributes('disabled')).toBeDefined()

    resolveGetGraph({ data: { flow_data: { nodes: [], edges: [] } } })
    await loadPromise
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="btn-paste-nodes"]').attributes('disabled')).toBeUndefined()
  })

  it('does not copy while the target sheet is still loading', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    // Select in the predecessor sheet first, so a wrongly-succeeding copy
    // during the load below is observable (an empty selection would make
    // clipboard stay null regardless of the loading guard being tested).
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))

    let resolveGetGraph
    logicApi.getGraph.mockReturnValueOnce(new Promise(resolve => { resolveGetGraph = resolve }))
    wrapper.vm.activeGraphId = 'graph-1'
    const loadPromise = wrapper.vm.loadGraph()

    // The selector already names graph-1, but `nodes` still holds graph-1's
    // predecessor (mountLogicView's own graph) with its selection — copying
    // now would silently put those stale blocks in the clipboard.
    wrapper.vm.copySelection()
    expect(wrapper.vm.clipboard).toBeNull()

    resolveGetGraph({ data: { flow_data: { nodes: [{ id: 'n1', position: { x: 0, y: 0 }, data: {} }], edges: [] } } })
    await loadPromise

    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))
    wrapper.vm.copySelection()
    expect(wrapper.vm.clipboard).not.toBeNull()
  })

  it('disables the copy button while the target sheet is still loading', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="btn-copy-nodes"]').attributes('disabled')).toBeUndefined()

    let resolveGetGraph
    logicApi.getGraph.mockReturnValueOnce(new Promise(resolve => { resolveGetGraph = resolve }))
    wrapper.vm.activeGraphId = 'graph-1'
    const loadPromise = wrapper.vm.loadGraph()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="btn-copy-nodes"]').attributes('disabled')).toBeDefined()

    resolveGetGraph({ data: { flow_data: { nodes: [{ id: 'n1', position: { x: 0, y: 0 }, data: {} }], edges: [] } } })
    await loadPromise
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="btn-copy-nodes"]').attributes('disabled')).toBeUndefined()
  })

  it('cancels a still-pending status timeout when a new status is shown, so an earlier one cannot clear a later result', async () => {
    vi.useFakeTimers()
    try {
      const { wrapper } = await mountLogicView()
      wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))

      wrapper.vm.copySelection()
      vi.advanceTimersByTime(2000)
      wrapper.vm.pasteClipboard()
      const pastedText = wrapper.vm.statusMsg.text

      // Copy's 3s timeout (now at 2000ms elapsed, 1000ms left) must not
      // survive to null out Paste's own, later-started status.
      vi.advanceTimersByTime(1000)
      expect(wrapper.vm.statusMsg).not.toBeNull()
      expect(wrapper.vm.statusMsg.ok).toBe(true)
      expect(wrapper.vm.statusMsg.text).toBe(pastedText)
    } finally {
      vi.useRealTimers()
    }
  })

  it('loadGraph falls back to an empty edges array when the response omits it', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    logicApi.getGraph.mockResolvedValueOnce({
      data: { flow_data: { nodes: [{ id: 'bare' }] } }, // no edges, no data on the node
    })

    await wrapper.vm.loadGraph()

    expect(wrapper.vm.nodes).toEqual([{ id: 'bare', position: { x: 100, y: 100 }, data: {} }])
    expect(wrapper.vm.edges).toEqual([])
  })

  it('loadGraph falls back to an empty nodes array when the response omits it entirely', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    logicApi.getGraph.mockResolvedValueOnce({ data: { flow_data: {} } })

    await wrapper.vm.loadGraph()

    expect(wrapper.vm.nodes).toEqual([])
    expect(wrapper.vm.edges).toEqual([])
  })

  it('clears stale nodes/edges/selection and the active graph selection when a sheet switch fails to load', async () => {
    // Regression: without this, graphLoading still clears in `finally`
    // (re-enabling Copy/Paste/Save) while activeGraphId keeps naming the
    // sheet that failed to load — Save stays enabled and would submit
    // whatever nodes/edges are on screen (stale, or emptied), overwriting
    // the real graph on the server. Reverting activeGraphId to '' hides the
    // whole editor and disables every admin action gated on it.
    const { wrapper, logicApi } = await mountLogicView()
    expect(wrapper.vm.nodes).toHaveLength(2)
    wrapper.vm.selectedNode = { id: 'n1', type: 'and', data: {} }

    logicApi.getGraph.mockRejectedValueOnce(new Error('network error'))
    wrapper.vm.activeGraphId = 'graph-2'
    await wrapper.vm.loadGraph()

    expect(wrapper.vm.activeGraphId).toBe('')
    expect(wrapper.vm.nodes).toEqual([])
    expect(wrapper.vm.edges).toEqual([])
    expect(wrapper.vm.selectedNode).toBeNull()
    expect(wrapper.vm.graphLoading).toBe(false)
    expect(wrapper.vm.statusMsg.ok).toBe(false)
  })

  it('shows the server-provided error detail when a sheet load fails with a response body', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    logicApi.getGraph.mockRejectedValueOnce({ response: { data: { detail: 'Graph not found' } } })
    wrapper.vm.activeGraphId = 'graph-2'
    await wrapper.vm.loadGraph()

    expect(wrapper.vm.statusMsg).toEqual({ ok: false, text: 'Graph not found' })
  })

  it('copySelection is a no-op for non-admin users', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: false })
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))

    wrapper.vm.copySelection()

    expect(wrapper.vm.clipboard).toBeNull()
  })

  it('_isEditableTarget returns false for a null element', async () => {
    const { wrapper } = await mountLogicView()
    expect(wrapper.vm._isEditableTarget(null)).toBe(false)
  })

  it('_isEditableTarget returns true for a textarea', async () => {
    const { wrapper } = await mountLogicView()
    const textarea = document.createElement('textarea')
    expect(wrapper.vm._isEditableTarget(textarea)).toBe(true)
  })

  it('_isEditableTarget returns true for a contenteditable element', async () => {
    const { wrapper } = await mountLogicView()
    const div = document.createElement('div')
    div.contentEditable = 'true'
    expect(wrapper.vm._isEditableTarget(div)).toBe(true)
  })

  it('_isEditableTarget returns false for a plain, non-editable div', async () => {
    const { wrapper } = await mountLogicView()
    const div = document.createElement('div')
    expect(wrapper.vm._isEditableTarget(div)).toBe(false)
  })

  it('ignores Ctrl+C while a textarea is focused', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)

    const textarea = document.createElement('textarea')
    document.body.appendChild(textarea)
    textarea.focus()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }))
    expect(wrapper.vm.clipboard).toBeNull()

    document.body.removeChild(textarea)
  })

  it('ignores Ctrl+C while a contenteditable element is focused', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)

    const div = document.createElement('div')
    div.contentEditable = 'true'
    div.tabIndex = 0
    document.body.appendChild(div)
    div.focus()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }))
    expect(wrapper.vm.clipboard).toBeNull()

    document.body.removeChild(div)
  })

  it('_onClipboardKeydown does nothing for non-admin users', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: false })
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }))

    expect(wrapper.vm.clipboard).toBeNull()
  })

  it('_onClipboardKeydown does nothing without an active graph', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))
    wrapper.vm.activeGraphId = ''

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }))

    expect(wrapper.vm.clipboard).toBeNull()
  })

  it('_onClipboardKeydown ignores keydown without Ctrl/Cmd held', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c' }))

    expect(wrapper.vm.clipboard).toBeNull()
  })

  it('Ctrl+Shift+V (uppercase V) also pastes', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'V', ctrlKey: true }))

    expect(wrapper.vm.nodes).toHaveLength(3)
  })

  it('_onClipboardKeydown ignores Ctrl-held keys other than C/V', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => ({ ...n, selected: true }))

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'x', ctrlKey: true }))

    expect(wrapper.vm.clipboard).toBeNull()
  })

  it('Cmd (metaKey) alone also triggers the copy/paste shortcuts', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', metaKey: true }))
    expect(wrapper.vm.clipboard).not.toBeNull()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', metaKey: true }))
    expect(wrapper.vm.nodes).toHaveLength(3)
  })

  it('uppercase C also copies', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'C', ctrlKey: true }))

    expect(wrapper.vm.clipboard).not.toBeNull()
  })

  it('ignores Ctrl+C / Ctrl+V while the rename-graph modal is open', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()
    wrapper.vm.showRenameGraph = true

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', ctrlKey: true }))

    expect(wrapper.vm.nodes).toHaveLength(2)
  })

  it('ignores Ctrl+C / Ctrl+V while the delete-confirm modal is open', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()
    wrapper.vm.showDeleteConfirm = true

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', ctrlKey: true }))

    expect(wrapper.vm.nodes).toHaveLength(2)
  })

  it('pasteClipboard is a no-op for non-admin users even with a populated clipboard', async () => {
    const { wrapper } = await mountLogicView({ isAdmin: false })
    // Assigned directly (bypassing copySelection, itself already guarded
    // for non-admin elsewhere) so this test isolates pasteClipboard's own
    // admin guard.
    wrapper.vm.clipboard = { nodes: [{ id: 'copied', type: 'and', position: { x: 0, y: 0 }, data: {} }], edges: [] }

    wrapper.vm.pasteClipboard()

    expect(wrapper.vm.nodes).toHaveLength(2)
  })

  it('pasteClipboard is a no-op with a populated clipboard but no active graph', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.nodes = wrapper.vm.nodes.map(n => n.id === 'n1' ? { ...n, selected: true } : n)
    wrapper.vm.copySelection()
    expect(wrapper.vm.clipboard).not.toBeNull()

    wrapper.vm.activeGraphId = ''

    wrapper.vm.pasteClipboard()

    expect(wrapper.vm.nodes).toHaveLength(2)
  })
})
