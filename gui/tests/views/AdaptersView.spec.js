import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// ─── Stubs ───────────────────────────────────────────────────────────────────

const STUBS = {
  SchemaForm:                        { template: '<div class="schema-form" />' },
  KnxConfigForm:                     { template: '<div class="knx-form" />' },
  AnwesenheitConfigForm:             { template: '<div class="anwesenheit-form" />' },
  ZeitschaltuhrCustomHolidaysEditor: { template: '<div />' },
  AnwesenheitDatapointSelector:      { template: '<div />' },
  Spinner:  { template: '<span class="spinner" />' },
  Badge:    { template: '<span class="badge"><slot /></span>' },
  Modal: {
    template: '<div v-if="modelValue" class="modal"><slot /></div>',
    props: ['modelValue', 'title', 'maxWidth', 'resizable'],
    emits: ['update:modelValue'],
  },
  ConfirmDialog: {
    template: '<div v-if="modelValue"><button data-testid="confirm-btn" @click="$emit(\'confirm\')" /></div>',
    props: { modelValue: Boolean, title: String, message: String, confirmLabel: String },
    emits: ['confirm', 'update:modelValue'],
  },
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

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
    testInstance:          vi.fn().mockResolvedValue({ data: { success: true, detail: 'Connected!' } }),
    restartInstance:       vi.fn().mockResolvedValue({ data: makeInstance() }),
    migrateBindings:       vi.fn().mockResolvedValue({ data: { migrated: 3, skipped: 0 } }),
    anwesenheitHealth:     vi.fn().mockRejectedValue(new Error('n/a')),
    iobrokerImportPreview: vi.fn(),
    iobrokerImport:        vi.fn(),
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

async function mountAdapters({ instances = [], types = [], username = 'admin' } = {}) {
  adapterApiMock.listInstances.mockResolvedValue({ data: instances })
  adapterApiMock.list.mockResolvedValue({
    data: types.map(t => ({ adapter_type: t, hidden: false })),
  })

  const pinia = createPinia()
  setActivePinia(pinia)

  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { username, is_admin: username !== 'demo' }

  const { default: AdaptersView } = await import('@/views/AdaptersView.vue')
  const wrapper = mount(AdaptersView, {
    global: { plugins: [pinia], stubs: STUBS },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper }
}

// ─── Initial render ───────────────────────────────────────────────────────────

describe('AdaptersView — initial render', () => {
  it('shows empty-state when there are no instances', async () => {
    const { wrapper } = await mountAdapters({ instances: [] })
    // German translation for adapters.noInstances
    expect(wrapper.text()).toContain('Keine Adapter-Instanzen konfiguriert')
  })

  it('renders one card row per instance', async () => {
    const { wrapper } = await mountAdapters({
      instances: [makeInstance({ id: 1 }), makeInstance({ id: 2, name: 'MQTT' })],
    })
    expect(wrapper.find('[data-testid="adapter-row-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="adapter-row-2"]').exists()).toBe(true)
  })

  it('shows instance name and adapter_type in each row', async () => {
    const { wrapper } = await mountAdapters({
      instances: [makeInstance({ name: 'KNX Test', adapter_type: 'KNX' })],
    })
    expect(wrapper.text()).toContain('KNX Test')
    expect(wrapper.text()).toContain('KNX')
  })

  it('populates the type selector with available types after opening the create form', async () => {
    const { wrapper } = await mountAdapters({ types: ['KNX', 'MQTT'] })
    await wrapper.find('[data-testid="btn-new-instance"]').trigger('click')
    await flushPromises()
    const select = wrapper.find('[data-testid="select-adapter-type"]')
    expect(select.html()).toContain('KNX')
    expect(select.html()).toContain('MQTT')
  })
})

// ─── Demo mode ───────────────────────────────────────────────────────────────

describe('AdaptersView — demo mode', () => {
  it('hides the "New Instance" button for demo user', async () => {
    const { wrapper } = await mountAdapters({ username: 'demo' })
    expect(wrapper.find('[data-testid="btn-new-instance"]').exists()).toBe(false)
  })

  it('shows the "New Instance" button for admin user', async () => {
    const { wrapper } = await mountAdapters({ username: 'admin' })
    expect(wrapper.find('[data-testid="btn-new-instance"]').exists()).toBe(true)
  })

  it('shows demo-mode banner for demo user', async () => {
    const { wrapper } = await mountAdapters({ username: 'demo' })
    // German translation: "Demo-Modus — Ansicht ist schreibgeschützt."
    expect(wrapper.text()).toContain('Demo-Modus')
  })
})

// ─── Status detail ────────────────────────────────────────────────────────────

describe('AdaptersView — status detail panel', () => {
  it('shows detail panel for adapter with severity=error and status_detail', async () => {
    const { wrapper } = await mountAdapters({
      instances: [makeInstance({ severity: 'error', status_detail: 'connection refused' })],
    })
    expect(wrapper.find('[data-testid="adapter-status-detail-1"]').exists()).toBe(true)
  })

  it('hides detail panel for adapter with severity=ok', async () => {
    const { wrapper } = await mountAdapters({
      instances: [makeInstance({ severity: 'ok', status_detail: '' })],
    })
    expect(wrapper.find('[data-testid="adapter-status-detail-1"]').exists()).toBe(false)
  })

  it('hides detail panel when severity=warning but no detail text', async () => {
    const { wrapper } = await mountAdapters({
      instances: [makeInstance({ severity: 'warning', status_detail: '', status_detail_code: null })],
    })
    expect(wrapper.find('[data-testid="adapter-status-detail-1"]').exists()).toBe(false)
  })
})

// ─── New instance form ────────────────────────────────────────────────────────

describe('AdaptersView — new instance form', () => {
  it('shows the create form when clicking "New Instance"', async () => {
    const { wrapper } = await mountAdapters()
    await wrapper.find('[data-testid="btn-new-instance"]').trigger('click')
    expect(wrapper.find('[data-testid="select-adapter-type"]').exists()).toBe(true)
  })

  it('hides the create form when clicking Abbrechen', async () => {
    const { wrapper } = await mountAdapters()
    await wrapper.find('[data-testid="btn-new-instance"]').trigger('click')
    // Find the "Abbrechen" cancel button (German for common.cancel)
    const cancelBtn = wrapper.findAll('button').find(b => b.text() === 'Abbrechen')
    await cancelBtn.trigger('click')
    expect(wrapper.find('[data-testid="select-adapter-type"]').exists()).toBe(false)
  })

  it('shows validation error when submitting without type/name', async () => {
    const { wrapper } = await mountAdapters()
    await wrapper.find('[data-testid="btn-new-instance"]').trigger('click')
    await wrapper.find('[data-testid="btn-save-instance"]').trigger('click')
    await flushPromises()
    // German: "Bitte Typ und Name ausfüllen."
    expect(wrapper.text()).toContain('Bitte Typ und Name ausfüllen')
  })

  it('creates instance and closes form when type and name are provided', async () => {
    const newInst = makeInstance({ id: 99, name: 'My KNX', adapter_type: 'KNX' })
    adapterApiMock.createInstance.mockResolvedValue({ data: newInst })
    adapterApiMock.listInstances.mockResolvedValue({ data: [] })

    const { wrapper } = await mountAdapters({ types: ['KNX'] })

    // Get the store so we can spy on createInstance
    const { useAdapterStore } = await import('@/stores/adapters')
    const adStore = useAdapterStore()
    const createSpy = vi.spyOn(adStore, 'createInstance').mockResolvedValue(newInst)

    await wrapper.find('[data-testid="btn-new-instance"]').trigger('click')

    // Set adapter type
    const typeSelect = wrapper.find('[data-testid="select-adapter-type"]')
    await typeSelect.setValue('KNX')
    await typeSelect.trigger('change')
    await flushPromises()

    // Set name
    await wrapper.find('[data-testid="input-instance-name"]').setValue('My KNX')

    await wrapper.find('[data-testid="btn-save-instance"]').trigger('click')
    await flushPromises()

    expect(createSpy).toHaveBeenCalledWith('KNX', 'My KNX', expect.any(Object))
    // Form closes on success
    expect(wrapper.find('[data-testid="select-adapter-type"]').exists()).toBe(false)
  })

  it('shows API error message on create failure', async () => {
    adapterApiMock.createInstance.mockRejectedValue({
      response: { data: { detail: 'Duplicate name' } },
    })
    const { wrapper } = await mountAdapters({ types: ['KNX'] })

    const { useAdapterStore } = await import('@/stores/adapters')
    vi.spyOn(useAdapterStore(), 'createInstance').mockRejectedValue({
      response: { data: { detail: 'Duplicate name' } },
    })

    await wrapper.find('[data-testid="btn-new-instance"]').trigger('click')
    const typeSelect = wrapper.find('[data-testid="select-adapter-type"]')
    await typeSelect.setValue('KNX')
    await typeSelect.trigger('change')
    await flushPromises()
    await wrapper.find('[data-testid="input-instance-name"]').setValue('bad')
    await wrapper.find('[data-testid="btn-save-instance"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Duplicate name')
  })
})

// ─── Expand / collapse ────────────────────────────────────────────────────────

describe('AdaptersView — expand / collapse', () => {
  it('reveals action buttons when expand button is clicked', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    // delete button is inside expanded panel (v-if="expanded[a.id]" + v-if="!isDemo")
    expect(wrapper.find('[data-testid="btn-delete-instance"]').exists()).toBe(false)

    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="btn-delete-instance"]').exists()).toBe(true)
  })

  it('collapses the card on a second expand-button click', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="btn-delete-instance"]').exists()).toBe(false)
  })

  it('loads the adapter schema when expanding', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance({ adapter_type: 'MQTT' })] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    expect(adapterApiMock.schema).toHaveBeenCalledWith('MQTT')
  })
})

// ─── Save instance ────────────────────────────────────────────────────────────

describe('AdaptersView — save instance', () => {
  it('calls store.updateInstance when Speichern is clicked', async () => {
    adapterApiMock.updateInstance.mockResolvedValue({ data: makeInstance() })
    adapterApiMock.listInstances.mockResolvedValue({ data: [makeInstance()] })

    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    // Find "Speichern" (German for common.save) — excludes the create form's "Erstellen"
    const saveBtn = wrapper.findAll('button').find(b => b.text() === 'Speichern')
    await saveBtn.trigger('click')
    await flushPromises()

    expect(adapterApiMock.updateInstance).toHaveBeenCalledWith(1, expect.any(Object))
    // Shows green success feedback
    expect(wrapper.html()).toContain('bg-green-500/10')
  })

  it('shows error feedback when save fails', async () => {
    adapterApiMock.updateInstance.mockRejectedValue({
      response: { data: { detail: 'Validation error' } },
    })

    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    const saveBtn = wrapper.findAll('button').find(b => b.text() === 'Speichern')
    await saveBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Validation error')
  })
})

// ─── Delete flow ──────────────────────────────────────────────────────────────

describe('AdaptersView — delete flow', () => {
  it('shows the confirm dialog on clicking Löschen', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="btn-delete-instance"]').trigger('click')

    expect(wrapper.find('[data-testid="confirm-btn"]').exists()).toBe(true)
  })

  it('calls store.deleteInstance after confirming', async () => {
    adapterApiMock.deleteInstance.mockResolvedValue({})
    adapterApiMock.listInstances.mockResolvedValue({ data: [] })

    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="btn-delete-instance"]').trigger('click')
    await wrapper.find('[data-testid="confirm-btn"]').trigger('click')
    await flushPromises()

    expect(adapterApiMock.deleteInstance).toHaveBeenCalledWith(1)
  })
})

// ─── Test connection ──────────────────────────────────────────────────────────

describe('AdaptersView — test connection', () => {
  it('calls testInstance and shows success feedback', async () => {
    adapterApiMock.testInstance.mockResolvedValue({ data: { success: true, detail: 'Connected!' } })
    adapterApiMock.listInstances.mockResolvedValue({ data: [makeInstance()] })

    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    // German: "Verbindung testen"
    const testBtn = wrapper.findAll('button').find(b => b.text() === 'Verbindung testen')
    await testBtn.trigger('click')
    await flushPromises()

    expect(adapterApiMock.testInstance).toHaveBeenCalledWith(1, expect.any(Object))
    expect(wrapper.html()).toContain('bg-green-500/10')
  })

  it('shows error feedback when testInstance fails', async () => {
    adapterApiMock.testInstance.mockRejectedValue({
      response: { data: { detail: 'Timeout' } },
    })

    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    const testBtn = wrapper.findAll('button').find(b => b.text() === 'Verbindung testen')
    await testBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Timeout')
  })
})

// ─── Restart instance ─────────────────────────────────────────────────────────

describe('AdaptersView — restart instance', () => {
  it('calls restartInstance and shows success feedback', async () => {
    adapterApiMock.restartInstance.mockResolvedValue({ data: makeInstance({ connected: true }) })
    adapterApiMock.listInstances.mockResolvedValue({ data: [makeInstance()] })

    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    // German: "Neu verbinden"
    const restartBtn = wrapper.findAll('button').find(b => b.text() === 'Neu verbinden')
    await restartBtn.trigger('click')
    await flushPromises()

    expect(adapterApiMock.restartInstance).toHaveBeenCalledWith(1)
    expect(wrapper.html()).toContain('bg-green-500/10')
  })
})

// ─── Binding migration ────────────────────────────────────────────────────────

describe('AdaptersView — binding migration', () => {
  it('migration button is disabled when no same-type targets exist', async () => {
    const { wrapper } = await mountAdapters({
      instances: [makeInstance({ id: 1, adapter_type: 'KNX' })],
    })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    const migrateBtn = wrapper.find('[data-testid="btn-open-migrate-bindings-1"]')
    expect(migrateBtn.attributes('disabled')).toBeDefined()
  })

  it('migration button is enabled when a same-type target exists', async () => {
    const { wrapper } = await mountAdapters({
      instances: [
        makeInstance({ id: 1, adapter_type: 'KNX', name: 'KNX A' }),
        makeInstance({ id: 2, adapter_type: 'KNX', name: 'KNX B' }),
      ],
    })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    const migrateBtn = wrapper.find('[data-testid="btn-open-migrate-bindings-1"]')
    expect(migrateBtn.attributes('disabled')).toBeUndefined()
  })

  it('opens migration modal when clicking the button', async () => {
    const instances = [
      makeInstance({ id: 1, adapter_type: 'KNX', name: 'KNX A' }),
      makeInstance({ id: 2, adapter_type: 'KNX', name: 'KNX B' }),
    ]
    adapterApiMock.listInstances.mockResolvedValue({ data: instances })

    const { wrapper } = await mountAdapters({ instances })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="btn-open-migrate-bindings-1"]').trigger('click')
    expect(wrapper.find('.modal').exists()).toBe(true)
    expect(wrapper.find('[data-testid="select-migration-target"]').exists()).toBe(true)
  })

  it('executes binding migration and shows result', async () => {
    const instances = [
      makeInstance({ id: 1, adapter_type: 'KNX', name: 'KNX A' }),
      makeInstance({ id: 2, adapter_type: 'KNX', name: 'KNX B' }),
    ]
    adapterApiMock.listInstances.mockResolvedValue({ data: instances })

    const { wrapper } = await mountAdapters({ instances })
    await wrapper.find('[data-testid="btn-expand-1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="btn-open-migrate-bindings-1"]').trigger('click')

    // Select the target (id=2)
    const select = wrapper.find('[data-testid="select-migration-target"]')
    await select.setValue('2')
    await select.trigger('change')

    await wrapper.find('[data-testid="btn-migrate-bindings-confirm"]').trigger('click')
    await flushPromises()

    expect(adapterApiMock.migrateBindings).toHaveBeenCalled()
    expect(wrapper.find('[data-testid="migration-result"]').exists()).toBe(true)
  })
})

// ─── Polling timer ────────────────────────────────────────────────────────────

describe('AdaptersView — polling', () => {
  it('polls fetchAdapters every 10 s', async () => {
    adapterApiMock.listInstances.mockResolvedValue({ data: [makeInstance()] })

    await mountAdapters({ instances: [makeInstance()] })
    expect(adapterApiMock.listInstances).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(10001)
    await flushPromises()

    expect(adapterApiMock.listInstances).toHaveBeenCalledTimes(2)
  })

  it('clears polling timer on unmount', async () => {
    adapterApiMock.listInstances.mockResolvedValue({ data: [] })
    const { wrapper } = await mountAdapters()

    wrapper.unmount()
    vi.advanceTimersByTime(15000)
    await flushPromises()

    expect(adapterApiMock.listInstances).toHaveBeenCalledTimes(1) // only on mount
  })
})
