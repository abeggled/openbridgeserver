/**
 * Settings → "Visu-Geräte" tab: device profiles with start page and return
 * timeout. The tab is admin-only (like users/security) and hosts
 * VisuDevicesPanel, which has its own spec.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  const storage = {
    getItem: vi.fn().mockReturnValue('de'),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  }
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })

  vi.doMock('@/api/client', () => ({
    settingsApi: {
      get: vi.fn().mockResolvedValue({
        data: {
          timezone: 'Europe/Zurich', date_format: 'dd.MM.yyyy', time_format: 'HH:mm:ss',
          language: 'de', region_format: 'auto', currency: 'auto',
        },
      }),
      update: vi.fn().mockResolvedValue({ data: {} }),
      displaySettings: vi.fn().mockResolvedValue({
        data: { supported_region_formats: ['auto'], supported_currencies: ['auto'] },
      }),
    },
    historySettingsApi: {
      get: vi.fn().mockResolvedValue({ data: { plugin: 'sqlite', default_window_hours: 168 } }),
      update: vi.fn().mockResolvedValue({ data: {} }),
      test: vi.fn().mockResolvedValue({ data: { ok: true } }),
    },
    dpApi: { listAll: vi.fn().mockResolvedValue({ data: { items: [] } }), update: vi.fn().mockResolvedValue({ data: {} }) },
    securityApi: {
      listUrlTargets: vi.fn().mockResolvedValue({ data: { path: '/allowlist.yaml', entries: [] } }),
    },
    authApi: {
      listUsers: vi.fn().mockResolvedValue({ data: [] }),
      listApiKeys: vi.fn().mockResolvedValue({ data: [] }),
    },
    adapterApi: { listInstances: vi.fn().mockResolvedValue({ data: [] }) },
    configApi: { export: vi.fn().mockResolvedValue({ data: {} }), exportDb: vi.fn().mockResolvedValue({ data: new Blob(['db']) }) },
    autobackupApi: { getConfig: vi.fn().mockResolvedValue({ data: {} }), list: vi.fn().mockResolvedValue({ data: [] }) },
    knxprojApi: { listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }) },
    iconsApi: { list: vi.fn().mockResolvedValue({ data: { icons: [] } }), getSettings: vi.fn().mockResolvedValue({ data: {} }) },
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
    supportApi: {
      categories: vi.fn().mockResolvedValue({ data: [] }),
      getDebugStatus: vi.fn().mockResolvedValue({ data: { active: false, level: 'INFO', until: null } }),
    },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountSettingsView(user = { id: 'u1', username: 'admin', is_admin: true }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = user

  const mod = await import('@/views/SettingsView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: {
        HierarchyManager: true,
        VisuDevicesPanel: { template: '<div data-testid="visu-devices-panel-stub" />' },
        VisuAlarmsPanel: { template: '<div data-testid="visu-alarms-panel-stub" />' },
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

function tabButton(wrapper) {
  return wrapper.findAll('button').find(button => button.text() === 'Visu-Geräte')
}

describe('SettingsView — Visu devices tab', () => {
  it('shows the tab to admins and renders the panel when it is opened', async () => {
    const wrapper = await mountSettingsView()

    expect(wrapper.find('[data-testid="visu-devices-panel-stub"]').exists()).toBe(false)
    await tabButton(wrapper).trigger('click')
    await flushPromises()

    const tab = wrapper.get('[data-testid="visudevices-tab"]')
    expect(tab.find('[data-testid="visu-devices-panel-stub"]').exists()).toBe(true)
    expect(tab.classes()).not.toContain('pointer-events-none')
  })

  it('hides the tab from non-admin users', async () => {
    const wrapper = await mountSettingsView({ id: 'u2', username: 'alice', is_admin: false })

    expect(tabButton(wrapper)).toBeUndefined()
  })

  it('shows the tab read-only to the demo user', async () => {
    const wrapper = await mountSettingsView({ id: 'u3', username: 'demo', is_admin: false })

    await tabButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="visudevices-tab"]').classes()).toContain('pointer-events-none')
  })
})

function alarmsTab(wrapper) {
  return wrapper.findAll('button').find(button => button.text() === 'Visu-Alarme')
}

describe('SettingsView — Visu alarms tab', () => {
  it('shows the tab to admins and renders the panel when it is opened', async () => {
    const wrapper = await mountSettingsView()

    await alarmsTab(wrapper).trigger('click')
    await flushPromises()

    const tab = wrapper.get('[data-testid="visualarms-tab"]')
    expect(tab.find('[data-testid="visu-alarms-panel-stub"]').exists()).toBe(true)
    expect(tab.classes()).not.toContain('pointer-events-none')
  })

  it('hides the tab from non-admin users and shows it read-only to the demo user', async () => {
    expect(alarmsTab(await mountSettingsView({ id: 'u2', username: 'alice', is_admin: false }))).toBeUndefined()

    const demo = await mountSettingsView({ id: 'u3', username: 'demo', is_admin: false })
    await alarmsTab(demo).trigger('click')
    await flushPromises()
    expect(demo.get('[data-testid="visualarms-tab"]').classes()).toContain('pointer-events-none')
  })
})
