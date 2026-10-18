/**
 * Integrated help drawer wiring on the Adapter/AdaptersView view.
 *
 * The header, the "create new instance" form, and each expanded instance's
 * action panel each got a HelpButton pointing at a help_id documented in
 * help/adapters/list.md. This spec checks the buttons are present with the
 * right help_id and that clicking one opens the real help store.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const STUBS = {
  SchemaForm:                        { template: '<div />' },
  KnxConfigForm:                     { template: '<div />' },
  AnwesenheitConfigForm:             { template: '<div />' },
  ZeitschaltuhrCustomHolidaysEditor: { template: '<div />' },
  AnwesenheitDatapointSelector:      { template: '<div />' },
  Spinner:  { template: '<span />' },
  Badge:    { template: '<span><slot /></span>' },
  Modal:        { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue', 'title', 'maxWidth', 'resizable'] },
  ConfirmDialog: { template: '<div />', props: ['modelValue', 'title', 'message', 'confirmLabel'] },
}

function makeInstance(overrides = {}) {
  return {
    id: 1,
    adapter_type: 'KNX',
    name: 'KNX Main',
    running: true,
    connected: true,
    severity: 'ok',
    registered: true,
    bindings: 5,
    config: { host: '192.168.1.1' },
    enabled: true,
    status_detail: '',
    status_detail_code: null,
    status_detail_params: {},
    ...overrides,
  }
}

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountAdaptersView({ instances = [] } = {}) {
  vi.doMock('@/api/client', () => ({
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({ data: instances }),
      list: vi.fn().mockResolvedValue({ data: [] }),
      schema: vi.fn().mockResolvedValue({ data: { type: 'object', properties: {} } }),
      anwesenheitHealth: vi.fn().mockRejectedValue(new Error('n/a')),
    },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { username: 'admin', is_admin: true }

  const { default: AdaptersView } = await import('@/views/AdaptersView.vue')
  const wrapper = mount(AdaptersView, {
    global: { plugins: [pinia], stubs: STUBS },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

async function expectOpensHelpStore(wrapper, helpId) {
  const { useHelpStore } = await import('@/stores/help')
  const helpStore = useHelpStore()
  await helpButton(wrapper, helpId).trigger('click')
  expect(helpStore.isOpen).toBe(true)
  expect(helpStore.currentHelpId).toBe(helpId)
}

describe('AdaptersView — help buttons', () => {
  it('renders a help button for the instance list in the header', async () => {
    const wrapper = await mountAdaptersView()
    expect(helpButton(wrapper, 'adapters-list').exists()).toBe(true)
  })

  it('opens the help store with adapters-list when its button is clicked', async () => {
    const wrapper = await mountAdaptersView()
    await expectOpensHelpStore(wrapper, 'adapters-list')
  })

  it('renders a help button on the create-instance form once opened', async () => {
    const wrapper = await mountAdaptersView()
    expect(helpButton(wrapper, 'adapters-create').exists()).toBe(false)
    await wrapper.find('[data-testid="btn-new-instance"]').trigger('click')
    expect(helpButton(wrapper, 'adapters-create').exists()).toBe(true)
  })

  it('opens the help store with adapters-create when its button is clicked', async () => {
    const wrapper = await mountAdaptersView()
    await wrapper.find('[data-testid="btn-new-instance"]').trigger('click')
    await expectOpensHelpStore(wrapper, 'adapters-create')
  })

  it('renders a help button in an expanded instance panel', async () => {
    const wrapper = await mountAdaptersView({ instances: [makeInstance()] })
    expect(helpButton(wrapper, 'adapters-instance-actions').exists()).toBe(false)
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    expect(helpButton(wrapper, 'adapters-instance-actions').exists()).toBe(true)
  })

  it('opens the help store with adapters-instance-actions when its button is clicked', async () => {
    const wrapper = await mountAdaptersView({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await expectOpensHelpStore(wrapper, 'adapters-instance-actions')
  })
})
