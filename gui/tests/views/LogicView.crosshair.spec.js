/**
 * Tests for the drag-time block-sized crosshair alignment overlay (#1118).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let mockViewport = { x: 0, y: 0, zoom: 1 }

beforeEach(() => {
  vi.resetModules()
  mockViewport = { x: 0, y: 0, zoom: 1 }
  const storage = {
    getItem: vi.fn().mockReturnValue(null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  }
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
  vi.doMock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))
  vi.doMock('@vue-flow/core', () => ({
    VueFlow: { name: 'VueFlow', props: ['snapToGrid', 'snapGrid'], template: '<div data-testid="vue-flow"><slot /></div>' },
    Handle: { template: '<span />' },
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    useVueFlow: () => ({ project: (point) => point, getViewport: () => mockViewport }),
    addEdge: (edge, edges) => [...edges, edge],
  }))
  vi.doMock('@vue-flow/background', () => ({ Background: { name: 'Background', props: ['gap', 'offset'], template: '<div />' } }))
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

function makeGraph(id = 'graph-1', overrides = {}) {
  return {
    id,
    name: 'Main Graph',
    description: '',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'n1', type: 'and', position: { x: 0, y: 0 }, data: { input_count: 2 } },
      ],
      edges: [],
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

describe('LogicView drag crosshair overlay', () => {
  it('starts with no overlay', async () => {
    const { wrapper } = await mountLogicView()
    expect(wrapper.vm.dragCrosshair).toBeNull()
    expect(wrapper.find('[data-testid="logic-crosshair-overlay"]').exists()).toBe(false)
  })

  it('onNodeDragStart derives the overlay rect from the dragged node position/dimensions and the current viewport', async () => {
    const { wrapper } = await mountLogicView()
    mockViewport = { x: 10, y: 20, zoom: 2 }
    const node = { position: { x: 50, y: 30 }, dimensions: { width: 80, height: 40 } }

    wrapper.vm.onNodeDragStart({ node })

    expect(wrapper.vm.dragCrosshair).toEqual({
      left: 50 * 2 + 10,
      top: 30 * 2 + 20,
      width: 80 * 2,
      height: 40 * 2,
    })
  })

  it('renders the crosshair bars sized/positioned from the overlay rect once dragging starts', async () => {
    const { wrapper } = await mountLogicView()
    const node = { position: { x: 5, y: 7 }, dimensions: { width: 60, height: 30 } }

    wrapper.vm.onNodeDragStart({ node })
    await wrapper.vm.$nextTick()

    const overlay = wrapper.find('[data-testid="logic-crosshair-overlay"]')
    expect(overlay.exists()).toBe(true)
    const [hBar, vBar] = overlay.findAll('div')
    // Positioned via `transform`, not top/left — see the CSS comment: this
    // keeps the overlay on the compositor thread so it can't visibly fall a
    // step behind the (also transform-positioned) dragged node.
    expect(hBar.attributes('style')).toContain('transform: translateY(7px)')
    expect(hBar.attributes('style')).toContain('height: 30px')
    expect(vBar.attributes('style')).toContain('transform: translateX(5px)')
    expect(vBar.attributes('style')).toContain('width: 60px')
  })

  it('onNodeDrag keeps the overlay updated as the node moves', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.onNodeDragStart({ node: { position: { x: 0, y: 0 }, dimensions: { width: 10, height: 10 } } })

    wrapper.vm.onNodeDrag({ node: { position: { x: 100, y: 200 }, dimensions: { width: 10, height: 10 } } })

    expect(wrapper.vm.dragCrosshair).toMatchObject({ left: 100, top: 200 })
  })

  it('uses position rather than computedPosition, which vue-flow recalculates via a deferred watch() and can still lag one axis behind mid-drag', async () => {
    const { wrapper } = await mountLogicView()
    const node = { position: { x: 0, y: 0 }, computedPosition: { x: 15, y: 25 }, dimensions: { width: 10, height: 10 } }

    wrapper.vm.onNodeDragStart({ node })

    expect(wrapper.vm.dragCrosshair).toMatchObject({ left: 0, top: 0 })
  })

  it('onNodeDragStop clears the overlay', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.onNodeDragStart({ node: { position: { x: 0, y: 0 }, dimensions: { width: 10, height: 10 } } })
    expect(wrapper.vm.dragCrosshair).not.toBeNull()

    wrapper.vm.onNodeDragStop()

    expect(wrapper.vm.dragCrosshair).toBeNull()
  })

  it('clears the overlay when the dragged node has no measured dimensions yet', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.onNodeDragStart({ node: { position: { x: 0, y: 0 }, dimensions: { width: 10, height: 10 } } })

    wrapper.vm.onNodeDrag({ node: { position: { x: 0, y: 0 }, dimensions: { width: 0, height: 0 } } })

    expect(wrapper.vm.dragCrosshair).toBeNull()
  })

  it('clears the overlay when no dimensions object is present at all', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.onNodeDragStart({ node: { position: { x: 0, y: 0 } } })

    expect(wrapper.vm.dragCrosshair).toBeNull()
  })

  it('clears the overlay when width is measured but height is still zero', async () => {
    const { wrapper } = await mountLogicView()
    wrapper.vm.onNodeDragStart({ node: { position: { x: 0, y: 0 }, dimensions: { width: 10, height: 0 } } })

    expect(wrapper.vm.dragCrosshair).toBeNull()
  })
})
