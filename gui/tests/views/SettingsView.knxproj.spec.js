import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let knxprojApi

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

  knxprojApi = {
    listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }),
    import: vi.fn().mockResolvedValue({
      data: {
        imported: 2,
        created: 0,
        updated: 0,
        locations: 0,
        trades: 0,
        hierarchies: [
          {
            mode: 'groups',
            status: 'created',
            tree_id: 'tree-1',
            tree_name: 'ETS Gruppenadressen',
            nodes_created: 3,
            links_created: 1,
            message: 'created',
          },
        ],
      },
    }),
  }

  vi.doMock('@/api/client', () => ({
    settingsApi: {
      get: vi.fn().mockResolvedValue({ data: { timezone: 'Europe/Berlin' } }),
      update: vi.fn().mockResolvedValue({ data: {} }),
    },
    historySettingsApi: {
      get: vi.fn().mockResolvedValue({ data: { plugin: 'sqlite', default_window_hours: 168 } }),
      update: vi.fn().mockResolvedValue({ data: {} }),
      test: vi.fn().mockResolvedValue({ data: { ok: true } }),
    },
    dpApi: {
      listAll: vi.fn().mockResolvedValue({ data: { items: [] } }),
      update: vi.fn().mockResolvedValue({ data: {} }),
    },
    securityApi: {
      listUrlTargets: vi.fn().mockResolvedValue({ data: { path: '/allowlist.yaml', entries: [] } }),
      checkUrlTarget: vi.fn().mockResolvedValue({ data: { allowed: true } }),
      addUrlTarget: vi.fn().mockResolvedValue({ data: {} }),
      deleteUrlTarget: vi.fn().mockResolvedValue({ data: {} }),
    },
    authApi: {
      listUsers: vi.fn().mockResolvedValue({ data: [] }),
      listApiKeys: vi.fn().mockResolvedValue({ data: [] }),
    },
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({
        data: [{ id: 'knx-1', name: 'KNX main', adapter_type: 'KNX', enabled: true }],
      }),
    },
    configApi: {
      export: vi.fn().mockResolvedValue({ data: {} }),
      exportDb: vi.fn().mockResolvedValue({ data: new Blob(['db']) }),
      import: vi.fn().mockResolvedValue({ data: {} }),
      importDb: vi.fn().mockResolvedValue({ data: {} }),
    },
    autobackupApi: {
      getConfig: vi.fn().mockResolvedValue({ data: {} }),
      list: vi.fn().mockResolvedValue({ data: [] }),
    },
    knxprojApi,
    iconsApi: {
      list: vi.fn().mockResolvedValue({ data: { icons: [] } }),
      getSettings: vi.fn().mockResolvedValue({ data: {} }),
    },
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
    supportApi: {
      categories: vi.fn().mockResolvedValue({ data: [] }),
      getDebugStatus: vi.fn().mockResolvedValue({ data: { active: false, level: 'INFO', until: null } }),
    },
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

  const mod = await import('@/views/SettingsView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: {
        HierarchyManager: true,
        Modal: { props: ['modelValue'], template: '<div v-if="modelValue"><slot /><slot name="footer" /></div>' },
        ConfirmDialog: true,
        IconPicker: true,
        VisuIcon: true,
        LocaleSwitcher: true,
        Badge: { template: '<span><slot /></span>' },
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

async function openImportExportTab(wrapper) {
  const tab = wrapper.findAll('button').find(button => button.text() === 'Datenmanagement')
  expect(tab).toBeTruthy()
  await tab.trigger('click')
  await flushPromises()
}

async function selectKnxProjectFile(wrapper) {
  const file = new File(['knxproj'], 'project.knxproj', { type: 'application/octet-stream' })
  const input = wrapper.find('input[accept=".knxproj"]')
  Object.defineProperty(input.element, 'files', {
    value: [file],
    configurable: true,
  })
  await input.trigger('change')
  await flushPromises()
}

function findKnxImportButton(wrapper) {
  return wrapper
    .findAll('button')
    .find(button => button.text() === 'Importieren' && button.element.closest('.card')?.textContent?.includes('.knxproj'))
}

describe('SettingsView KNX project hierarchy options', () => {
  it('sends selected hierarchy modes and auto-link settings with the import', async () => {
    const wrapper = await mountSettingsView()
    await openImportExportTab(wrapper)
    await selectKnxProjectFile(wrapper)

    const labels = wrapper.findAll('label')
    const createDatapoints = labels.find(label => label.text().includes('Objekte anlegen'))
    expect(createDatapoints).toBeTruthy()
    await createDatapoints.find('input[type="checkbox"]').setValue(true)

    const trades = labels.find(label => label.text().includes('Gewerke'))
    expect(trades).toBeTruthy()
    await trades.find('input[type="checkbox"]').setValue(false)

    const importButton = findKnxImportButton(wrapper)
    expect(importButton).toBeTruthy()
    await importButton.trigger('click')
    await flushPromises()

    expect(knxprojApi.import).toHaveBeenCalledTimes(1)
    expect(knxprojApi.import.mock.calls[0][1]).toMatchObject({
      adapter_name: 'KNX main',
      hierarchy_modes: 'groups,buildings',
      hierarchy_auto_link: true,
    })
    expect(wrapper.text()).toContain('Topologie: angelegt')
    expect(wrapper.text()).toContain('3 Knoten')
    expect(wrapper.text()).toContain('1 Verknüpfung')

    wrapper.unmount()
  })
})
