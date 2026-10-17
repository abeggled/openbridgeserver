/**
 * Integrated help drawer wiring on the Settings → General tab (#896).
 *
 * The "Allgemein" tab hosts two cards ("Zeitzone/Format" and "Erscheinungsbild")
 * that each got a HelpButton in their card-header, pointing at the
 * settings-general / settings-appearance help_ids documented in
 * help/settings/general.md. This spec checks the buttons are present with the
 * right help_id and that clicking one opens the real help store.
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

// SettingsView is imported dynamically after vi.resetModules() in each test,
// so it resolves a different module instance than a static top-level
// `import HelpButton from ...` here would — findAllComponents(HelpButton)
// would never match by identity. The testid survives that just fine.
function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('SettingsView — help buttons on the General tab (#896)', () => {
  it('renders a help button for the timezone/format card', async () => {
    const wrapper = await mountSettingsView()
    expect(helpButton(wrapper, 'settings-general').exists()).toBe(true)
  })

  it('renders a help button for the appearance card', async () => {
    const wrapper = await mountSettingsView()
    expect(helpButton(wrapper, 'settings-appearance').exists()).toBe(true)
  })

  it('opens the help store with settings-general when its button is clicked', async () => {
    const wrapper = await mountSettingsView()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'settings-general').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('settings-general')
  })

  it('opens the help store with settings-appearance when that button is clicked', async () => {
    const wrapper = await mountSettingsView()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'settings-appearance').trigger('click')

    expect(helpStore.currentHelpId).toBe('settings-appearance')
  })
})

describe('SettingsView — help button on the Password tab (#896)', () => {
  it('renders a help button for the password card', async () => {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = 'password'
    await flushPromises()

    expect(helpButton(wrapper, 'settings-password').exists()).toBe(true)
  })

  it('opens the help store with settings-password when its button is clicked', async () => {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = 'password'
    await flushPromises()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'settings-password').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('settings-password')
  })
})

describe('SettingsView — help buttons on the Users tab (#896)', () => {
  it('renders a help button for the user list (covers rights editor content too)', async () => {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = 'users'
    await flushPromises()

    expect(helpButton(wrapper, 'settings-users').exists()).toBe(true)
  })

  it('opens the help store with settings-users when its button is clicked', async () => {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = 'users'
    await flushPromises()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'settings-users').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('settings-users')
  })
})

describe('SettingsView — help button on the API Keys tab (#896)', () => {
  it('renders a help button for the API keys list', async () => {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = 'apikeys'
    await flushPromises()

    expect(helpButton(wrapper, 'settings-apikeys').exists()).toBe(true)
  })

  it('opens the help store with settings-apikeys when its button is clicked', async () => {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = 'apikeys'
    await flushPromises()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'settings-apikeys').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('settings-apikeys')
  })
})

describe('SettingsView — help buttons on the Security tab (#896)', () => {
  async function mountOnSecurityTab() {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = 'security'
    await flushPromises()
    return wrapper
  }

  it('renders a help button for each of the three security cards', async () => {
    const wrapper = await mountOnSecurityTab()

    expect(helpButton(wrapper, 'settings-security').exists()).toBe(true)
    expect(helpButton(wrapper, 'settings-security-check').exists()).toBe(true)
    expect(helpButton(wrapper, 'settings-security-entries').exists()).toBe(true)
  })

  it('opens the help store with settings-security when the allowlist-intro button is clicked', async () => {
    const wrapper = await mountOnSecurityTab()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'settings-security').trigger('click')

    expect(helpStore.currentHelpId).toBe('settings-security')
  })

  it('opens the help store with settings-security-check when that button is clicked', async () => {
    const wrapper = await mountOnSecurityTab()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'settings-security-check').trigger('click')

    expect(helpStore.currentHelpId).toBe('settings-security-check')
  })

  it('opens the help store with settings-security-entries when that button is clicked', async () => {
    const wrapper = await mountOnSecurityTab()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'settings-security-entries').trigger('click')

    expect(helpStore.currentHelpId).toBe('settings-security-entries')
  })
})

// Support, Links, Hierarchie, Datenmanagement, Icons, Historie DB, Gefahrenzone (#896).
// Data-driven instead of one hand-written block per tab: each tab lists the help_ids
// its HelpButtons should carry, matching the help_id headings documented in the
// corresponding help/settings/<tab>.md page.
const remainingTabCases = [
  { tab: 'support', ids: ['settings-support-debug', 'settings-support-package', 'settings-support-viewer'] },
  { tab: 'links', ids: ['settings-links'] },
  { tab: 'hierarchy', ids: ['settings-hierarchy'] },
  {
    tab: 'importexport',
    ids: [
      'settings-importexport-config',
      'settings-importexport-db',
      'settings-importexport-messagearchive',
      'settings-importexport-autobackup',
      'settings-importexport-knx',
    ],
  },
  { tab: 'icons', ids: ['settings-icons', 'settings-icons-import', 'settings-icons-knxuf', 'settings-icons-fontawesome'] },
  { tab: 'history', ids: ['settings-history-db', 'settings-history-filter'] },
  { tab: 'dangerzone', ids: ['settings-dangerzone'] },
]

describe('SettingsView — help buttons on the remaining tabs (#896)', () => {
  it.each(remainingTabCases)('renders all expected help buttons on the $tab tab', async ({ tab, ids }) => {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = tab
    await flushPromises()

    for (const id of ids) {
      expect(helpButton(wrapper, id).exists()).toBe(true)
    }
  })

  const remainingButtonCases = remainingTabCases.flatMap(({ tab, ids }) => ids.map((id) => ({ tab, id })))

  it.each(remainingButtonCases)('opens the help store with $id when its button on the $tab tab is clicked', async ({ tab, id }) => {
    const wrapper = await mountSettingsView()
    wrapper.vm.activeTab = tab
    await flushPromises()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, id).trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(id)
  })
})
