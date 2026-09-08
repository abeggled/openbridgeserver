/**
 * Tests for the "open graph" button + GraphPickerModal wiring in LogicView.vue
 * (#1217) — replaces the old flat <select>.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('vue-router', () => ({ useRoute: () => ({ query: {} }), useRouter: () => ({ push: vi.fn() }) }))
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

const GRAPH_A = { id: 'graph-a', name: 'Sheet A', description: '', enabled: true, flow_data: { nodes: [], edges: [] } }
const GRAPH_B = { id: 'graph-b', name: 'Sheet B', description: '', enabled: true, flow_data: { nodes: [], edges: [] } }

const GraphPickerModalStub = {
  name: 'GraphPickerModal',
  props: ['modelValue'],
  emits: ['update:modelValue', 'select'],
  template: '<div v-if="modelValue" data-testid="graph-picker-stub" />',
}

async function mountLogicView() {
  const logicApi = {
    nodeTypes:  vi.fn().mockResolvedValue({ data: [] }),
    listGraphs: vi.fn().mockResolvedValue({ data: [GRAPH_A, GRAPH_B] }),
    getGraph:   vi.fn().mockImplementation((id) => Promise.resolve({ data: id === 'graph-b' ? GRAPH_B : GRAPH_A })),
    saveGraph:  vi.fn().mockResolvedValue({ data: GRAPH_A }),
  }
  vi.doMock('@/api/client', () => ({ logicApi }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/views/LogicView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: { Modal: true, ConfirmDialog: true, NodePalette: true, GraphPickerModal: GraphPickerModalStub },
    },
    attachTo: document.body,
  })
  mountedWrappers.push(wrapper)
  await flushPromises()
  return { wrapper, logicApi }
}

describe('LogicView — graph picker button (#1217)', () => {
  it('shows the placeholder label before any graph is open', async () => {
    const { wrapper } = await mountLogicView()
    expect(wrapper.find('[data-testid="btn-open-graph-picker"]').text()).not.toContain('Sheet')
  })

  it('clicking the button opens the picker', async () => {
    const { wrapper } = await mountLogicView()
    expect(wrapper.find('[data-testid="graph-picker-stub"]').exists()).toBe(false)
    await wrapper.find('[data-testid="btn-open-graph-picker"]').trigger('click')
    expect(wrapper.find('[data-testid="graph-picker-stub"]').exists()).toBe(true)
  })

  it('a select event from the picker loads that graph and shows its name on the button', async () => {
    const { wrapper, logicApi } = await mountLogicView()
    await wrapper.find('[data-testid="btn-open-graph-picker"]').trigger('click')

    const picker = wrapper.findComponent(GraphPickerModalStub)
    picker.vm.$emit('select', 'graph-b')
    await flushPromises()

    expect(logicApi.getGraph).toHaveBeenCalledWith('graph-b')
    expect(wrapper.find('[data-testid="btn-open-graph-picker"]').text()).toContain('Sheet B')
  })
})
