import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import de from '@/locales/de.json'
import en from '@/locales/en.json'

// Danger zone: every target button must open a dialog with real text, not raw i18n keys (#1315).

let configApi
let knxprojApi
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

  configApi = {
    export:          vi.fn().mockResolvedValue({ data: {} }),
    exportDb:        vi.fn().mockResolvedValue({ data: new Blob() }),
    import:          vi.fn().mockResolvedValue({ data: {} }),
    importDb:        vi.fn().mockResolvedValue({ data: {} }),
    reset:           vi.fn().mockResolvedValue({ data: { datapoints_deleted: 3, bindings_deleted: 4, adapter_instances_deleted: 1, knx_group_addresses_deleted: 2, logic_graphs_deleted: 5, icons_deleted: 0 } }),
    resetBindings:   vi.fn().mockResolvedValue({ data: { deleted: 4 } }),
    resetDatapoints: vi.fn().mockResolvedValue({ data: { deleted: 3, bindings_deleted: 4 } }),
    resetLogic:      vi.fn().mockResolvedValue({ data: { deleted: 5 } }),
    resetAdapters:   vi.fn().mockResolvedValue({ data: { deleted: 1, bindings_deleted: 4 } }),
  }
  knxprojApi = {
    listGA:  vi.fn().mockResolvedValue({ data: { total: 2, items: [] } }),
    import:  vi.fn().mockResolvedValue({ data: {} }),
    clearGA: vi.fn().mockResolvedValue({ data: {} }),
  }
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
    configApi,
    autobackupApi: { getConfig: vi.fn().mockResolvedValue({ data: { enabled: false, hour: 3, retention_days: 7 } }), list: vi.fn().mockResolvedValue({ data: [] }), setConfig: vi.fn().mockResolvedValue({ data: {} }), runNow: vi.fn().mockResolvedValue({ data: {} }), restore: vi.fn().mockResolvedValue({ data: {} }) },
    knxprojApi,
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
        ConfirmDialog: {
          name: 'ConfirmDialog',
          props: ['modelValue', 'title', 'message', 'confirmLabel'],
          emits: ['confirm', 'update:modelValue'],
          template: '<div v-if="modelValue" data-testid="dz-dialog" />',
        },
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

async function openDangerZone(wrapper) {
  const tab = wrapper.findAll('button').find(b => b.text() === 'Gefahrenzone')
  expect(tab).toBeTruthy()
  await tab.trigger('click')
  await flushPromises()
}

const TARGETS = [
  { target: 'bindings',   api: () => configApi.resetBindings },
  { target: 'datapoints', api: () => configApi.resetDatapoints },
  { target: 'logic',      api: () => configApi.resetLogic },
  { target: 'adapters',   api: () => configApi.resetAdapters },
  { target: 'knxga',      api: () => knxprojApi.clearGA },
  { target: 'factory',    api: () => configApi.reset },
]

describe('SettingsView danger zone (#1315)', () => {
  it.each(TARGETS)('$target: dialog shows translated texts and confirm runs its reset', async ({ target, api }) => {
    const wrapper = await mountSettingsView()
    await openDangerZone(wrapper)

    await wrapper.find(`[data-testid="dz-${target}"]`).trigger('click')
    const dialog = wrapper.findComponent({ name: 'ConfirmDialog' })
    expect(dialog.props('modelValue')).toBe(true)

    const texts = de.settings.dangerzone[target]
    expect(dialog.props('title')).toBe(texts.confirmTitle)
    expect(dialog.props('message')).toBe(texts.confirmMessage)
    expect(dialog.props('confirmLabel')).toBe(texts.confirmLabel)

    dialog.vm.$emit('confirm')
    await flushPromises()
    expect(api()).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('factory reset reports the deleted counts', async () => {
    const wrapper = await mountSettingsView()
    await openDangerZone(wrapper)

    await wrapper.find('[data-testid="dz-factory"]').trigger('click')
    wrapper.findComponent({ name: 'ConfirmDialog' }).vm.$emit('confirm')
    await flushPromises()

    expect(wrapper.text()).toContain('Zurückgesetzt: 3 Objekte, 4 Verknüpfungen, 1 Adapter, 2 KNX-GAs, 5 Logikblätter gelöscht.')
    wrapper.unmount()
  })

  it.each([['de', de], ['en', en]])('%s locale has title, message and confirm label for every target', (_locale, messages) => {
    for (const { target } of TARGETS) {
      const texts = messages.settings.dangerzone[target]
      expect(texts, target).toBeTruthy()
      for (const key of ['confirmTitle', 'confirmMessage', 'confirmLabel']) {
        expect(typeof texts[key], `${target}.${key}`).toBe('string')
        expect(texts[key].length, `${target}.${key}`).toBeGreaterThan(0)
      }
    }
  })
})
