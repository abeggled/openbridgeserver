/**
 * Regional format / currency settings UI (issue #1073).
 *
 * The regional format is an explicit setting next to the language, so this spec
 * covers the option lists (from the server and from the local fallback), the
 * live preview, saving, and the numeric helpers on the page that follow the
 * saved format.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const NBSP = ' '

let settingsGet
let settingsUpdate
let displaySettings

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

  settingsGet = vi.fn().mockResolvedValue({
    data: {
      timezone: 'Europe/Zurich',
      date_format: 'dd.MM.yyyy',
      time_format: 'HH:mm:ss',
      language: 'de',
      region_format: 'auto',
      currency: 'auto',
    },
  })
  settingsUpdate = vi.fn().mockResolvedValue({ data: {} })
  displaySettings = vi.fn().mockResolvedValue({
    data: {
      supported_region_formats: ['auto', 'de-DE', 'de-CH', 'en-US'],
      supported_currencies: ['auto', 'EUR', 'CHF'],
    },
  })

  vi.doMock('@/api/client', () => ({
    settingsApi: { get: settingsGet, update: settingsUpdate, displaySettings },
    historySettingsApi: {
      get: vi.fn().mockResolvedValue({ data: { plugin: 'sqlite', default_window_hours: 168 } }),
      update: vi.fn().mockResolvedValue({ data: {} }),
      test: vi.fn().mockResolvedValue({ data: { ok: true } }),
    },
    dpApi: { listAll: vi.fn().mockResolvedValue({ data: { items: [] } }), update: vi.fn().mockResolvedValue({ data: {} }) },
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
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.unstubAllGlobals()
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

describe('SettingsView regional format (#1073)', () => {
  it('offers the server-provided formats with a sample and a live preview', async () => {
    const wrapper = await mountSettingsView()

    const regionSelect = wrapper.find('[data-testid="region-format-select"]')
    const options = regionSelect.findAll('option')
    expect(options.map(option => option.attributes('value'))).toEqual(['auto', 'de-DE', 'de-CH', 'en-US'])
    // 'auto' explains which format the current language resolves to.
    expect(options[0].text()).toContain('de-DE')
    expect(options[0].text()).toContain('1.234,50')
    // Explicit entries show the localized language name, the code and a sample.
    expect(options[2].text()).toContain('de-CH')
    expect(options[2].text()).toContain("1'234.50")

    const currencyOptions = wrapper.find('[data-testid="currency-select"]').findAll('option')
    expect(currencyOptions.map(option => option.attributes('value'))).toEqual(['auto', 'EUR', 'CHF'])
    expect(currencyOptions[0].text()).toContain('EUR')
    expect(currencyOptions[2].text()).toContain('CHF')

    const preview = wrapper.find('[data-testid="region-format-preview"]')
    expect(preview.text()).toContain('1.234,500')
    expect(preview.text()).toContain(`1.234,50${NBSP}€`)
  })

  it('updates the preview and the derived currency when another format is picked', async () => {
    const wrapper = await mountSettingsView()

    await wrapper.find('[data-testid="region-format-select"]').setValue('de-CH')

    const preview = wrapper.find('[data-testid="region-format-preview"]')
    expect(preview.text()).toContain("1'234.500")
    expect(preview.text()).toContain(`CHF${NBSP}1'234.50`)
    // 'auto' currency now derives CHF from the Swiss format, not from the language.
    expect(wrapper.find('[data-testid="currency-select"]').findAll('option')[0].text()).toContain('CHF')
  })

  it('saves the regional format alongside timezone and formats', async () => {
    const wrapper = await mountSettingsView()

    await wrapper.find('[data-testid="region-format-select"]').setValue('en-US')
    await wrapper.find('[data-testid="currency-select"]').setValue('EUR')
    const saveButton = wrapper.findAll('button').find(button => button.text().trim() === 'Speichern')
    await saveButton.trigger('click')
    await flushPromises()

    expect(settingsUpdate).toHaveBeenCalledWith({
      timezone: 'Europe/Zurich',
      date_format: 'dd.MM.yyyy',
      time_format: 'HH:mm:ss',
      language: 'de',
      region_format: 'en-US',
      currency: 'EUR',
    })
  })

  it('falls back to the built-in option lists when the public endpoint is unreachable', async () => {
    displaySettings.mockRejectedValue(new Error('offline'))

    const wrapper = await mountSettingsView()

    const regionValues = wrapper.find('[data-testid="region-format-select"]').findAll('option').map(o => o.attributes('value'))
    expect(regionValues).toContain('de-AT')
    expect(regionValues).toContain('es-ES')
    const currencyValues = wrapper.find('[data-testid="currency-select"]').findAll('option').map(o => o.attributes('value'))
    expect(currencyValues).toEqual(['auto', 'EUR', 'CHF', 'USD', 'GBP'])
  })

  it('falls back to the bare code when the display name cannot be resolved', async () => {
    settingsGet.mockResolvedValue({
      data: { timezone: 'Europe/Zurich', language: 'de', region_format: 'auto', currency: 'auto' },
    })
    const wrapper = await mountSettingsView()

    // Intl.DisplayNames throws for a structurally invalid locale tag …
    expect(wrapper.vm.regionDisplayName('not a language tag')).toBe('not a language tag')

    // … and may also return nothing for an unknown but well-formed code.
    const RealDisplayNames = Intl.DisplayNames
    vi.stubGlobal('Intl', { ...Intl, DisplayNames: class { of() { return undefined } } })
    try {
      expect(wrapper.vm.regionDisplayName('de-CH')).toBe('de-CH')
    } finally {
      vi.stubGlobal('Intl', { ...Intl, DisplayNames: RealDisplayNames })
    }
  })

  it('formats byte sizes and support metrics in the saved regional format', async () => {
    const wrapper = await mountSettingsView()

    expect(wrapper.vm.formatBytes(512)).toBe('512 B')
    expect(wrapper.vm.formatBytes(2048)).toBe('2,0 KB')
    expect(wrapper.vm.formatBytes(5 * 1024 * 1024)).toBe('5,0 MB')

    expect(wrapper.vm.supportFormatNumber(1234567)).toBe('1.234.567')
    // At most three fraction digits, never padded — matching toLocaleString().
    expect(wrapper.vm.supportFormatNumber(1234.56789)).toBe('1.234,568')
    expect(wrapper.vm.supportFormatNumber('nope')).toBe('—')
    expect(wrapper.vm.supportFormatPercent(42.55)).toBe('42,6%')
    expect(wrapper.vm.supportFormatPercent(null)).toBe('—')
    expect(wrapper.vm.supportFormatCpu({ system: { cpu_count: 4, load_average: { '1m': 1.234 } } }))
      .toContain('1,23')
    expect(wrapper.vm.supportFormatCpu({ system: { cpu_count: 4 } })).not.toContain(',')
    expect(wrapper.vm.supportFormatCpu({ system: {} })).toBe('—')
  })
})
