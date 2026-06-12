import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let listUrlTargets
let checkUrlTarget
let addUrlTarget
let exportConfig
let exportDb
let importConfig
let importDb
let authApi
let historySettingsApi
let dpApi
let autobackupApi
let iconsApi

beforeEach(() => {
  vi.resetModules()
  const storage = {
    getItem: vi.fn().mockReturnValue('de'),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  }
  Object.defineProperty(window, 'localStorage', {
    value: storage,
    configurable: true,
  })
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
  })
  listUrlTargets = vi.fn().mockResolvedValue({
    data: {
      path: '/data/secrets/url-target-allowlist.yaml',
      entries: [],
    },
  })
  checkUrlTarget = vi.fn().mockResolvedValue({
    data: {
      allowed: false,
      url: 'http://10.38.113.23/api/v1/status',
      host: '10.38.113.23',
      resolved_ips: ['10.38.113.23'],
      blocked_ips: ['10.38.113.23'],
      reason: 'URL target resolves to an internal address',
      suggested_target: '10.38.113.23/32',
    },
  })
  addUrlTarget = vi.fn().mockResolvedValue({ data: { target: '10.38.113.23/32' } })
  exportConfig = vi.fn().mockResolvedValue({ data: { exported: true } })
  exportDb = vi.fn().mockResolvedValue({ data: new Blob(['sqlite'], { type: 'application/octet-stream' }) })
  importConfig = vi.fn().mockResolvedValue({
    data: {
      datapoints_created: 1,
      datapoints_updated: 1,
      bindings_created: 2,
      bindings_updated: 1,
      knx_group_addresses_upserted: 1,
      logic_graphs_created: 1,
      logic_graphs_updated: 0,
      icons_imported: 1,
      visu_nodes_upserted: 1,
    },
  })
  importDb = vi.fn().mockResolvedValue({ data: { message: 'ok', adapters_restarted: 1 } })
  authApi = {
    listUsers: vi.fn().mockResolvedValue({ data: [{ username: 'admin', is_admin: true }] }),
    listApiKeys: vi.fn().mockResolvedValue({ data: [{ id: 'key-1', name: 'Bridge API' }] }),
    changePassword: vi.fn().mockResolvedValue({ data: {} }),
    createUser: vi.fn().mockResolvedValue({ data: { username: 'operator' } }),
    deleteUser: vi.fn().mockResolvedValue({ data: {} }),
    setMqttPassword: vi.fn().mockResolvedValue({ data: {} }),
    deleteMqttPassword: vi.fn().mockResolvedValue({ data: {} }),
    createApiKey: vi.fn().mockResolvedValue({ data: { key: 'obs_secret' } }),
    deleteApiKey: vi.fn().mockResolvedValue({ data: {} }),
  }
  historySettingsApi = {
    get: vi.fn().mockResolvedValue({ data: { plugin: 'sqlite', default_window_hours: 168 } }),
    update: vi.fn().mockResolvedValue({ data: {} }),
    test: vi.fn().mockResolvedValue({ data: { ok: true } }),
  }
  dpApi = {
    listAll: vi.fn().mockResolvedValue({
      data: {
        items: [
          { id: 'dp-1', name: 'Temperature', data_type: 'FLOAT', unit: 'C', record_history: false },
          { id: 'dp-2', name: 'Switch', data_type: 'BOOL', record_history: true },
        ],
      },
    }),
    update: vi.fn().mockResolvedValue({ data: {} }),
  }
  autobackupApi = {
    getConfig: vi.fn().mockResolvedValue({ data: { enabled: true, hour: 3, retention_days: 7 } }),
    list: vi.fn().mockResolvedValue({ data: [{ name: '20240506-0300', size: 2048 }] }),
    setConfig: vi.fn().mockResolvedValue({ data: {} }),
    runNow: vi.fn().mockResolvedValue({ data: { name: '20240507-0300' } }),
    restore: vi.fn().mockResolvedValue({ data: { datapoints: 2, bindings: 3, visu_nodes: 1, errors: ['adapter restart'] } }),
  }
  iconsApi = {
    getSettings: vi.fn().mockResolvedValue({ data: { fa_api_key: 'saved' } }),
    saveSettings: vi.fn().mockResolvedValue({ data: {} }),
    list: vi.fn().mockResolvedValue({ data: { icons: [{ name: 'home', size: 1024 }] } }),
    import: vi.fn().mockResolvedValue({ data: { message: 'uploaded' } }),
    importKnxuf: vi.fn().mockResolvedValue({ data: { imported: 1, message: 'imported' } }),
    importFa: vi.fn().mockResolvedValue({ data: { imported: 1, message: 'imported', debug: [] } }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
    export: vi.fn().mockResolvedValue({ data: new Blob(['icons'], { type: 'application/zip' }) }),
  }

  vi.doMock('@/api/client', () => ({
    settingsApi: {
      get: vi.fn().mockResolvedValue({ data: { timezone: 'Europe/Berlin' } }),
      update: vi.fn().mockResolvedValue({ data: {} }),
    },
    historySettingsApi,
    dpApi,
    securityApi: {
      listUrlTargets,
      checkUrlTarget,
      addUrlTarget,
      deleteUrlTarget: vi.fn().mockResolvedValue({ data: { deleted: true } }),
    },
    authApi,
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({ data: [] }),
    },
    configApi: {
      export: exportConfig,
      exportDb,
      import: importConfig,
      importDb,
      resetBindings: vi.fn().mockResolvedValue({ data: { deleted: 1 } }),
      resetDatapoints: vi.fn().mockResolvedValue({ data: { deleted: 1, bindings_deleted: 1 } }),
      resetLogic: vi.fn().mockResolvedValue({ data: { deleted: 1 } }),
      resetAdapters: vi.fn().mockResolvedValue({ data: { deleted: 1, bindings_deleted: 1 } }),
      reset: vi.fn().mockResolvedValue({
        data: {
          datapoints_deleted: 1,
          bindings_deleted: 1,
          adapter_instances_deleted: 1,
          knx_group_addresses_deleted: 1,
          logic_graphs_deleted: 1,
          icons_deleted: 1,
        },
      }),
    },
    autobackupApi,
    knxprojApi: {
      listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }),
    },
    iconsApi,
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountSettingsView({ isAdmin = true } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: isAdmin ? 'admin' : 'viewer', is_admin: isAdmin }

  const mod = await import('@/views/SettingsView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: {
        HierarchyManager: true,
        Modal: { template: '<div><slot /><slot name="footer" /></div>' },
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

describe('SettingsView security tab', () => {
  it('hides the import/export tab for non-admin users', async () => {
    const wrapper = await mountSettingsView({ isAdmin: false })

    expect(wrapper.text()).not.toContain('Datenmanagement')
  })

  it('allows admins to export config and database backups', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:settings-export')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const wrapper = await mountSettingsView()

    await wrapper.vm.doExport()
    expect(exportConfig).toHaveBeenCalled()
    expect(createObjectURL).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()

    await wrapper.vm.doExportDb()
    expect(exportDb).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalledTimes(2)

    clickSpy.mockRestore()
    createObjectURL.mockRestore()
    revokeObjectURL.mockRestore()
  })

  it('covers admin account, key, history and backup workflows', async () => {
    const wrapper = await mountSettingsView()

    wrapper.vm.openCreateUser()
    Object.assign(wrapper.vm.userForm, {
      username: 'operator',
      password: 'secret',
      is_admin: true,
      mqtt_enabled: true,
      mqtt_password: 'mqtt-secret',
    })
    await wrapper.vm.doCreateUser()
    expect(authApi.createUser).toHaveBeenCalledWith({
      username: 'operator',
      password: 'secret',
      is_admin: true,
      mqtt_enabled: true,
      mqtt_password: 'mqtt-secret',
    })

    wrapper.vm.confirmDeleteUser({ username: 'operator' })
    expect(wrapper.vm.showUserConfirm).toBe(true)
    await wrapper.vm.doDeleteUser()
    expect(authApi.deleteUser).toHaveBeenCalledWith('operator')

    wrapper.vm.createApiKey()
    wrapper.vm.newKeyName = 'Bridge API'
    await wrapper.vm.doCreateKey()
    expect(authApi.createApiKey).toHaveBeenCalledWith('Bridge API')
    expect(wrapper.vm.newKeySecret).toBe('obs_secret')
    await wrapper.vm.deleteApiKey('key-1')
    expect(authApi.deleteApiKey).toHaveBeenCalledWith('key-1')

    Object.assign(wrapper.vm.pwForm, { current: 'old', new1: 'new', new2: 'different' })
    await wrapper.vm.changePassword()
    expect(wrapper.vm.pwMsg.ok).toBe(false)
    Object.assign(wrapper.vm.pwForm, { current: 'old', new1: 'new', new2: 'new' })
    await wrapper.vm.changePassword()
    expect(authApi.changePassword).toHaveBeenCalledWith('old', 'new')

    wrapper.vm.histForm.default_window_hours = 'not-a-number'
    await wrapper.vm.saveHistorySettings()
    expect(historySettingsApi.update).toHaveBeenCalledWith(expect.objectContaining({ default_window_hours: 168 }))
    await wrapper.vm.testHistoryConnection()
    expect(historySettingsApi.test).toHaveBeenCalledWith(expect.objectContaining({ default_window_hours: 168 }))

    await wrapper.vm.loadHistoryFilterDps()
    wrapper.vm.histFilterSearch = 'temp'
    expect(wrapper.vm.histFilteredDps.map(dp => dp.id)).toEqual(['dp-1'])
    await wrapper.vm.toggleHistoryFilter(wrapper.vm.histFilteredDps[0])
    expect(dpApi.update).toHaveBeenCalledWith('dp-1', { record_history: true })
    await wrapper.vm.histFilterSetAll(true)
    expect(wrapper.vm.histFilterExcludedCount).toBe(0)

    await wrapper.vm.saveAutobackupConfig()
    expect(autobackupApi.setConfig).toHaveBeenCalledWith(expect.objectContaining({ enabled: true }))
    await wrapper.vm.runAutobackupNow()
    expect(autobackupApi.runNow).toHaveBeenCalled()
    wrapper.vm.selectedAutobackup = '20240506-0300'
    await wrapper.vm.restoreAutobackup()
    expect(autobackupApi.restore).toHaveBeenCalledWith('20240506-0300')
    expect(wrapper.vm.formatAutobackupName('20240506-0300')).toBe('06.05.2024 03:00 Uhr')
    expect(wrapper.vm.formatBytes(2048)).toBe('2.0 KB')
  })

  it('covers config import and icon library workflows', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:settings-icons')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const wrapper = await mountSettingsView()

    await wrapper.vm.onImportFile({
      target: { files: [new File([JSON.stringify({ datapoints: [] })], 'config.json', { type: 'application/json' })] },
    })
    expect(importConfig).toHaveBeenCalledWith({ datapoints: [] })
    expect(wrapper.vm.importResult.ok).toBe(true)

    await wrapper.vm.onImportDbFile({
      target: { files: [new File(['sqlite'], 'backup.sqlite', { type: 'application/octet-stream' })] },
    })
    expect(importDb).toHaveBeenCalled()
    expect(wrapper.vm.importDbResult.ok).toBe(true)

    await wrapper.vm.loadIcons()
    expect(iconsApi.list).toHaveBeenCalled()
    wrapper.vm.iconsSearch = 'ho'
    expect(wrapper.vm.iconsFiltered.map(icon => icon.name)).toEqual(['home'])
    wrapper.vm.iconsToggle('home')
    expect(wrapper.vm.iconsSelected.has('home')).toBe(true)
    wrapper.vm.iconsSelectAll()
    expect(wrapper.vm.iconsSelected.size).toBe(0)
    wrapper.vm.iconsSelectAll()
    expect(wrapper.vm.iconsSelected.size).toBe(1)

    await wrapper.vm.doIconsExport()
    expect(iconsApi.export).toHaveBeenCalledWith(['home'])
    expect(clickSpy).toHaveBeenCalled()

    await wrapper.vm.doIconsDelete()
    expect(iconsApi.delete).toHaveBeenCalledWith(['home'])

    wrapper.vm.faApiKey = 'fa-secret'
    await wrapper.vm.doSaveFaKey()
    expect(iconsApi.saveSettings).toHaveBeenCalledWith({ fa_api_key: 'fa-secret' })
    await wrapper.vm.doDeleteFaKey()
    expect(iconsApi.saveSettings).toHaveBeenCalledWith({ fa_api_key: null })

    wrapper.vm.faIconNames = 'house, bolt'
    await wrapper.vm.doFaImport()
    expect(iconsApi.importFa).toHaveBeenCalledWith({ icons: ['house', 'bolt'], style: 'solid' })
    await wrapper.vm.doKnxufImport()
    expect(iconsApi.importKnxuf).toHaveBeenCalled()

    clickSpy.mockRestore()
    createObjectURL.mockRestore()
    revokeObjectURL.mockRestore()
  })

  it('surfaces icon workflow errors without keeping busy state stuck', async () => {
    const wrapper = await mountSettingsView()

    iconsApi.import.mockRejectedValueOnce({ response: { data: { detail: 'SVG ungültig' } } })
    await wrapper.vm.onIconsFileSelect({
      target: { files: [new File(['<svg />'], 'bad.svg', { type: 'image/svg+xml' })], value: 'bad.svg' },
    })
    expect(wrapper.vm.iconsUploading).toBe(false)
    expect(wrapper.vm.iconsMsg).toEqual({ ok: false, text: 'SVG ungültig' })

    wrapper.vm.iconsSelected = new Set(['home'])
    iconsApi.delete.mockRejectedValueOnce({ response: { data: { detail: 'delete failed' } } })
    await wrapper.vm.doIconsDelete()
    expect(wrapper.vm.iconsMsg).toEqual({ ok: false, text: 'delete failed' })

    iconsApi.export.mockRejectedValueOnce({ response: { data: { detail: 'export failed' } } })
    await wrapper.vm.doIconsExport()
    expect(wrapper.vm.iconsMsg).toEqual({ ok: false, text: 'export failed' })

    wrapper.vm.faIconNames = 'broken'
    iconsApi.importFa.mockRejectedValueOnce({ response: { data: { detail: 'fa failed' } } })
    await wrapper.vm.doFaImport()
    expect(wrapper.vm.faImporting).toBe(false)
    expect(wrapper.vm.faMsg).toEqual({ ok: false, text: 'fa failed', debug: [] })
  })

  it('checks a private URL target and allows the suggested CIDR entry', async () => {
    checkUrlTarget
      .mockResolvedValueOnce({
        data: {
          allowed: false,
          url: 'http://internal.example/api/v1/status',
          host: 'internal.example',
          resolved_ips: ['10.38.113.23'],
          blocked_ips: ['10.38.113.23'],
          reason: 'URL target resolves to an internal address',
          suggested_target: '10.38.113.23/32',
        },
      })
      .mockResolvedValueOnce({
        data: {
          allowed: true,
          url: 'http://internal.example/api/v1/status',
          host: 'internal.example',
          resolved_ips: ['10.38.113.23'],
          blocked_ips: [],
          reason: 'URL target is allowed',
          allowlisted_by: '10.38.113.23/32',
        },
      })

    const wrapper = await mountSettingsView()
    const securityTab = wrapper.findAll('button').find(button => button.text() === 'Sicherheit')
    expect(securityTab).toBeTruthy()
    await securityTab.trigger('click')
    await flushPromises()

    expect(listUrlTargets).toHaveBeenCalled()
    expect(wrapper.text()).toContain('/data/secrets/url-target-allowlist.yaml')

    await wrapper.find('[data-testid="security-url-target-check-input"]').setValue('internal.example/api/v1/status')
    await wrapper.find('[data-testid="security-url-target-check"]').trigger('click')
    await flushPromises()

    expect(checkUrlTarget).toHaveBeenCalledWith({ url: 'http://internal.example/api/v1/status' })
    expect(wrapper.text()).toContain('Ziel blockiert')
    expect(wrapper.text()).toContain('10.38.113.23/32')

    await wrapper.find('[data-testid="security-url-target-allow-suggested"]').trigger('click')
    await flushPromises()

    expect(addUrlTarget).toHaveBeenCalledWith({
      target: '10.38.113.23/32',
      reason: 'Freigabe nach URL-Zielprüfung',
    })
    expect(checkUrlTarget).toHaveBeenLastCalledWith({ url: 'http://internal.example/api/v1/status' })
    expect(wrapper.text()).toContain('Ziel erlaubt')
    wrapper.unmount()
  })
})
