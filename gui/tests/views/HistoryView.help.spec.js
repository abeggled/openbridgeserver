/**
 * Integrated help drawer wiring on the Historie/HistoryView view.
 *
 * The controls header and the chart/raw-data card each got a HelpButton
 * pointing at a help_id documented in help/history/overview.md. This spec
 * checks the buttons are present with the right help_id and that clicking
 * one opens the real help store.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('chart.js', () => ({
    Chart: vi.fn().mockImplementation(function () { this.destroy = vi.fn() }),
    LineController: {}, LineElement: {}, PointElement: {}, LinearScale: {},
    TimeScale: {}, Tooltip: {}, Legend: {}, registerables: [],
  }))
  vi.doMock('chart.js/auto', () => ({}))
  vi.doMock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))
})

afterEach(() => {
  vi.doUnmock('chart.js')
  vi.doUnmock('chart.js/auto')
  vi.doUnmock('vue-router')
  vi.doUnmock('@/api/client')
})

async function mountHistoryView() {
  vi.doMock('@/api/client', () => ({
    historyApi: { aggregate: vi.fn().mockResolvedValue({ data: [] }), query: vi.fn().mockResolvedValue({ data: [] }) },
    dpApi: { get: vi.fn() },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: HistoryView } = await import('@/views/HistoryView.vue')
  const wrapper = mount(HistoryView, {
    global: {
      plugins: [pinia],
      stubs: {
        DpCombobox: { template: '<div />' },
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

describe('HistoryView — help buttons', () => {
  it.each(['history-controls', 'history-results'])('renders a help button for %s', async (helpId) => {
    const wrapper = await mountHistoryView()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it.each(['history-controls', 'history-results'])(
    'opens the help store with %s when its button is clicked',
    async (helpId) => {
      const wrapper = await mountHistoryView()
      const { useHelpStore } = await import('@/stores/help')
      const helpStore = useHelpStore()

      await helpButton(wrapper, helpId).trigger('click')

      expect(helpStore.isOpen).toBe(true)
      expect(helpStore.currentHelpId).toBe(helpId)
    }
  )
})
