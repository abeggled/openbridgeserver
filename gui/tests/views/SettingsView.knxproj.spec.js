import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let knxprojApi
let configApi
let autobackupApi
let iconsApi
let authApi
let dpApi
let historySettingsApi
let adapterApi

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
            links_created: 0,
            trees_replaced: 1,
            message: 'created',
          },
        ],
      },
    }),
  }
  configApi = {
    export: vi.fn().mockResolvedValue({ data: { obs_export: 'config', version: 1 } }),
    exportDb: vi.fn().mockResolvedValue({ data: new Blob(['db']) }),
    import: vi.fn().mockResolvedValue({
      data: {
        datapoints_created: 1,
        datapoints_updated: 2,
        bindings_created: 3,
        bindings_updated: 4,
        knx_group_addresses_upserted: 5,
        logic_graphs_created: 1,
        logic_graphs_updated: 1,
        icons_imported: 2,
        visu_nodes_upserted: 6,
      },
    }),
    importDb: vi.fn().mockResolvedValue({
      data: { message: 'DB restored', adapters_restarted: 2 },
    }),
  }
  autobackupApi = {
    getConfig: vi.fn().mockResolvedValue({ data: { enabled: true, hour: 3, retention_days: 7 } }),
    list: vi.fn().mockResolvedValue({ data: [{ name: '20240506-0300', size_bytes: 2048 }] }),
    setConfig: vi.fn().mockResolvedValue({ data: {} }),
    runNow: vi.fn().mockResolvedValue({ data: { name: '20240507-0300' } }),
    restore: vi.fn().mockResolvedValue({
      data: { datapoints: 2, bindings: 3, visu_nodes: 4, errors: ['warning'] },
    }),
  }
  iconsApi = {
    list: vi.fn().mockResolvedValue({
      data: {
        icons: [
          { name: 'custom_light', content: '<svg viewBox="0 0 16 16"><path d="M1 1h14v14H1z"/></svg>' },
          { name: 'kuf_switch', content: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/></svg>' },
        ],
      },
    }),
    getSettings: vi.fn().mockResolvedValue({ data: { fa_api_key: 'saved-key' } }),
    saveSettings: vi.fn().mockResolvedValue({ data: {} }),
    import: vi.fn().mockResolvedValue({ data: { message: '2 Icons importiert' } }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
    export: vi.fn().mockResolvedValue({ data: new Blob(['icons']) }),
    importKnxuf: vi.fn().mockResolvedValue({ data: { imported: 1, message: 'KNX UF importiert' } }),
    importFa: vi.fn().mockResolvedValue({ data: { imported: 1, message: 'FontAwesome importiert', debug: ['ok'] } }),
  }
  authApi = {
    listUsers: vi.fn().mockResolvedValue({
      data: [
        { id: 'u1', username: 'admin', is_admin: true, mqtt_enabled: false, created_at: '2026-06-08T00:00:00Z' },
        { id: 'u2', username: 'bob', is_admin: false, mqtt_enabled: true, created_at: '2026-06-08T00:00:00Z' },
      ],
    }),
    listApiKeys: vi.fn().mockResolvedValue({
      data: [{ id: 'key-1', name: 'Main key', created_at: '2026-06-08T00:00:00Z' }],
    }),
    changePassword: vi.fn().mockResolvedValue({ data: {} }),
    createUser: vi.fn().mockResolvedValue({ data: {} }),
    deleteUser: vi.fn().mockResolvedValue({ data: {} }),
    setMqttPassword: vi.fn().mockResolvedValue({ data: {} }),
    deleteMqttPassword: vi.fn().mockResolvedValue({ data: {} }),
    createApiKey: vi.fn().mockResolvedValue({ data: { key: 'obs_secret_key' } }),
    deleteApiKey: vi.fn().mockResolvedValue({ data: {} }),
  }
  dpApi = {
    listAll: vi.fn().mockResolvedValue({
      data: {
        items: [
          { id: 'dp-temp', name: 'Temperature', data_type: 'FLOAT', unit: '°C', record_history: false },
          { id: 'dp-switch', name: 'Switch', data_type: 'BOOLEAN', unit: null, record_history: true },
        ],
      },
    }),
    update: vi.fn().mockResolvedValue({ data: {} }),
  }
  historySettingsApi = {
    get: vi.fn().mockResolvedValue({
      data: {
        plugin: 'sqlite',
        default_window_hours: 168,
        influx_version: 2,
        influx_url: 'http://localhost:8086',
        influx_token: 'token',
        influx_org: 'obs-org',
        influx_bucket: 'obs-bucket',
        influx_database: 'obs',
        influx_username: 'obs',
        influx_password: 'secret',
        timescale_dsn: 'postgres://obs:secret@localhost/obs',
      },
    }),
    update: vi.fn().mockResolvedValue({ data: {} }),
    test: vi.fn().mockResolvedValue({ data: { ok: true, message: 'History OK' } }),
  }
  adapterApi = {
    listInstances: vi.fn().mockResolvedValue({ data: [] }),
  }

  vi.doMock('@/api/client', () => ({
    settingsApi: {
      get: vi.fn().mockResolvedValue({ data: { timezone: 'Europe/Berlin' } }),
      update: vi.fn().mockResolvedValue({ data: {} }),
    },
    historySettingsApi,
    dpApi,
    securityApi: {
      listUrlTargets: vi.fn().mockResolvedValue({ data: { path: '/allowlist.yaml', entries: [] } }),
      checkUrlTarget: vi.fn().mockResolvedValue({ data: { allowed: true } }),
      addUrlTarget: vi.fn().mockResolvedValue({ data: {} }),
      deleteUrlTarget: vi.fn().mockResolvedValue({ data: {} }),
    },
    authApi,
    adapterApi,
    configApi,
    autobackupApi,
    knxprojApi,
    iconsApi,
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
  await openTab(wrapper, 'Datenmanagement')
}

async function openTab(wrapper, text) {
  const tab = wrapper.findAll('button').find(button => button.text() === text)
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

async function selectFile(input, file) {
  Object.defineProperty(input.element, 'files', {
    value: [file],
    configurable: true,
  })
  await input.trigger('change')
  await flushPromises()
}

function findButton(wrapper, text) {
  return wrapper.findAll('button').find(button => button.text() === text)
}

function findButtonContaining(wrapper, text) {
  return wrapper.findAll('button').find(button => button.text().includes(text))
}

function findKnxImportButton(wrapper) {
  return wrapper
    .findAll('button')
    .find(button => button.text() === 'Importieren' && button.element.closest('.card')?.textContent?.includes('.knxproj'))
}

function findReplaceExistingCheckbox(wrapper) {
  const label = wrapper
    .findAll('label')
    .find(node => node.text().includes('Bestehende ETS-Hierarchien dieses Imports ersetzen'))
  expect(label).toBeTruthy()
  return label.find('input[type="checkbox"]')
}

describe('SettingsView KNX project import', () => {
  it('sends hierarchy_replace_existing by default and shows replaced tree counts', async () => {
    const wrapper = await mountSettingsView()
    await openImportExportTab(wrapper)
    await selectKnxProjectFile(wrapper)

    const replaceExisting = findReplaceExistingCheckbox(wrapper)
    expect(replaceExisting.element.checked).toBe(true)

    const importButton = findKnxImportButton(wrapper)
    expect(importButton).toBeTruthy()
    await importButton.trigger('click')
    await flushPromises()

    expect(knxprojApi.import).toHaveBeenCalledTimes(1)
    expect(knxprojApi.import.mock.calls[0][1]).toMatchObject({
      hierarchy_modes: 'groups,buildings,trades',
      hierarchy_auto_link: false,
      hierarchy_replace_existing: true,
    })
    expect(wrapper.text()).toContain('1 ersetzt')

    wrapper.unmount()
  })

  it('can keep existing ETS hierarchy trees for separate import runs', async () => {
    knxprojApi.import.mockResolvedValueOnce({
      data: {
        imported: 2,
        created: 0,
        updated: 0,
        locations: 0,
        trades: 0,
        hierarchies: [
          {
            mode: 'groups',
            status: 'skipped',
            message: 'Keine Gruppenadressen gefunden',
          },
          {
            mode: 'buildings',
            status: 'created',
            tree_id: 'tree-2',
            tree_name: 'ETS Gebäude/Räume',
            nodes_created: 2,
            links_created: 0,
            trees_replaced: 0,
            message: 'created',
          },
        ],
      },
    })
    const wrapper = await mountSettingsView()
    await openImportExportTab(wrapper)
    await selectKnxProjectFile(wrapper)

    await findReplaceExistingCheckbox(wrapper).setValue(false)
    const importButton = findKnxImportButton(wrapper)
    expect(importButton).toBeTruthy()
    await importButton.trigger('click')
    await flushPromises()

    expect(knxprojApi.import).toHaveBeenCalledTimes(1)
    expect(knxprojApi.import.mock.calls[0][1]).toMatchObject({
      hierarchy_modes: 'groups,buildings,trades',
      hierarchy_auto_link: false,
      hierarchy_replace_existing: false,
    })
    expect(wrapper.text()).toContain('Topologie: nicht angelegt')
    expect(wrapper.text()).toContain('Keine Gruppenadressen gefunden')
    expect(wrapper.text()).toContain('Gebäude / Räume: angelegt')
    expect(wrapper.text()).toContain('2 Knoten, 0 Verknüpfungen')

    wrapper.unmount()
  })

  it('renders hierarchy import results with missing optional counts', async () => {
    knxprojApi.import.mockResolvedValueOnce({
      data: {
        imported: 1,
        hierarchies: [
          {
            mode: 'groups',
            status: 'created',
            tree_id: 'tree-1',
            tree_name: 'ETS Gruppenadressen',
            message: 'created',
          },
        ],
      },
    })

    const wrapper = await mountSettingsView()
    await openImportExportTab(wrapper)
    await selectKnxProjectFile(wrapper)

    const importButton = findKnxImportButton(wrapper)
    expect(importButton).toBeTruthy()
    await importButton.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Topologie: angelegt')
    expect(wrapper.text()).toContain('0 Knoten, 0 Verknüpfungen')

    wrapper.unmount()
  })

  it('enables hierarchy auto-link when DataPoint import uses a KNX adapter', async () => {
    adapterApi.listInstances.mockResolvedValueOnce({
      data: [{ name: 'knx-main', adapter_type: 'KNX' }],
    })

    const wrapper = await mountSettingsView()
    await openImportExportTab(wrapper)
    await selectKnxProjectFile(wrapper)

    const createDps = wrapper
      .findAll('label')
      .find(node => node.text().includes('Objekte anlegen / aktualisieren'))
      .find('input[type="checkbox"]')
    await createDps.setValue(true)
    await flushPromises()

    const importButton = findKnxImportButton(wrapper)
    expect(importButton).toBeTruthy()
    await importButton.trigger('click')
    await flushPromises()

    expect(knxprojApi.import).toHaveBeenCalledTimes(1)
    expect(knxprojApi.import.mock.calls[0][1]).toMatchObject({
      adapter_name: 'knx-main',
      direction: 'SOURCE',
      hierarchy_auto_link: true,
      hierarchy_replace_existing: true,
    })

    wrapper.unmount()
  })
})

describe('SettingsView import/export coverage', () => {
  it('covers JSON/DB backup, restore, and autobackup workflows', async () => {
    const createObjectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:settings-backup')
    const revokeObjectUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const wrapper = await mountSettingsView()
    await openImportExportTab(wrapper)

    await findButton(wrapper, 'JSON herunterladen').trigger('click')
    await flushPromises()
    expect(configApi.export).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:settings-backup')

    await findButton(wrapper, 'SQLite herunterladen').trigger('click')
    await flushPromises()
    expect(configApi.exportDb).toHaveBeenCalled()

    const jsonInput = wrapper.find('input[accept=".json"]')
    await selectFile(
      jsonInput,
      new File([JSON.stringify({ obs_export: 'config', version: 1 })], 'backup.json', { type: 'application/json' }),
    )
    expect(configApi.import).toHaveBeenCalledWith({ obs_export: 'config', version: 1 })
    expect(iconsApi.getSettings).toHaveBeenCalled()
    expect(iconsApi.list).toHaveBeenCalled()
    expect(wrapper.text()).toContain('Wiederherstellung OK')
    expect(wrapper.text()).toContain('5 KNX-GAs')
    expect(wrapper.text()).toContain('2 Icons')

    const dbInput = wrapper.find('input[accept=".sqlite,.db"]')
    await selectFile(dbInput, new File(['sqlite'], 'backup.sqlite', { type: 'application/octet-stream' }))
    expect(configApi.importDb).toHaveBeenCalled()
    expect(wrapper.text()).toContain('Datenbankwiederherstellung OK')
    expect(wrapper.text()).toContain('2 Adapter neu gestartet')

    const enableAutobackup = wrapper
      .findAll('label')
      .find(label => label.text().includes('Autobackup aktivieren'))
      .find('input[type="checkbox"]')
    await enableAutobackup.setValue(false)
    await flushPromises()
    expect(autobackupApi.setConfig).toHaveBeenCalledWith(expect.objectContaining({ enabled: false }))

    await findButton(wrapper, 'Jetzt sichern').trigger('click')
    await flushPromises()
    expect(autobackupApi.runNow).toHaveBeenCalled()
    expect(wrapper.text()).toContain('Sicherung erstellt: 07.05.2024 03:00 Uhr')

    await wrapper.find('select').setValue('20240506-0300')
    await findButton(wrapper, 'Wiederherstellen').trigger('click')
    await flushPromises()
    expect(autobackupApi.restore).toHaveBeenCalledWith('20240506-0300')
    expect(wrapper.text()).toContain('Wiederherstellung OK: 2 Objekte, 3 Verknüpfungen, 4 Visu-Knoten')
    expect(wrapper.text()).toContain('(1 Warnung(en))')

    wrapper.unmount()
    createObjectUrl.mockRestore()
    revokeObjectUrl.mockRestore()
    clickSpy.mockRestore()
  })
})

describe('SettingsView account and admin coverage', () => {
  it('covers timezone selection, password changes, users, MQTT passwords, and API keys', async () => {
    const wrapper = await mountSettingsView()

    const timezoneButton = wrapper.findAll('button').find(button => button.text().includes('Europe/Berlin'))
    expect(timezoneButton).toBeTruthy()
    await timezoneButton.trigger('click')
    await flushPromises()
    const timezoneSearch = wrapper.find('.relative input[type="text"]')
    await timezoneSearch.setValue('Europe/Berlin')
    await timezoneSearch.trigger('keydown.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('Europe/Berlin')

    const darkTheme = wrapper.findAll('input[type="radio"]').find(input => input.element.value === 'dark')
    await darkTheme.setValue()
    await flushPromises()

    await openTab(wrapper, 'Passwort')
    const passwordInputs = wrapper.findAll('input[type="password"]')
    await passwordInputs[0].setValue('old-password')
    await passwordInputs[1].setValue('new-password')
    await passwordInputs[2].setValue('different-password')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(authApi.changePassword).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Passwörter stimmen nicht überein')

    await passwordInputs[2].setValue('new-password')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(authApi.changePassword).toHaveBeenCalledWith('old-password', 'new-password')
    expect(wrapper.text()).toContain('Passwort erfolgreich geändert')

    await openTab(wrapper, 'Benutzer')
    expect(authApi.listUsers).toHaveBeenCalled()
    expect(wrapper.text()).toContain('bob')
    expect(wrapper.text()).toContain('Aktiv')

    await findButton(wrapper, '+ Benutzer').trigger('click')
    await flushPromises()
    const createUserInputs = wrapper.findAll('form input')
    await createUserInputs[0].setValue('alice')
    await createUserInputs[1].setValue('secret')
    await wrapper.find('#isAdmin').setValue(true)
    await wrapper.find('#mqttEnabled').setValue(true)
    await flushPromises()
    await wrapper.findAll('form input[type="password"]')[1].setValue('mqtt-secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(authApi.createUser).toHaveBeenCalledWith({
      username: 'alice',
      password: 'secret',
      is_admin: true,
      mqtt_enabled: true,
      mqtt_password: 'mqtt-secret',
    })

    const mqttSetButton = wrapper.findAll('button').find(button => button.attributes('title') === 'MQTT-Passwort setzen')
    expect(mqttSetButton).toBeTruthy()
    await mqttSetButton.trigger('click')
    await flushPromises()
    const mqttInput = wrapper.findAll('form input[type="password"]')[0]
    await mqttInput.setValue('new-mqtt-secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(authApi.setMqttPassword).toHaveBeenCalledWith('admin', 'new-mqtt-secret')
    await findButton(wrapper, 'Abbrechen').trigger('click')
    await flushPromises()

    const mqttDisableButton = wrapper.findAll('button').find(button => button.attributes('title') === 'MQTT deaktivieren')
    expect(mqttDisableButton).toBeTruthy()
    await mqttDisableButton.trigger('click')
    await flushPromises()
    expect(authApi.deleteMqttPassword).toHaveBeenCalledWith('bob')

    await openTab(wrapper, 'API Keys')
    expect(wrapper.text()).toContain('Main key')
    await findButton(wrapper, '+ API Key').trigger('click')
    await flushPromises()
    await wrapper.find('form input').setValue('Automation')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(authApi.createApiKey).toHaveBeenCalledWith('Automation')
    expect(wrapper.text()).toContain('obs_secret_key')

    await wrapper.find('.table button').trigger('click')
    await flushPromises()
    expect(authApi.deleteApiKey).toHaveBeenCalledWith('key-1')

    wrapper.unmount()
  })
})

describe('SettingsView history and icon coverage', () => {
  it('covers history backend settings and datapoint filters', async () => {
    const wrapper = await mountSettingsView()
    await openTab(wrapper, 'Historie DB')

    const pluginSelect = wrapper.find('.card select')
    await pluginSelect.setValue('influxdb')
    await flushPromises()
    expect(wrapper.text()).toContain('API Token')

    const selects = wrapper.findAll('select')
    await selects[1].setValue('1')
    await flushPromises()
    expect(wrapper.text()).toContain('Benutzername')

    await selects[1].setValue('3')
    await flushPromises()
    expect(wrapper.text()).toContain('Datenbank')

    await pluginSelect.setValue('timescaledb')
    await flushPromises()
    expect(wrapper.text()).toContain('Connection DSN')

    await wrapper.find('input[type="number"]').setValue('24')
    await findButton(wrapper, 'Verbindung testen').trigger('click')
    await flushPromises()
    expect(historySettingsApi.test).toHaveBeenCalledWith(expect.objectContaining({
      plugin: 'timescaledb',
      default_window_hours: 24,
    }))
    expect(wrapper.text()).toContain('History OK')

    await findButtonContaining(wrapper, 'Speichern & aktivieren').trigger('click')
    await flushPromises()
    expect(historySettingsApi.update).toHaveBeenCalledWith(expect.objectContaining({
      plugin: 'timescaledb',
      default_window_hours: 24,
    }))
    expect(wrapper.text()).toContain('Historie DB gespeichert und aktiviert')

    await wrapper.find('[data-testid="input-history-filter-search"]').setValue('temp')
    await flushPromises()
    expect(wrapper.text()).toContain('Temperature')

    await wrapper.find('[data-testid="toggle-history-dp-temp"]').trigger('click')
    await flushPromises()
    expect(dpApi.update).toHaveBeenCalledWith('dp-temp', { record_history: true })

    await wrapper.find('[data-testid="btn-history-filter-disable-all"]').trigger('click')
    await flushPromises()
    expect(dpApi.update).toHaveBeenCalledWith('dp-temp', { record_history: false })

    await wrapper.find('[data-testid="btn-history-filter-enable-all"]').trigger('click')
    await flushPromises()
    expect(dpApi.update).toHaveBeenCalledWith('dp-temp', { record_history: true })

    wrapper.unmount()
  })

  it('covers icon selection, import/export, KNX UF, and FontAwesome settings', async () => {
    const createObjectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:icons')
    const revokeObjectUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const wrapper = await mountSettingsView()
    await openTab(wrapper, 'Icons')

    expect(iconsApi.list).toHaveBeenCalled()
    expect(wrapper.find('[data-testid="icons-grid"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('custom_light')

    await wrapper.find('[data-testid="input-icons-search"]').setValue('switch')
    await flushPromises()
    expect(wrapper.text()).toContain('kuf_switch')

    await wrapper.find('[data-testid="btn-icons-select-all"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="btn-icons-export"]').trigger('click')
    await flushPromises()
    expect(iconsApi.export).toHaveBeenCalledWith(['custom_light', 'kuf_switch'])
    expect(clickSpy).toHaveBeenCalled()
    expect(createObjectUrl).toHaveBeenCalled()
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:icons')

    await wrapper.find('[data-testid="btn-icons-delete"]').trigger('click')
    await flushPromises()
    expect(iconsApi.delete).toHaveBeenCalledWith(['custom_light', 'kuf_switch'])
    expect(wrapper.text()).toContain('2 Icon(s) gelöscht')

    const iconInput = wrapper.find('[data-testid="input-icons-file"]')
    await selectFile(iconInput, new File(['<svg />'], 'custom.svg', { type: 'image/svg+xml' }))
    expect(iconsApi.import).toHaveBeenCalled()
    expect(wrapper.text()).toContain('2 Icons importiert')

    const dropzone = wrapper.find('[data-testid="icons-dropzone"]')
    await dropzone.trigger('dragover')
    await dropzone.trigger('dragleave')
    await dropzone.trigger('drop', {
      dataTransfer: {
        files: [new File(['zip'], 'icons.zip', { type: 'application/zip' })],
      },
    })
    await flushPromises()
    expect(iconsApi.import).toHaveBeenCalledTimes(2)

    await wrapper.find('[data-testid="btn-knxuf-import"]').trigger('click')
    await flushPromises()
    expect(iconsApi.importKnxuf).toHaveBeenCalled()
    expect(wrapper.text()).toContain('KNX UF importiert')

    await wrapper.find('[data-testid="input-fa-apikey"]').setValue('new-fa-key')
    await findButton(wrapper, 'Speichern').trigger('click')
    await flushPromises()
    expect(iconsApi.saveSettings).toHaveBeenCalledWith({ fa_api_key: 'new-fa-key' })
    expect(wrapper.text()).toContain('gespeichert')

    await findButton(wrapper, 'Löschen').trigger('click')
    await flushPromises()
    expect(iconsApi.saveSettings).toHaveBeenCalledWith({ fa_api_key: null })

    await wrapper.find('[data-testid="input-fa-names"]').setValue('house,user')
    await wrapper.find('[data-testid="select-fa-style"]').setValue('brands')
    await wrapper.find('[data-testid="btn-fa-import"]').trigger('click')
    await flushPromises()
    expect(iconsApi.importFa).toHaveBeenCalledWith({ icons: ['house', 'user'], style: 'brands' })
    expect(wrapper.text()).toContain('FontAwesome importiert')

    wrapper.unmount()
    createObjectUrl.mockRestore()
    revokeObjectUrl.mockRestore()
    clickSpy.mockRestore()
  })
})
