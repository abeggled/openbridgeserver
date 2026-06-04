import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let listUrlTargets
let checkUrlTarget
let addUrlTarget

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
      listUrlTargets,
      checkUrlTarget,
      addUrlTarget,
      deleteUrlTarget: vi.fn().mockResolvedValue({ data: { deleted: true } }),
    },
    authApi: {
      listUsers: vi.fn().mockResolvedValue({ data: [] }),
      listApiKeys: vi.fn().mockResolvedValue({ data: [] }),
    },
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({ data: [] }),
    },
    configApi: {},
    autobackupApi: {
      getConfig: vi.fn().mockResolvedValue({ data: {} }),
      list: vi.fn().mockResolvedValue({ data: [] }),
    },
    knxprojApi: {
      listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }),
    },
    iconsApi: {},
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
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
