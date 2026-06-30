import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const MINIMAP_POS_KEY = 'obs-logic-minimap-pos'

function makeStorage(overrides = {}) {
  const store = { ...overrides }
  return {
    getItem:    vi.fn(k => store[k] ?? null),
    setItem:    vi.fn((k, v) => { store[k] = v }),
    removeItem: vi.fn(k => { delete store[k] }),
    clear:      vi.fn(() => { Object.keys(store).forEach(k => delete store[k]) }),
    _store:     store,
  }
}

function overrideStorage(storage) {
  Object.defineProperty(window,     'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
}

// A MiniMap stub that exposes a real DOM element via $el (to simulate inheritAttrs:false behaviour)
const MiniMapStub = {
  name: 'MiniMap',
  inheritAttrs: false,
  template: '<div class="minimap-stub" />',
}

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  overrideStorage(makeStorage())

  vi.doMock('@vue-flow/core', () => ({
    VueFlow: { template: '<div data-testid="vue-flow"><slot /></div>' },
    Handle:  { template: '<span />' },
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    useVueFlow: () => ({ project: p => p }),
    addEdge: (edge, edges) => [...edges, edge],
  }))
  vi.doMock('@vue-flow/background', () => ({ Background: { template: '<div />' } }))
  vi.doMock('@vue-flow/controls',   () => ({ Controls:   { template: '<div />' } }))
  vi.doMock('@vue-flow/minimap',    () => ({ MiniMap: MiniMapStub }))
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

async function mountView(storageOverrides = {}) {
  overrideStorage(makeStorage(storageOverrides))

  vi.doMock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))
  vi.doMock('@/api/client', () => ({
    logicApi: {
      nodeTypes:   vi.fn().mockResolvedValue({ data: [] }),
      listGraphs:  vi.fn().mockResolvedValue({ data: [] }),
      getGraph:    vi.fn().mockResolvedValue({ data: null }),
      createGraph: vi.fn(),
      saveGraph:   vi.fn(),
      runGraph:    vi.fn(),
      patchGraph:  vi.fn(),
      deleteGraph: vi.fn(),
      duplicateGraph: vi.fn(),
      exportGraph:    vi.fn(),
      importGraph:    vi.fn(),
    },
  }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/views/LogicView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: {
        NodePalette:   true,
        NodeConfigPanel: true,
        Modal:         { template: '<div><slot /><slot name="footer" /></div>' },
        ConfirmDialog: true,
        Spinner:       { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

// ── minimapStyle computed ──────────────────────────────────────────────────

describe('minimapStyle', () => {
  it('returns empty object when minimapPos is null', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = null
    expect(wrapper.vm.minimapStyle).toEqual({})
  })

  it('returns position styles when minimapPos is set', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = { x: 100, y: 200 }
    expect(wrapper.vm.minimapStyle).toEqual({
      left: '100px', top: '200px', right: 'auto', bottom: 'auto',
    })
  })
})

// ── localStorage initialisation ────────────────────────────────────────────

describe('minimap localStorage init', () => {
  it('reads saved position from localStorage on mount', async () => {
    const saved = JSON.stringify({ x: 42, y: 99 })
    const wrapper = await mountView({ [MINIMAP_POS_KEY]: saved })
    expect(wrapper.vm.minimapPos).toEqual({ x: 42, y: 99 })
  })

  it('starts with null position when nothing is stored', async () => {
    const wrapper = await mountView()
    expect(wrapper.vm.minimapPos).toBeNull()
  })

  it('starts with null position when stored value is invalid JSON', async () => {
    const wrapper = await mountView({ [MINIMAP_POS_KEY]: 'not-json{{' })
    expect(wrapper.vm.minimapPos).toBeNull()
  })
})

// ── _defaultMinimapPos ─────────────────────────────────────────────────────

describe('_defaultMinimapPos', () => {
  it('uses canvasWrapper dimensions when available', async () => {
    const wrapper = await mountView()
    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ width: 800, height: 600 }) }
    const pos = wrapper.vm._defaultMinimapPos()
    expect(pos).toEqual({ x: 800 - 208, y: 600 - 152 })
  })

  it('falls back to window dimensions when canvasWrapper is absent', async () => {
    const wrapper = await mountView()
    wrapper.vm.canvasWrapper = null
    const pos = wrapper.vm._defaultMinimapPos()
    expect(pos).toEqual({ x: window.innerWidth - 208, y: window.innerHeight - 152 })
  })
})

// ── _onMinimapMouseDown ────────────────────────────────────────────────────

describe('_onMinimapMouseDown', () => {
  it('ignores non-left-button events', async () => {
    const wrapper = await mountView()
    const addSpy = vi.spyOn(window, 'addEventListener')
    wrapper.vm._onMinimapMouseDown({ button: 2, clientX: 0, clientY: 0 })
    expect(addSpy).not.toHaveBeenCalled()
    addSpy.mockRestore()
  })

  it('registers mousemove and mouseup listeners on left click', async () => {
    const wrapper = await mountView()
    const addSpy = vi.spyOn(window, 'addEventListener')
    wrapper.vm.minimapPos = { x: 50, y: 60 }
    wrapper.vm._onMinimapMouseDown({ button: 0, clientX: 10, clientY: 20 })
    expect(addSpy).toHaveBeenCalledWith('mousemove', expect.any(Function), { capture: true })
    expect(addSpy).toHaveBeenCalledWith('mouseup',   expect.any(Function), { capture: true })
    addSpy.mockRestore()
  })

  it('computes start pos from _defaultMinimapPos when minimapPos is null', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = null
    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ width: 800, height: 600 }) }
    const addSpy = vi.spyOn(window, 'addEventListener')
    wrapper.vm._onMinimapMouseDown({ button: 0, clientX: 5, clientY: 10 })
    expect(addSpy).toHaveBeenCalled()
    addSpy.mockRestore()
  })
})

// ── _onMinimapMouseMove ────────────────────────────────────────────────────

describe('_onMinimapMouseMove', () => {
  async function startDrag(wrapper, startX = 0, startY = 0) {
    wrapper.vm.minimapPos = { x: 100, y: 100 }
    wrapper.vm._onMinimapMouseDown({ button: 0, clientX: startX, clientY: startY })
  }

  it('returns early when movement is below the drag threshold', async () => {
    const wrapper = await mountView()
    await startDrag(wrapper)
    const initialPos = { ...wrapper.vm.minimapPos }
    wrapper.vm._onMinimapMouseMove({ clientX: 1, clientY: 1, stopImmediatePropagation: vi.fn() })
    expect(wrapper.vm.minimapPos).toEqual(initialPos)
    expect(wrapper.vm.minimapDragging).toBe(false)
  })

  it('starts dragging once movement exceeds the threshold', async () => {
    const wrapper = await mountView()
    await startDrag(wrapper)
    const stopFn = vi.fn()
    wrapper.vm._onMinimapMouseMove({ clientX: 10, clientY: 10, stopImmediatePropagation: stopFn })
    expect(wrapper.vm.minimapDragging).toBe(true)
    expect(stopFn).toHaveBeenCalled()
  })

  it('updates minimapPos while dragging', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = { x: 100, y: 100 }
    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ width: 800, height: 600 }) }
    await startDrag(wrapper, 0, 0)
    wrapper.vm._onMinimapMouseMove({ clientX: 20, clientY: 15, stopImmediatePropagation: vi.fn() })
    expect(wrapper.vm.minimapPos).toEqual({ x: 120, y: 115 })
  })

  it('clamps position to 0 minimum', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = { x: 5, y: 5 }
    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ width: 800, height: 600 }) }
    wrapper.vm._onMinimapMouseDown({ button: 0, clientX: 100, clientY: 100 })
    wrapper.vm._onMinimapMouseMove({ clientX: 10, clientY: 10, stopImmediatePropagation: vi.fn() })
    expect(wrapper.vm.minimapPos.x).toBeGreaterThanOrEqual(0)
    expect(wrapper.vm.minimapPos.y).toBeGreaterThanOrEqual(0)
  })

  it('clamps position to maxX/maxY when canvasWrapper is absent', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = { x: 100, y: 100 }
    wrapper.vm.canvasWrapper = null
    await startDrag(wrapper, 0, 0)
    // Without rect, maxX/maxY equal rawX/rawY so clamping still works
    wrapper.vm._onMinimapMouseMove({ clientX: 20, clientY: 15, stopImmediatePropagation: vi.fn() })
    expect(wrapper.vm.minimapPos).toEqual({ x: 120, y: 115 })
  })

  it('calls stopImmediatePropagation on every move after drag starts', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = { x: 100, y: 100 }
    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ width: 800, height: 600 }) }
    await startDrag(wrapper, 0, 0)
    // First move crosses threshold
    const stop1 = vi.fn()
    wrapper.vm._onMinimapMouseMove({ clientX: 20, clientY: 20, stopImmediatePropagation: stop1 })
    expect(stop1).toHaveBeenCalledOnce()
    // Second move — still dragging
    const stop2 = vi.fn()
    wrapper.vm._onMinimapMouseMove({ clientX: 30, clientY: 25, stopImmediatePropagation: stop2 })
    expect(stop2).toHaveBeenCalledOnce()
  })
})

// ── _onMinimapMouseUp ──────────────────────────────────────────────────────

describe('_onMinimapMouseUp', () => {
  it('saves position to localStorage when a drag occurred', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = { x: 100, y: 100 }
    wrapper.vm.canvasWrapper = { getBoundingClientRect: () => ({ width: 800, height: 600 }) }
    // Spy on the localStorage instance that mountView installed
    const setItemSpy = vi.spyOn(window.localStorage, 'setItem')
    // Simulate a full drag sequence
    wrapper.vm._onMinimapMouseDown({ button: 0, clientX: 0, clientY: 0 })
    wrapper.vm._onMinimapMouseMove({ clientX: 20, clientY: 20, stopImmediatePropagation: vi.fn() })
    wrapper.vm._onMinimapMouseUp({ stopImmediatePropagation: vi.fn() })

    expect(setItemSpy).toHaveBeenCalledWith(
      MINIMAP_POS_KEY,
      JSON.stringify(wrapper.vm.minimapPos),
    )
    expect(wrapper.vm.minimapDragging).toBe(false)
    setItemSpy.mockRestore()
  })

  it('does not save to localStorage when no drag occurred (simple click)', async () => {
    const storage = makeStorage()
    overrideStorage(storage)
    const wrapper = await mountView()
    wrapper.vm.minimapPos = { x: 100, y: 100 }
    // Mousedown then immediate mouseup without threshold movement
    wrapper.vm._onMinimapMouseDown({ button: 0, clientX: 0, clientY: 0 })
    const stopFn = vi.fn()
    wrapper.vm._onMinimapMouseUp({ stopImmediatePropagation: stopFn })

    expect(storage.setItem).not.toHaveBeenCalledWith(MINIMAP_POS_KEY, expect.any(String))
    expect(stopFn).not.toHaveBeenCalled()
    expect(wrapper.vm.minimapDragging).toBe(false)
  })

  it('removes window mousemove and mouseup listeners after mouseup', async () => {
    const wrapper = await mountView()
    wrapper.vm.minimapPos = { x: 100, y: 100 }
    wrapper.vm._onMinimapMouseDown({ button: 0, clientX: 0, clientY: 0 })
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    wrapper.vm._onMinimapMouseUp({ stopImmediatePropagation: vi.fn() })
    expect(removeSpy).toHaveBeenCalledWith('mousemove', expect.any(Function), { capture: true })
    expect(removeSpy).toHaveBeenCalledWith('mouseup',   expect.any(Function), { capture: true })
    removeSpy.mockRestore()
  })
})

// ── onUnmounted cleanup ────────────────────────────────────────────────────

describe('minimap cleanup on unmount', () => {
  it('removes window listeners on unmount', async () => {
    const wrapper = await mountView()
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    wrapper.unmount()
    expect(removeSpy).toHaveBeenCalledWith('mousemove', expect.any(Function), { capture: true })
    expect(removeSpy).toHaveBeenCalledWith('mouseup',   expect.any(Function), { capture: true })
    removeSpy.mockRestore()
  })
})
