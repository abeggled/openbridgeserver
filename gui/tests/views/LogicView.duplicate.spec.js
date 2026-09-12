/**
 * Duplicate-with-name-prompt (#1233 follow-up): "Duplizieren" no longer
 * duplicates immediately with a hardcoded "Kopie von …" name — it opens a
 * modal prefilled with the current name + " (Kopie)", editable before
 * confirming.
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

const GRAPH = {
  id: 'graph-1',
  name: 'Beschattung',
  description: '',
  enabled: true,
  flow_data: { nodes: [], edges: [] },
}

async function mountLogicView() {
  const logicApi = {
    nodeTypes:      vi.fn().mockResolvedValue({ data: [] }),
    listGraphs:     vi.fn().mockResolvedValue({ data: [GRAPH] }),
    getGraph:       vi.fn().mockResolvedValue({ data: structuredClone(GRAPH) }),
    saveGraph:      vi.fn().mockResolvedValue({ data: GRAPH }),
    duplicateGraph: vi.fn().mockImplementation((id, name) =>
      Promise.resolve({ data: { ...GRAPH, id: 'graph-copy', name } })),
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
      stubs: {
        NodePalette: true,
        ConfirmDialog: true,
        Modal: { template: '<div><slot /><slot name="footer" /></div>' },
      },
    },
    attachTo: document.body,
  })
  mountedWrappers.push(wrapper)
  await flushPromises()
  wrapper.vm.activeGraphId = GRAPH.id
  await wrapper.vm.loadGraph()
  await flushPromises()
  return { wrapper, logicApi }
}

describe('LogicView — duplicate-with-name-prompt (#1233 follow-up)', () => {
  it('clicking "Duplizieren" opens the modal prefilled, without duplicating yet', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    await wrapper.find('[data-testid="btn-duplicate"]').trigger('click')

    expect(logicApi.duplicateGraph).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="input-duplicate-name"]').element.value).toBe('Beschattung (Kopie)')
  })

  it('confirming with the prefilled name duplicates and switches to the copy', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    await wrapper.find('[data-testid="btn-duplicate"]').trigger('click')
    await wrapper.find('[data-testid="btn-duplicate-confirm"]').trigger('click')
    await flushPromises()

    expect(logicApi.duplicateGraph).toHaveBeenCalledWith('graph-1', 'Beschattung (Kopie)')
    expect(wrapper.vm.activeGraphId).toBe('graph-copy')
  })

  it('a custom name is honored instead of the prefill', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    await wrapper.find('[data-testid="btn-duplicate"]').trigger('click')
    await wrapper.find('[data-testid="input-duplicate-name"]').setValue('Beschattung Süd')
    await wrapper.find('[data-testid="btn-duplicate-confirm"]').trigger('click')
    await flushPromises()

    expect(logicApi.duplicateGraph).toHaveBeenCalledWith('graph-1', 'Beschattung Süd')
  })

  it('cancelling discards the prefilled name without duplicating', async () => {
    const { wrapper, logicApi } = await mountLogicView()

    await wrapper.find('[data-testid="btn-duplicate"]').trigger('click')
    await wrapper.find('[data-testid="btn-duplicate-cancel"]').trigger('click')
    await flushPromises()

    expect(wrapper.vm.showDuplicate).toBe(false)
    expect(logicApi.duplicateGraph).not.toHaveBeenCalled()
  })
})
