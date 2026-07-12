import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let listUsers
let getUserGrants
let userDeletionPreflight
let deleteUser

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

  listUsers = vi.fn().mockResolvedValue({
    data: [
      {
        id: 'u-3',
        username: 'viewer',
        is_admin: false,
        mqtt_enabled: true,
        mqtt_password_set: false,
        created_at: '2026-01-03T00:00:00Z',
      },
      {
        id: 'u-2',
        username: 'ops',
        is_admin: true,
        mqtt_enabled: false,
        mqtt_password_set: false,
        created_at: '2026-01-02T00:00:00Z',
      },
      {
        id: 'u-1',
        username: 'admin',
        is_admin: true,
        mqtt_enabled: true,
        mqtt_password_set: true,
        created_at: '2026-01-01T00:00:00Z',
      },
    ],
  })
  getUserGrants = vi.fn().mockImplementation((username) => Promise.resolve({
    data: {
      grants: username === 'viewer'
        ? [{ node_type: 'hierarchy', node_id: 'living-room', role: 'guest', effect: 'allow' }]
        : [],
    },
  }))

  vi.doMock('@/api/authz', () => ({
    authzApi: {
      getUserGrants,
      preview: vi.fn().mockResolvedValue({ data: { results: [] } }),
      updateUserGrants: vi.fn().mockResolvedValue({ data: { grants: [] } }),
    },
  }))
  userDeletionPreflight = vi.fn().mockResolvedValue({
    data: {
      revision: 'rev-1',
      username: 'viewer',
      visu_page_ids: ['page-1'],
      logic_graph_ids: ['graph-1'],
      filterset_ids: ['filter-1'],
      api_key_ids: ['key-1'],
      grant_count: 2,
      visu_acl_count: 1,
      filterset_state_count: 1,
    },
  })
  deleteUser = vi.fn().mockResolvedValue({ data: {} })
  vi.doMock('@/api/accountAdmin', () => ({
    accountAdminApi: { userDeletionPreflight, deleteUser },
  }))

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
      listUrlTargets: vi.fn().mockResolvedValue({ data: { path: '', entries: [] } }),
      checkUrlTarget: vi.fn().mockResolvedValue({ data: {} }),
      addUrlTarget: vi.fn().mockResolvedValue({ data: {} }),
      deleteUrlTarget: vi.fn().mockResolvedValue({ data: {} }),
    },
    authApi: {
      listUsers,
      createUser: vi.fn().mockResolvedValue({ data: {} }),
      deleteUser: vi.fn().mockResolvedValue({ data: {} }),
      setMqttPassword: vi.fn().mockResolvedValue({ data: {} }),
      deleteMqttPassword: vi.fn().mockResolvedValue({ data: {} }),
      listApiKeys: vi.fn().mockResolvedValue({ data: [] }),
      changePassword: vi.fn().mockResolvedValue({ data: {} }),
    },
    authzApi: {
      getUserGrants: vi.fn().mockResolvedValue({ data: { grants: [] } }),
      preview: vi.fn().mockResolvedValue({ data: { results: [] } }),
      updateUserGrants: vi.fn().mockResolvedValue({ data: { grants: [] } }),
    },
    hierarchyApi: {
      listTrees: vi.fn().mockResolvedValue({ data: [] }),
      getTreeNodes: vi.fn().mockResolvedValue({ data: [] }),
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
  vi.doUnmock('@/api/authz')
  vi.doUnmock('@/api/accountAdmin')
})

async function mountSettingsView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u-1', username: 'admin', is_admin: true }

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
        UserRightsEditor: {
          props: ['modelValue', 'username'],
          template: '<div v-if="modelValue" data-testid="rights-editor-stub">{{ username }}</div>',
        },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

describe('SettingsView users tab', () => {
  it('renders an owner-first operational user list from existing auth API fields', async () => {
    const wrapper = await mountSettingsView()
    const usersTab = wrapper.findAll('button').find(button => button.text() === 'Benutzer')
    expect(usersTab).toBeTruthy()
    await usersTab.trigger('click')
    await flushPromises()

    expect(listUsers).toHaveBeenCalled()
    const cards = wrapper.findAll('[data-testid^="user-card-"]')
    expect(cards.map(card => card.attributes('data-testid'))).toEqual([
      'user-card-admin',
      'user-card-ops',
      'user-card-viewer',
    ])

    expect(cards[0].text()).toContain('Du')
    expect(cards[0].text()).toContain('Aktuelles Konto')
    expect(cards[2].text()).toContain('MQTT ohne Passwort')
    expect(cards[2].text()).toContain('Passwort fehlt')
    expect(wrapper.find('[data-testid="user-delete-admin"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="user-delete-viewer"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="user-rights-summary-viewer"]').text()).toContain('Gast · 1 Bereiche')
    wrapper.unmount()
  })

  it('opens the dedicated rights editor from a user card', async () => {
    const wrapper = await mountSettingsView()
    const usersTab = wrapper.findAll('button').find(button => button.text() === 'Benutzer')
    await usersTab.trigger('click')
    await flushPromises()

    const button = wrapper.get('[data-testid="user-rights-viewer"]')
    expect(button.text()).toBe('Rechte bearbeiten')
    await button.trigger('click')

    expect(wrapper.get('[data-testid="rights-editor-stub"]').text()).toBe('viewer')
    wrapper.unmount()
  })

  it('hides delegation controls until a second user exists', async () => {
    listUsers.mockResolvedValueOnce({
      data: [{
        id: 'u-1',
        username: 'admin',
        is_admin: true,
        mqtt_enabled: false,
        mqtt_password_set: false,
        created_at: '2026-01-01T00:00:00Z',
      }],
    })
    const wrapper = await mountSettingsView()
    const usersTab = wrapper.findAll('button').find(button => button.text() === 'Benutzer')
    await usersTab.trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="user-rights-admin"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('requires reviewed impact and a successor before deleting owned artifacts', async () => {
    const wrapper = await mountSettingsView()
    const usersTab = wrapper.findAll('button').find(button => button.text() === 'Benutzer')
    await usersTab.trigger('click')
    await flushPromises()

    await wrapper.get('[data-testid="user-delete-viewer"]').trigger('click')
    await flushPromises()
    expect(userDeletionPreflight).toHaveBeenCalledWith('viewer')
    expect(wrapper.get('[data-testid="user-deletion-impact"]').text()).toContain('1 API-Schlüssel werden sofort widerrufen')
    expect(wrapper.get('[data-testid="user-deletion-confirm"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-testid="user-deletion-successor"]').setValue('ops')
    await wrapper.get('[data-testid="user-deletion-confirm"]').trigger('click')
    await flushPromises()
    expect(deleteUser).toHaveBeenCalledWith('viewer', { revision: 'rev-1', successor_username: 'ops' })
    wrapper.unmount()
  })
})
