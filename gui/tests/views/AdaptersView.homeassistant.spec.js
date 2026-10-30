import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const STUBS = {
  SchemaForm:                        { template: '<div class="schema-form" />' },
  KnxConfigForm:                     { template: '<div class="knx-form" />' },
  AnwesenheitConfigForm:             { template: '<div class="anwesenheit-form" />' },
  ZeitschaltuhrCustomHolidaysEditor: { template: '<div />' },
  AnwesenheitDatapointSelector:      { template: '<div class="anwesenheit-selector" />' },
  HomeAssistantImport:               { template: '<div class="ha-import" :data-instance="instance.id" />', props: ['instance'] },
  Spinner:  { template: '<span class="spinner" />' },
  Badge:    { template: '<span class="badge"><slot /></span>' },
  Modal: {
    template: '<div v-if="modelValue" class="modal"><button class="modal-close" @click="$emit(\'update:modelValue\', false)" /><slot /></div>',
    props: ['modelValue', 'title', 'maxWidth', 'resizable'],
    emits: ['update:modelValue'],
  },
  ConfirmDialog: {
    template: '<div v-if="modelValue"><button data-testid="confirm-btn" @click="$emit(\'confirm\')" /></div>',
    props: { modelValue: Boolean, title: String, message: String, confirmLabel: String },
    emits: ['confirm', 'update:modelValue'],
  },
}

function makeInstance(overrides = {}) {
  return {
    id: 1,
    adapter_type: 'HOME_ASSISTANT',
    name: 'HA Main',
    running: true,
    connected: true,
    severity: 'ok',
    registered: true,
    bindings: 0,
    config: { host: '192.168.1.100' },
    enabled: true,
    status_detail: '',
    status_detail_code: null,
    status_detail_params: {},
    ...overrides,
  }
}

let adapterApiMock

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  adapterApiMock = {
    listInstances:         vi.fn().mockResolvedValue({ data: [] }),
    list:                  vi.fn().mockResolvedValue({ data: [] }),
    schema:                vi.fn().mockResolvedValue({ data: { type: 'object', properties: {} } }),
    createInstance:        vi.fn().mockResolvedValue({ data: makeInstance({ id: 99 }) }),
    updateInstance:        vi.fn().mockResolvedValue({ data: makeInstance() }),
    deleteInstance:        vi.fn().mockResolvedValue({}),
    testInstance:          vi.fn().mockResolvedValue({ data: { success: true, detail: 'OK' } }),
    restartInstance:       vi.fn().mockResolvedValue({ data: makeInstance() }),
    migrateBindings:       vi.fn().mockResolvedValue({ data: { migrated: 1, skipped: 0 } }),
    anwesenheitHealth:     vi.fn().mockRejectedValue(new Error('n/a')),
    iobrokerImportPreview: vi.fn().mockResolvedValue({ data: { preview: [] } }),
    iobrokerImport:        vi.fn().mockResolvedValue({ data: { created_datapoints: 0, created_bindings: 0, skipped_existing: 0 } }),
  }
  vi.doMock('@/api/client', () => ({
    adapterApi:    adapterApiMock,
    authApi:       { login: vi.fn(), me: vi.fn() },
    knxKeyfileApi: {},
    searchApi:     { search: vi.fn().mockResolvedValue({ data: { items: [], total: 0, pages: 0 } }) },
    settingsApi:   { get: vi.fn().mockResolvedValue({ data: {} }) },
    navLinksApi:   { list: vi.fn().mockResolvedValue({ data: [] }) },
    dpApi: {},
    systemApi: {},
  }))
})

afterEach(() => {
  vi.useRealTimers()
  vi.doUnmock('@/api/client')
})

async function mountAdapters({ instances = [], types = [] } = {}) {
  adapterApiMock.listInstances.mockResolvedValue({ data: instances })
  adapterApiMock.list.mockResolvedValue({
    data: types.map(t => ({ adapter_type: t, hidden: false })),
  })

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
  return { wrapper }
}

async function expandInstance(wrapper, id = 1) {
  await wrapper.find(`[data-testid="btn-expand-${id}"]`).trigger('click')
  await flushPromises()
}

// ─── Home Assistant import (#1392) ────────────────────────────────────────────

describe('AdaptersView — Home Assistant import', () => {
  it('opens the entity import for a connected HOME_ASSISTANT instance', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    const btn = wrapper.find('[data-testid="btn-ha-import"]')
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.ha-import').exists()).toBe(false)
    await btn.trigger('click')
    await flushPromises()
    expect(wrapper.find('.ha-import').attributes('data-instance')).toBe('1')
    await wrapper.find('.modal-close').trigger('click')
    expect(wrapper.find('.ha-import').exists()).toBe(false)
  })

  it('disables the import while disconnected and hides it for other types', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance({ connected: false }), makeInstance({ id: 2, adapter_type: 'KNX' })] })
    await expandInstance(wrapper, 1)
    await expandInstance(wrapper, 2)
    const buttons = wrapper.findAll('[data-testid="btn-ha-import"]')
    expect(buttons).toHaveLength(1)
    expect(buttons[0].attributes('disabled')).toBeDefined()
  })
})
