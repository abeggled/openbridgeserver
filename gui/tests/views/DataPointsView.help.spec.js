/**
 * Integrated help drawer wiring on the Objekte/DataPoints list view (#896).
 *
 * The header, filter bar, and table each got a HelpButton pointing at a
 * help_id documented in help/datapoints/list.md. This spec checks the
 * buttons are present with the right help_id and that clicking one opens
 * the real help store.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  globalThis.IntersectionObserver = class {
    observe() {}
    disconnect() {}
  }
  vi.doMock('vue-router', () => ({
    onBeforeRouteLeave: vi.fn(),
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('vue-router')
})

async function mountDataPointsView() {
  vi.doMock('@/api/client', () => ({
    searchApi: { search: vi.fn().mockResolvedValue({ data: { items: [], total: 0, pages: 1 } }) },
    dpApi: { tags: vi.fn().mockResolvedValue({ data: [] }) },
    systemApi: { datatypes: vi.fn().mockResolvedValue({ data: [] }) },
    hierarchyApi: { searchNodes: vi.fn().mockResolvedValue({ data: [] }) },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'tester', is_admin: true }

  const { default: DataPointsView } = await import('@/views/DataPointsView.vue')
  const wrapper = mount(DataPointsView, {
    global: {
      plugins: [pinia],
      stubs: {
        AdapterCombobox: { template: '<div />' },
        Badge: { template: '<span><slot /></span>' },
        ConfirmDialog: true,
        DataPointForm: true,
        Modal: { template: '<div><slot /></div>', props: ['modelValue', 'title', 'dismissible'] },
        RouterLink: { props: ['to'], template: '<a><slot /></a>' },
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('DataPointsView — help buttons (#896)', () => {
  it.each(['datapoints-list', 'datapoints-filters', 'datapoints-table'])(
    'renders a help button for %s',
    async (helpId) => {
      const wrapper = await mountDataPointsView()
      expect(helpButton(wrapper, helpId).exists()).toBe(true)
    }
  )

  it.each(['datapoints-list', 'datapoints-filters', 'datapoints-table'])(
    'opens the help store with %s when its button is clicked',
    async (helpId) => {
      const wrapper = await mountDataPointsView()
      const { useHelpStore } = await import('@/stores/help')
      const helpStore = useHelpStore()

      await helpButton(wrapper, helpId).trigger('click')

      expect(helpStore.isOpen).toBe(true)
      expect(helpStore.currentHelpId).toBe(helpId)
    }
  )
})
