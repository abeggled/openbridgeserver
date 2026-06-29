import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let iconsApi

beforeEach(() => {
  vi.resetModules()

  const storage = {
    getItem: vi.fn().mockImplementation(key => (key === 'access_token' ? 'token' : 'de')),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  }
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })

  iconsApi = {
    list:         vi.fn().mockResolvedValue({ data: { icons: [{ name: 'home', content: '<svg/>' }] } }),
    getSettings:  vi.fn().mockResolvedValue({ data: { fa_api_key: 'existing-key' } }),
    saveSettings: vi.fn().mockResolvedValue({ data: {} }),
    import:       vi.fn().mockResolvedValue({ data: { message: 'ok' } }),
    delete:       vi.fn().mockResolvedValue({ data: {} }),
    export:       vi.fn().mockResolvedValue({ data: new Blob(['icons']) }),
    importKnxuf:  vi.fn().mockResolvedValue({ data: { imported: 0, message: 'ok' } }),
    importFa:     vi.fn().mockResolvedValue({ data: { imported: 0, message: 'ok', debug: [] } }),
  }

  vi.doMock('@/api/client', () => ({
    settingsApi:   { get: vi.fn().mockResolvedValue({ data: { timezone: 'Europe/Berlin' } }), update: vi.fn().mockResolvedValue({ data: {} }) },
    historySettingsApi: { get: vi.fn().mockResolvedValue({ data: { plugin: 'sqlite', default_window_hours: 168 } }), update: vi.fn().mockResolvedValue({ data: {} }), test: vi.fn().mockResolvedValue({ data: { ok: true } }) },
    dpApi:         { listAll: vi.fn().mockResolvedValue({ data: { items: [] } }), update: vi.fn().mockResolvedValue({ data: {} }) },
    securityApi:   { listUrlTargets: vi.fn().mockResolvedValue({ data: { path: '/al.yaml', entries: [] } }), checkUrlTarget: vi.fn(), addUrlTarget: vi.fn().mockResolvedValue({ data: {} }), deleteUrlTarget: vi.fn().mockResolvedValue({ data: {} }) },
    authApi:       { listUsers: vi.fn().mockResolvedValue({ data: [] }), listApiKeys: vi.fn().mockResolvedValue({ data: [] }), changePassword: vi.fn(), createUser: vi.fn(), deleteUser: vi.fn(), setMqttPassword: vi.fn(), deleteMqttPassword: vi.fn(), createApiKey: vi.fn(), deleteApiKey: vi.fn() },
    adapterApi:    { listInstances: vi.fn().mockResolvedValue({ data: [] }) },
    configApi:     { export: vi.fn().mockResolvedValue({ data: {} }), exportDb: vi.fn().mockResolvedValue({ data: new Blob() }), import: vi.fn().mockResolvedValue({ data: {} }), importDb: vi.fn().mockResolvedValue({ data: {} }) },
    autobackupApi: { getConfig: vi.fn().mockResolvedValue({ data: { enabled: false, hour: 3, retention_days: 7 } }), list: vi.fn().mockResolvedValue({ data: [] }), setConfig: vi.fn().mockResolvedValue({ data: {} }), runNow: vi.fn().mockResolvedValue({ data: {} }), restore: vi.fn().mockResolvedValue({ data: {} }) },
    knxprojApi:    { listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }), import: vi.fn().mockResolvedValue({ data: {} }) },
    iconsApi,
    navLinksApi:   { list: vi.fn().mockResolvedValue({ data: [] }) },
    supportApi:    { categories: vi.fn().mockResolvedValue({ data: [] }), getDebugStatus: vi.fn().mockResolvedValue({ data: { active: false, level: 'INFO', until: null } }) },
    hierarchyApi:  { listTrees: vi.fn().mockResolvedValue({ data: [] }), getTreeNodes: vi.fn().mockResolvedValue({ data: [] }), createTree: vi.fn(), updateTree: vi.fn(), deleteTree: vi.fn(), createNode: vi.fn(), updateNode: vi.fn(), deleteNode: vi.fn(), importFromEts: vi.fn() },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountSettingsView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const { default: SettingsView } = await import('@/views/SettingsView.vue')
  const wrapper = mount(SettingsView, {
    global: {
      plugins: [pinia],
      stubs: {
        HierarchyManager: true,
        Modal:        { props: ['modelValue'], template: '<div v-if="modelValue"><slot /><slot name="footer" /></div>' },
        ConfirmDialog: true,
        IconPicker:   true,
        VisuIcon:     true,
        LocaleSwitcher: true,
        Badge:        { template: '<span><slot /></span>' },
        Spinner:      { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

async function openIconsTab(wrapper) {
  const tab = wrapper.findAll('button').find(b => b.text() === 'Icons')
  expect(tab).toBeTruthy()
  await tab.trigger('click')
  await flushPromises()
}

// ─── loadIcons error path ─────────────────────────────────────────────────────

describe('SettingsView Icons tab — loadIcons error', () => {
  it('shows error message when iconsApi.list fails', async () => {
    iconsApi.list.mockRejectedValue({
      response: { data: { detail: 'Icons konnten nicht geladen werden' } },
    })
    const wrapper = await mountSettingsView()
    await openIconsTab(wrapper)
    expect(wrapper.text()).toContain('Icons konnten nicht geladen werden')
    wrapper.unmount()
  })

  it('shows fallback loadError message when API fails without detail', async () => {
    iconsApi.list.mockRejectedValue(new Error('network'))
    const wrapper = await mountSettingsView()
    await openIconsTab(wrapper)
    expect(wrapper.text()).toContain('Fehler beim Laden der Icons')
    wrapper.unmount()
  })
})

// ─── doSaveFaKey error path ────────────────────────────────────────────────────

describe('SettingsView Icons tab — doSaveFaKey error', () => {
  it('shows error message when saving FA API key fails', async () => {
    iconsApi.saveSettings.mockRejectedValue({
      response: { data: { detail: 'API-Key ungültig' } },
    })
    const wrapper = await mountSettingsView()
    await openIconsTab(wrapper)

    // Enter key into the password input
    const keyInput = wrapper.find('input[type="password"]')
    await keyInput.setValue('bad-key')

    // Find save button (not delete — the one that is btn-secondary, not btn-danger)
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Speichern') && !b.classes('btn-danger'))
    await saveBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('API-Key ungültig')
    wrapper.unmount()
  })

  it('shows fallback saveError message when API fails without detail', async () => {
    iconsApi.saveSettings.mockRejectedValue(new Error('network'))
    const wrapper = await mountSettingsView()
    await openIconsTab(wrapper)

    const keyInput = wrapper.find('input[type="password"]')
    await keyInput.setValue('some-key')
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Speichern') && !b.classes('btn-danger'))
    await saveBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Fehler beim Speichern')
    wrapper.unmount()
  })
})

// ─── doDeleteFaKey error path ──────────────────────────────────────────────────

describe('SettingsView Icons tab — doDeleteFaKey error', () => {
  it('shows error message when deleting FA API key fails', async () => {
    // getSettings returns existing key, so delete button is visible
    iconsApi.saveSettings.mockRejectedValue({
      response: { data: { detail: 'Key nicht gefunden' } },
    })
    const wrapper = await mountSettingsView()
    await openIconsTab(wrapper)

    // Delete button is btn-danger and appears only when faSavedKey is set
    const deleteBtn = wrapper.findAll('button').find(b => b.classes('btn-danger') && b.text().toLowerCase().includes('löschen'))
    expect(deleteBtn).toBeTruthy()
    await deleteBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Key nicht gefunden')
    wrapper.unmount()
  })

  it('shows fallback deleteError message when delete API fails without detail', async () => {
    iconsApi.saveSettings.mockRejectedValue(new Error('network'))
    const wrapper = await mountSettingsView()
    await openIconsTab(wrapper)

    const deleteBtn = wrapper.findAll('button').find(b => b.classes('btn-danger') && b.text().toLowerCase().includes('löschen'))
    expect(deleteBtn).toBeTruthy()
    await deleteBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Fehler beim Löschen')
    wrapper.unmount()
  })
})
