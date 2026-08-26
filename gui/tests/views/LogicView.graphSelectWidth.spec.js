/**
 * Issue #1171 — the logic-sheet select shrank to just its dropdown arrow on
 * narrow viewports because it had neither `min-w-*` nor `flex-shrink-0`: a
 * flex item's default `min-width: auto` resolves to a tiny intrinsic value
 * for a <select>, not the width of its longest <option>. The select now
 * sizes itself (in `ch`) to the longest visible option text and never
 * shrinks below that.
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

function graph(overrides = {}) {
  return {
    id: 'graph-1',
    name: 'Graph',
    description: '',
    enabled: true,
    flow_data: { nodes: [], edges: [] },
    ...overrides,
  }
}

async function mountLogicView(graphs) {
  const logicApi = {
    nodeTypes:  vi.fn().mockResolvedValue({ data: [] }),
    listGraphs: vi.fn().mockResolvedValue({ data: graphs }),
    getGraph:   vi.fn().mockResolvedValue({ data: structuredClone(graphs[0] ?? graph()) }),
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
  return wrapper
}

describe('LogicView — graph select width (#1171)', () => {
  it('never shrinks below the 12ch floor when there are no graphs', async () => {
    const w = await mountLogicView([])
    expect(w.vm.graphSelectWidthCh).toBeGreaterThanOrEqual(12)
  })

  it('grows to fit a long graph name', async () => {
    const longName = 'A'.repeat(30)
    const w = await mountLogicView([graph({ name: longName })])
    expect(w.vm.graphSelectWidthCh).toBe(30)
  })

  it('caps at the 40ch ceiling for an extremely long name', async () => {
    const veryLongName = 'B'.repeat(80)
    const w = await mountLogicView([graph({ name: veryLongName })])
    expect(w.vm.graphSelectWidthCh).toBe(40)
  })

  it('accounts for the disabled-suffix when computing the widest option', async () => {
    const shortDisabledName = 'C'.repeat(15)
    const w = await mountLogicView([graph({ name: shortDisabledName, enabled: false })])
    // The rendered option text is name + graphDisabledSuffix, so it must be
    // wider than the bare name alone.
    expect(w.vm.graphSelectWidthCh).toBeGreaterThan(15)
  })

  it('applies the computed width to the rendered select element', async () => {
    const longName = 'D'.repeat(25)
    const w = await mountLogicView([graph({ name: longName })])
    const select = w.get('[data-testid="select-graph"]')
    expect(select.element.style.width).toBe('25ch')
  })
})

describe('LogicView — graph select width uses real glyph measurement when canvas is available (Codex review, PR #1172)', () => {
  // jsdom has no real canvas, so the tests above only exercise the
  // text.length fallback. Real browsers do have canvas.measureText(), so a
  // shorter but wide-glyph name (e.g. many uppercase letters) can need MORE
  // pixels than a longer narrow-glyph one — text.length alone would pick the
  // narrow one as "longest" and clip the wide one. Mock getContext() to
  // prove the fix picks the actually-widest option instead of the
  // longest-by-character-count one.
  const widthOf = (char) => (char === 'W' ? 15 : char === '0' ? 7 : 5)
  let getContextSpy

  beforeEach(() => {
    getContextSpy = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      font: '',
      measureText: (str) => ({
        width: [...str].reduce((sum, ch) => sum + widthOf(ch), 0),
      }),
    })
  })

  afterEach(() => {
    getContextSpy.mockRestore()
  })

  it('sizes to the widest-in-pixels option, not the longest-by-character-count one', async () => {
    const narrowButLonger = 'i'.repeat(20)  // 20 * 5px = 100px, but 20 chars
    const wideButShorter = 'W'.repeat(14)   // 14 * 15px = 210px, but only 14 chars
    const w = await mountLogicView([
      graph({ id: 'g1', name: narrowButLonger }),
      graph({ id: 'g2', name: wideButShorter }),
    ])
    // chPx = widthOf('0') = 7; widest required = 210px → ceil(210/7) = 30ch.
    // The old text.length heuristic would have picked 20ch (narrowButLonger)
    // and clipped the wide name, whose required width is 210px > 20*7=140px.
    expect(w.vm.graphSelectWidthCh).toBe(30)
  })
})
