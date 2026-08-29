/**
 * Integrated help drawer wiring on the Logikmodul/LogicView view.
 *
 * The toolbar and the canvas each got a HelpButton pointing at a help_id
 * documented in help/logic/overview.md (the block-config panel's own
 * help_id is covered separately in NodeConfigPanel.help.spec.js). This spec
 * checks the buttons are present with the right help_id and that clicking
 * one opens the real help store.
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

async function mountLogicView() {
  vi.doMock('@/api/client', () => ({
    logicApi: {
      nodeTypes: vi.fn().mockResolvedValue({ data: [] }),
      listGraphs: vi.fn().mockResolvedValue({ data: [] }),
    },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))

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

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('LogicView — help buttons', () => {
  it.each(['logic-toolbar', 'logic-canvas'])('renders a help button for %s', async (helpId) => {
    const wrapper = await mountLogicView()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it.each(['logic-toolbar', 'logic-canvas'])('opens the help store with %s when its button is clicked', async (helpId) => {
    const wrapper = await mountLogicView()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, helpId).trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(helpId)
  })
})
