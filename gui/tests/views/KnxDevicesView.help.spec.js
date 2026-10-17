/**
 * Integrated help drawer wiring on the KNX-Geräte/KnxDevices view.
 *
 * The header, filter form, device table, and device detail panel each got a
 * HelpButton pointing at a help_id documented in help/knxdevices/list.md.
 * This spec checks the buttons are present with the right help_id and that
 * clicking one opens the real help store.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountKnxDevicesView() {
  vi.doMock('@/api/client', () => ({
    knxprojApi: {
      listDevices: vi.fn().mockResolvedValue({ data: { items: [], total: 0, page: 0, size: 25, pages: 1 } }),
      getDevice: vi.fn(),
      setDeviceHierarchyLinks: vi.fn(),
    },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'tester', is_admin: true }

  const { default: KnxDevicesView } = await import('@/views/KnxDevicesView.vue')
  const wrapper = mount(KnxDevicesView, {
    global: {
      plugins: [pinia],
      stubs: {
        HierarchyCombobox: { template: '<div />' },
        PathLabel: { template: '<span />' },
        QuickFilterInput: { template: '<input />' },
        RouterLink: { props: ['to'], template: '<a><slot /></a>' },
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

describe('KnxDevicesView — help buttons', () => {
  const helpIds = ['knxdevices-list', 'knxdevices-filters', 'knxdevices-table', 'knxdevices-detail']

  it.each(helpIds)('renders a help button for %s', async (helpId) => {
    const wrapper = await mountKnxDevicesView()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it.each(helpIds)('opens the help store with %s when its button is clicked', async (helpId) => {
    const wrapper = await mountKnxDevicesView()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, helpId).trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(helpId)
  })
})
