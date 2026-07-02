import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const STUBS = {
  SchemaForm:                        { template: '<div class="schema-form" />' },
  KnxConfigForm:                     { template: '<div class="knx-form" />' },
  AnwesenheitConfigForm:             { template: '<div class="anwesenheit-form" />' },
  ZeitschaltuhrCustomHolidaysEditor: { template: '<div />' },
  AnwesenheitDatapointSelector:      { template: '<div class="anwesenheit-selector" />' },
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

function makeInstance(overrides = {}) {
  return {
    id: 1,
    adapter_type: 'IOBROKER',
    name: 'ioBroker Main',
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

// ─── ioBroker import button ───────────────────────────────────────────────────

describe('AdaptersView — ioBroker import button', () => {
  it('shows import button for IOBROKER adapter type', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    const importBtn = wrapper.findAll('button').find(b => b.text() === 'Importieren')
    expect(importBtn).toBeTruthy()
  })

  it('import button is disabled when adapter is not connected', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance({ connected: false })] })
    await expandInstance(wrapper)
    const importBtn = wrapper.findAll('button').find(b => b.text() === 'Importieren')
    expect(importBtn?.attributes('disabled')).toBeDefined()
  })

  it('import button is enabled when adapter is connected', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance({ connected: true })] })
    await expandInstance(wrapper)
    const importBtn = wrapper.findAll('button').find(b => b.text() === 'Importieren')
    expect(importBtn?.attributes('disabled')).toBeUndefined()
  })

  it('does not show import button for non-IOBROKER adapter type', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance({ adapter_type: 'KNX' })] })
    await expandInstance(wrapper)
    const importBtn = wrapper.findAll('button').find(b => b.text() === 'Importieren')
    // KNX instance should not have the ioBroker import button
    // (there may be a modal with Importieren but not in the action row while modal is closed)
    expect(wrapper.find('.modal').exists()).toBe(false)
  })
})

// ─── ioBroker import modal ────────────────────────────────────────────────────

describe('AdaptersView — ioBroker import modal', () => {
  it('opens import modal when import button is clicked', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    const importBtn = wrapper.findAll('button').find(b => b.text() === 'Importieren')
    await importBtn.trigger('click')
    expect(wrapper.find('.modal').exists()).toBe(true)
  })

  it('shows prefix input and limit input in modal', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    const modal = wrapper.find('.modal')
    expect(modal.find('input[type="number"]').exists()).toBe(true)
  })

  it('shows direction select with 4 options in modal', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    const modal = wrapper.find('.modal')
    const select = modal.find('select')
    expect(select.findAll('option').length).toBe(4)
  })
})

// ─── loadImportPreview ────────────────────────────────────────────────────────

const PREVIEW_ITEMS = [
  { state_id: 'hm-rpc.0.DEV1.0.VALUE', name: 'Temperatur', data_type: 'FLOAT', direction: 'SOURCE', tags: ['sensor'], exists: false, reason: null },
  { state_id: 'hm-rpc.0.DEV2.0.STATE', name: 'Schalter',   data_type: 'BOOLEAN', direction: 'BOTH', tags: ['switch'], exists: true, reason: 'already imported' },
]

describe('AdaptersView — loadImportPreview', () => {
  it('calls iobrokerImportPreview when "Vorschau laden" is clicked', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    const previewBtn = wrapper.findAll('button').find(b => b.text().includes('Vorschau laden'))
    await previewBtn.trigger('click')
    await flushPromises()
    expect(adapterApiMock.iobrokerImportPreview).toHaveBeenCalledWith(1, expect.any(Object))
  })

  it('shows preview items after loading', async () => {
    adapterApiMock.iobrokerImportPreview.mockResolvedValue({ data: { preview: PREVIEW_ITEMS } })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    const previewBtn = wrapper.findAll('button').find(b => b.text().includes('Vorschau laden'))
    await previewBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('hm-rpc.0.DEV1.0.VALUE')
    expect(wrapper.text()).toContain('Temperatur')
  })

  it('pre-selects non-existing items after preview load', async () => {
    adapterApiMock.iobrokerImportPreview.mockResolvedValue({ data: { preview: PREVIEW_ITEMS } })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    await wrapper.findAll('button').find(b => b.text().includes('Vorschau laden')).trigger('click')
    await flushPromises()
    // Only the non-existing item (DEV1) should be checked; DEV2 exists → disabled
    const checkboxes = wrapper.find('.modal').findAll('input[type="checkbox"]')
    const itemCheckboxes = checkboxes.filter(cb => cb.attributes('value') !== undefined)
    const dev1 = itemCheckboxes.find(cb => cb.attributes('value') === 'hm-rpc.0.DEV1.0.VALUE')
    const dev2 = itemCheckboxes.find(cb => cb.attributes('value') === 'hm-rpc.0.DEV2.0.STATE')
    expect(dev1?.element.checked).toBe(true)
    expect(dev2?.attributes('disabled')).toBeDefined()
  })

  it('shows noStates message when preview returns empty list', async () => {
    adapterApiMock.iobrokerImportPreview.mockResolvedValue({ data: { preview: [] } })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    await wrapper.findAll('button').find(b => b.text().includes('Vorschau laden')).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Keine ioBroker-States gefunden')
  })

  it('shows error when iobrokerImportPreview fails', async () => {
    adapterApiMock.iobrokerImportPreview.mockRejectedValue({
      response: { data: { detail: 'Verbindung zum ioBroker unterbrochen' } },
    })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    await wrapper.findAll('button').find(b => b.text().includes('Vorschau laden')).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Verbindung zum ioBroker unterbrochen')
  })
})

// ─── toggleAllImport ──────────────────────────────────────────────────────────

describe('AdaptersView — toggleAllImport', () => {
  it('deselects all when select-all is unchecked', async () => {
    adapterApiMock.iobrokerImportPreview.mockResolvedValue({ data: { preview: PREVIEW_ITEMS } })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    await wrapper.findAll('button').find(b => b.text().includes('Vorschau laden')).trigger('click')
    await flushPromises()

    // select-all is inside the preview header label ("Alle auswählbaren States")
    // The modal also has persist_value/record_history checkboxes before it, so find by label text
    const selectAllLabel = wrapper.find('.modal').findAll('label').find(l => l.text().includes('Alle auswählbaren States'))
    const selectAll = selectAllLabel.find('input[type="checkbox"]')
    await selectAll.setChecked(false)
    await flushPromises()

    // After deselect-all, import button shows "0 importieren" and is disabled
    const importBtn = wrapper.find('.modal').findAll('button').find(b => b.text().includes('importieren'))
    expect(importBtn?.attributes('disabled')).toBeDefined()
  })

  it('selects all non-existing items when select-all is checked', async () => {
    adapterApiMock.iobrokerImportPreview.mockResolvedValue({ data: { preview: PREVIEW_ITEMS } })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    await wrapper.findAll('button').find(b => b.text().includes('Vorschau laden')).trigger('click')
    await flushPromises()

    const selectAllLabel = wrapper.find('.modal').findAll('label').find(l => l.text().includes('Alle auswählbaren States'))
    const selectAll = selectAllLabel.find('input[type="checkbox"]')

    // First uncheck all, then re-check all
    await selectAll.setChecked(false)
    await flushPromises()
    await selectAll.setChecked(true)
    await flushPromises()

    // After re-selecting all, the non-existing item should be selected again
    const dev1 = wrapper.find('.modal').findAll('input[type="checkbox"]')
      .find(cb => cb.attributes('value') === 'hm-rpc.0.DEV1.0.VALUE')
    expect(dev1?.element.checked).toBe(true)
  })
})

// ─── executeIoBrokerImport ────────────────────────────────────────────────────

describe('AdaptersView — executeIoBrokerImport', () => {
  async function openModalWithPreview(wrapper) {
    adapterApiMock.iobrokerImportPreview.mockResolvedValue({ data: { preview: PREVIEW_ITEMS } })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    await wrapper.findAll('button').find(b => b.text().includes('Vorschau laden')).trigger('click')
    await flushPromises()
  }

  it('calls iobrokerImport with selected states', async () => {
    adapterApiMock.iobrokerImport.mockResolvedValue({ data: { created_datapoints: 1, created_bindings: 1, skipped_existing: 1 } })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await openModalWithPreview(wrapper)

    const confirmBtn = wrapper.find('.modal').findAll('button').find(b => b.classes('btn-primary'))
    await confirmBtn.trigger('click')
    await flushPromises()

    expect(adapterApiMock.iobrokerImport).toHaveBeenCalledWith(1, expect.objectContaining({
      states: expect.arrayContaining(['hm-rpc.0.DEV1.0.VALUE']),
    }))
  })

  it('reloads preview after successful import', async () => {
    adapterApiMock.iobrokerImport.mockResolvedValue({ data: { created_datapoints: 1, created_bindings: 1, skipped_existing: 1 } })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await openModalWithPreview(wrapper)

    const confirmBtn = wrapper.find('.modal').findAll('button').find(b => b.classes('btn-primary'))
    await confirmBtn.trigger('click')
    await flushPromises()

    // iobrokerImport is called, then loadImportPreview re-runs automatically
    expect(adapterApiMock.iobrokerImport).toHaveBeenCalledWith(1, expect.any(Object))
    // loadImportPreview is called twice: once for preview load, once for post-import reload
    expect(adapterApiMock.iobrokerImportPreview).toHaveBeenCalledTimes(2)
  })

  it('shows error when iobrokerImport fails', async () => {
    adapterApiMock.iobrokerImport.mockRejectedValue({
      response: { data: { detail: 'Import fehlgeschlagen' } },
    })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await openModalWithPreview(wrapper)

    const confirmBtn = wrapper.find('.modal').findAll('button').find(b => b.classes('btn-primary'))
    await confirmBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Import fehlgeschlagen')
  })

  it('import confirm button is disabled when no states are selected', async () => {
    // Empty preview → no items to select
    adapterApiMock.iobrokerImportPreview.mockResolvedValue({ data: { preview: [] } })
    const { wrapper } = await mountAdapters({ instances: [makeInstance()] })
    await expandInstance(wrapper)
    await wrapper.findAll('button').find(b => b.text() === 'Importieren').trigger('click')
    await wrapper.findAll('button').find(b => b.text().includes('Vorschau laden')).trigger('click')
    await flushPromises()

    const confirmBtn = wrapper.find('.modal').findAll('button').find(b => b.classes('btn-primary'))
    expect(confirmBtn?.attributes('disabled')).toBeDefined()
  })
})

// ─── Anwesenheit selector ─────────────────────────────────────────────────────

describe('AdaptersView — Anwesenheit selector modal', () => {
  function makeAnwInstance(overrides = {}) {
    return makeInstance({
      id: 5,
      adapter_type: 'ANWESENHEITSSIMULATION',
      name: 'Anwesenheit 1',
      ...overrides,
    })
  }

  it('shows "Objekte verwalten" button for ANWESENHEITSSIMULATION adapter', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeAnwInstance()] })
    await expandInstance(wrapper, 5)
    const btn = wrapper.findAll('button').find(b => b.text() === 'Objekte verwalten')
    expect(btn).toBeTruthy()
  })

  it('does not show "Objekte verwalten" for non-Anwesenheit adapter', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeInstance({ adapter_type: 'KNX' })] })
    await expandInstance(wrapper)
    const btn = wrapper.findAll('button').find(b => b.text() === 'Objekte verwalten')
    expect(btn).toBeFalsy()
  })

  it('opens Anwesenheit modal on button click', async () => {
    const { wrapper } = await mountAdapters({ instances: [makeAnwInstance()] })
    await expandInstance(wrapper, 5)
    const btn = wrapper.findAll('button').find(b => b.text() === 'Objekte verwalten')
    await btn.trigger('click')
    expect(wrapper.find('.modal').exists()).toBe(true)
    expect(wrapper.find('.anwesenheit-selector').exists()).toBe(true)
  })
})

// ─── Migration error path ─────────────────────────────────────────────────────

describe('AdaptersView — migration error path', () => {
  it('shows migrationError when executeBindingMigration fails', async () => {
    adapterApiMock.migrateBindings.mockRejectedValue({
      response: { data: { detail: 'Migration nicht möglich' } },
    })
    const instances = [
      makeInstance({ id: 1, adapter_type: 'KNX', name: 'KNX A' }),
      makeInstance({ id: 2, adapter_type: 'KNX', name: 'KNX B' }),
    ]
    adapterApiMock.listInstances.mockResolvedValue({ data: instances })

    const { wrapper } = await mountAdapters({ instances })
    await expandInstance(wrapper)
    await wrapper.find('[data-testid="btn-open-migrate-bindings-1"]').trigger('click')

    const select = wrapper.find('[data-testid="select-migration-target"]')
    await select.setValue('2')
    await select.trigger('change')

    await wrapper.find('[data-testid="btn-migrate-bindings-confirm"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Migration nicht möglich')
  })
})

// ─── Delete error path ────────────────────────────────────────────────────────

describe('AdaptersView — delete error path', () => {
  it('shows error feedback when deleteInstance fails', async () => {
    adapterApiMock.deleteInstance.mockRejectedValue({
      response: { data: { detail: 'Löschen nicht erlaubt' } },
    })

    const { wrapper } = await mountAdapters({ instances: [makeInstance({ adapter_type: 'KNX', name: 'KNX Test' })] })
    await expandInstance(wrapper)
    await wrapper.find('[data-testid="btn-delete-instance"]').trigger('click')
    await wrapper.find('[data-testid="confirm-btn"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Löschen nicht erlaubt')
  })
})
