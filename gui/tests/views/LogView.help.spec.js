/**
 * Integrated help drawer wiring on the Logs/LogView view.
 *
 * The header (runtime log level) and the filters/table each got a HelpButton
 * pointing at a help_id documented in help/logs/overview.md. This spec
 * checks the buttons are present with the right help_id and that clicking
 * one opens the real help store.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/stores/websocket')
})

async function mountLogView() {
  vi.doMock('@/api/client', () => ({
    logsApi: {
      getLevel: vi.fn().mockResolvedValue({ data: { level: 'INFO' } }),
      list: vi.fn().mockResolvedValue({ data: [] }),
      setLevel: vi.fn().mockResolvedValue({}),
    },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({ connected: true, onLogEntry: () => vi.fn() }),
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: LogView } = await import('@/views/LogView.vue')
  const wrapper = mount(LogView, {
    global: {
      plugins: [pinia],
      stubs: {
        Badge: { template: '<span><slot /></span>' },
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('LogView — help buttons', () => {
  it.each(['logs-level', 'logs-table'])('renders a help button for %s', async (helpId) => {
    const wrapper = await mountLogView()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it.each(['logs-level', 'logs-table'])('opens the help store with %s when its button is clicked', async (helpId) => {
    const wrapper = await mountLogView()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, helpId).trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(helpId)
  })
})
