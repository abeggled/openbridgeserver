import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let supportApi

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  const storage = {
    getItem: vi.fn().mockImplementation(key => (key === 'access_token' ? 'token' : 'de')),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  }
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })

  supportApi = {
    categories: vi.fn().mockResolvedValue({
      data: [
        { key: 'installation', label: 'Installation', description: 'Installation data' },
        { key: 'adapters', label: 'Adapters', description: 'Adapter data' },
      ],
    }),
    getDebugStatus: vi.fn().mockResolvedValue({ data: { active: false, level: 'INFO', until: null } }),
    enableDebugLog: vi.fn().mockResolvedValue({
      data: { active: true, level: 'DEBUG', until: new Date(Date.now() + 300000).toISOString() },
    }),
    disableDebugLog: vi.fn().mockResolvedValue({ data: { active: false, level: 'INFO', until: null } }),
    createPackage: vi.fn().mockResolvedValue({ data: supportPackage() }),
  }

  vi.doMock('@/api/client', () => ({
    settingsApi: {
      get: vi.fn().mockResolvedValue({ data: { timezone: 'Europe/Zurich' } }),
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
      changePassword: vi.fn().mockResolvedValue({ data: {} }),
    },
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({ data: [] }),
    },
    configApi: {
      export: vi.fn().mockResolvedValue({ data: {} }),
      exportDb: vi.fn().mockResolvedValue({ data: new Blob(['db']) }),
    },
    autobackupApi: {
      getConfig: vi.fn().mockResolvedValue({ data: {} }),
      list: vi.fn().mockResolvedValue({ data: [] }),
    },
    knxprojApi: {
      listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }),
    },
    iconsApi: {
      list: vi.fn().mockResolvedValue({ data: { icons: [] } }),
      getSettings: vi.fn().mockResolvedValue({ data: {} }),
    },
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
    supportApi,
  }))
})

afterEach(() => {
  vi.useRealTimers()
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

describe('SettingsView support tab', () => {
  it('creates packages, controls debug logging, and analyzes uploaded support files', async () => {
    const objectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:support')
    const revokeUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const wrapper = await mountSettingsView()
    const supportTab = wrapper.findAll('button').find(button => button.text() === 'Support')
    expect(supportTab).toBeTruthy()
    await supportTab.trigger('click')
    await flushPromises()

    expect(supportApi.categories).toHaveBeenCalled()
    expect(supportApi.getDebugStatus).toHaveBeenCalled()
    expect(wrapper.text()).toContain('Debug-Log Einstellungen')
    expect(wrapper.text()).toContain('Support-Paket erstellen')

    await wrapper.find('[data-testid="btn-support-debug-enable"]').trigger('click')
    await flushPromises()
    expect(supportApi.enableDebugLog).toHaveBeenCalledWith({ duration_seconds: 300, level: 'DEBUG' })
    expect(wrapper.text()).toContain('Debug aktiv')

    await vi.advanceTimersByTimeAsync(301000)
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Debug aus')

    supportApi.enableDebugLog.mockResolvedValueOnce({
      data: { active: true, level: 'DEBUG', until: new Date(Date.now() + 300000).toISOString() },
    })
    await wrapper.find('[data-testid="btn-support-debug-enable"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="btn-support-debug-disable"]').trigger('click')
    await flushPromises()
    expect(supportApi.disableDebugLog).toHaveBeenCalled()

    await wrapper.find('[data-testid="btn-support-package"]').trigger('click')
    await flushPromises()
    expect(supportApi.createPackage).toHaveBeenCalled()
    expect(objectUrl).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeUrl).toHaveBeenCalledWith('blob:support')

    const file = new File([JSON.stringify(supportPackage())], 'obs_support_test.json', { type: 'application/json' })
    const input = wrapper.find('input[accept=".json,application/json"]')
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('Support-Paket geladen')
    expect(wrapper.text()).toContain('KNX EG')
    expect(wrapper.text()).toContain('Transformationen')
    expect(wrapper.text()).toContain('python')
    expect(wrapper.text()).toContain('Debug ga=20/2/123')

    await wrapper.find('[data-testid="input-support-log-filter"]').setValue('kn ga=20/2/1*')
    expect(wrapper.text()).toContain('Debug ga=20/2/123')
    expect(wrapper.text()).not.toContain('MQTT reconnect')

    await wrapper.find('[data-testid="input-support-log-filter"]').setValue('does-not-match')
    expect(wrapper.text()).toContain('Keine Logeinträge passen zum Filter')

    await wrapper.findAll('button').find(button => button.text() === 'Ansicht leeren').trigger('click')
    expect(wrapper.text()).not.toContain('KNX EG')

    const invalid = new File(['{"not":"support"}'], 'invalid.json', { type: 'application/json' })
    Object.defineProperty(input.element, 'files', {
      value: [invalid],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('Die Datei ist kein gültiges Support-Paket')

    const malformedArrays = new File(
      [JSON.stringify({ generated_at: '2026-06-06T08:00:00Z', categories: ['logs'], adapters: { broken: true } })],
      'malformed.json',
      { type: 'application/json' },
    )
    Object.defineProperty(input.element, 'files', {
      value: [malformedArrays],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('Die Datei ist kein gültiges Support-Paket')

    wrapper.unmount()
    objectUrl.mockRestore()
    revokeUrl.mockRestore()
    clickSpy.mockRestore()
  })
})

function supportPackage() {
  return {
    schema_version: 1,
    generated_at: '2026-06-06T08:00:00Z',
    categories: ['installation', 'adapters', 'history', 'monitor', 'logs'],
    privacy: { automatic_upload: false, remote_access: false, sanitizer: 'central_recursive_v1' },
    installation: { obs_version: '1.2.3' },
    runtime: {
      os: 'Linux',
      os_release: '6.0',
      architecture: 'x86_64',
      uptime_seconds: 3661,
      resources: {
        system: {
          cpu_count: 4,
          load_average: { '1m': 0.42 },
          memory: { used_bytes: 1024, total_bytes: 4096 },
        },
        process: { max_rss_bytes: 2048 },
        disk: { available: true, free_bytes: 8192, total_bytes: 16384 },
        top_cpu_processes: { available: true, source: 'procfs', items: [{ pid: 1, name: 'python', cpu_percent: 12.3, rss_bytes: 2048 }] },
        top_memory_processes: { available: true, source: 'procfs', items: [{ pid: 1, name: 'python', rss_bytes: 2048 }] },
      },
    },
    adapters: [
      {
        id: 'inst-knx',
        name: 'KNX EG',
        adapter_type: 'KNX',
        enabled: true,
        connected: true,
        objects: 2,
        bindings: 3,
        active_transformations: 1,
        active_filters: 2,
        transactions_per_second: 0.2,
      },
    ],
    history: {
      active_plugin: 'sqlite',
      sqlite_storage: {
        total_values: 42,
        datapoints: 2,
        oldest_ts: '2026-06-06T07:00:00Z',
        newest_ts: '2026-06-06T08:00:00Z',
      },
    },
    monitor: { stats: { total: 11, storage: 'file', file_size_bytes: 2048, effective_retention_seconds: 3600 }, recent_sample_size: 10 },
    warning_history: [
      { ts: '2026-06-06T08:01:00Z', level: 'WARNING', logger: 'obs.knx', message: 'KNX ga=20/2/123 warning' },
      { ts: '2026-06-06T08:02:00Z', level: 'WARNING', logger: 'obs.mqtt', message: 'MQTT reconnect' },
    ],
    error_history: [],
    debug_log: [
      { ts: '2026-06-06T08:03:00Z', level: 'DEBUG', logger: 'obs.knx', message: 'Debug ga=20/2/123 telegram' },
      { ts: '2026-06-06T08:04:00Z', level: 'INFO', logger: 'obs.mqtt', message: 'MQTT reconnect' },
    ],
  }
}
